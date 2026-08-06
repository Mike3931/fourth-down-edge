# Domain identity — logical slot vs. content

Four records now carry two hashes instead of relying on a service-level
pre-check.

```text
logical_identity_version = domain-logical-identity-v1
content_hash_version     = domain-content-v1
digest                   = sha256
float precision          = 6 decimal places
```

## Why two hashes

Two different questions were being answered by one thing:

| | question | answers |
|---|---|---|
| **logical identity** | *which* record is this? | the slot an attempt aims at |
| **content hash** | *what* does it say? | what was observed, computed or decided |

Keeping them apart is what makes a retry distinguishable from a
contradiction:

```text
same slot + same content      -> EXISTING_IDENTICAL   (no new row)
same slot + different content -> CONFLICT             (original stands)
different slot                -> CREATED              (legitimate version)
```

The previous design was `SELECT → not found → INSERT`. That is race-prone
by construction — two callers both see nothing and both insert — and it
passed for months because SQLite serialises writers at the file level while
PostgreSQL does not. **The unique index on the logical identity is now the
authority.** The pre-check remains only because the overwhelmingly common
case is a retry and a failed insert per retry is wasteful.

## Getting the breadth right

The whole design lives in how broad each logical identity is:

- **too broad** — every conflicting payload becomes a "new version", and
  contradictions are silently accepted as history
- **too narrow** — a legitimately revised observation collides with the one
  it revises, and real new information is rejected as a conflict

Each entity's choice is argued below.

## Canonicalisation rules

Shared by both hashes, in `fde_api.forward.domain_identity`:

- versioned canonical JSON, sorted keys, `(",", ":")` separators
- explicit **allowlist** per entity — a new column cannot silently join a
  hash, which would change every stored value without anyone deciding it
  should
- timestamps normalised to UTC ISO; a **naive timestamp is refused**,
  because identity depends on an unambiguous instant
- floats rounded to 6 places, so arithmetic noise is not a different reading
- sets sorted (order is not semantic); lists preserved (order is)
- enums render as their stable code
- `null` distinguished from *missing*
- database keys, scheduler run ids and rendered prose excluded

---

## consensus_snapshot

**Logical identity** — `canonical_game_id`, `market`, `cutoff`, `cohort`,
`method_version`

**Content** — `median_line`, `home_price_american`, `away_price_american`,
`over_price_american`, `under_price_american`, `no_vig_home_prob`,
`no_vig_over_prob`, `eligible_books`, `quote_lineage`, `line_dispersion`,
`price_dispersion`, `provider_mode`

| behaviour | outcome |
|---|---|
| recompute the same cutoff | `EXISTING_IDENTICAL` |
| later cutoff | `CREATED` — new slot |
| different `method_version` | `CREATED` — new slot |
| different answer, same slot | `CONFLICT`, **manual review required** |

A consensus is a calculation over the quotes visible at a cutoff, so
recomputing it must reproduce it. A different answer at the same slot means
the inputs changed after the fact, and downstream consumers read that value
as truth.

- **Constraint** `uq_consensus_snapshots_logical_identity`
- **Backfill** cutoff from `observed_at`, cohort from `data_mode`
- **Lineage** referenced by prediction vintages via canonical identity

---

## research_evaluation

**Logical identity** — `canonical_game_id`, `prediction_identity`,
`price_identity`, `evaluation_type`, `cohort`, `policy_version`,
`model_version`, `decision_context_hash`

**Content** — `status`, `model_probability`, `conservative_probability`,
`break_even_probability`, `expected_value`, `decision_reason_codes`,
`suppressed`, `data_completeness`, `execution_eligible`

The **decision-context hash is in the logical identity deliberately**. A
health remediation legitimately changes the context, and the evaluation made
afterwards is a genuinely new immutable record — not a correction of the
suppressed one, which stays exactly as it was. Without the context in the
identity, remediation would look like a conflict; with it, the same context
producing a different analytical answer *is* a conflict, which is the case
that matters.

| behaviour | outcome |
|---|---|
| same context, same output | `EXISTING_IDENTICAL` |
| remediated context | `CREATED` — new immutable evaluation |
| same context, different output | `CONFLICT`, **manual review required** |

Rendered explanation text determines neither identity nor content equality.

- **Constraint** `uq_forward_ledger_logical_identity`
- **Backfill** `decision_context_hash` as `legacy:<as_of_at>` for
  pre-migration rows, which carry no stored context; the cutoff is the one
  field distinguishing successive evaluations, so backfilled identities stay
  distinct without inventing a context that was never computed
- **Lineage** the forward-performance row is this row

---

## availability_assessment

**Logical identity** — `canonical_game_id`, `player_id`, `cutoff`,
`cohort`, `method_version`

**Content** — `state`, `active_prob_low`, `active_prob_high`,
`snap_share_low`, `snap_share_high`, `confidence_tier`, `is_starting_qb`,
`observation_lineage`, `missing_data`

| behaviour | outcome |
|---|---|
| reassess the same cutoff | `EXISTING_IDENTICAL` |
| later cutoff | `CREATED` — how an evolving injury picture is represented |
| different `method_version` | `CREATED` |
| different assessment, same slot | `CONFLICT`, review *not* required |

Review is not required here: the newer observation is visible in the
lineage and a later cutoff supersedes it. This is the one entity where a
conflict is informative rather than blocking.

- **Constraint** `uq_availability_assessments_logical_identity`
- **Backfill** cutoff from `as_of_at`, method `availability-v0`
- **Lineage** consumed by prediction vintages

---

## price_observation

**Logical identity** — `canonical_game_id`, `market`, `selection`,
`observed_at`, `cohort`, `provider_mode`, `source_identity`

**Content** — `line`, `american`, `decimal_odds`,
`break_even_probability`, `confirmed`, `source`, `policy_version`,
`code_commit`

**Line and price are deliberately NOT in the logical identity.** The same
submission reporting a different number is precisely the case that must
surface: a person re-entering one observation with a different price has
either mistyped or is looking at a changed market, and storing both as
equally valid observations of one moment hides that.

`source_identity` is built from the submitter and the correction target,
since there is no provider request id for a manually entered price.

| behaviour | outcome |
|---|---|
| exact retry | `EXISTING_IDENTICAL` |
| same submission, different price | `CONFLICT`, **manual review required** |
| new observation time | `CREATED` |
| different submitter | `CREATED` |
| correction | `CREATED` under a new governed identity; supersedes, never edits |

- **Constraint** `uq_manual_book_price_entries_logical_identity`
- **Backfill** `source_identity` from `user_id`
- **Lineage** referenced by evaluations via market/selection/price identity

---

## Migration

Revision **`b7e2f9c41a68`**, on `a4d81c6b0e57`.

Order is deliberate: add nullable columns → backfill deterministically →
**inventory duplicates** → fail loudly on any duplicate → only then create
the unique index. Creating the index first would fail with a bare
constraint violation naming nothing, leaving an operator to work out which
rows collided and why.

The inventory classifies each logical identity as `UNIQUE`,
`EXACT_DUPLICATE` or `CONFLICTING_DUPLICATE` and writes
`reports/integrity/identity-migration-audit.json`. **Neither duplicate class
is resolved automatically.** Choosing between conflicting rows discards
evidence somebody wrote; consolidating exact duplicates remaps downstream
lineage, which is a governed operation and not a side effect of adding a
constraint.

Backfill is deterministic: no random values, no current timestamp in hash
material, no dependence on insertion order. Re-running produces identical
hashes.

**Downgrade** is structurally supported and semantically lossy. Dropping the
columns discards every stored hash; a re-upgrade recomputes from *current*
content, which is correct for unchanged rows and silently wrong for any row
edited in between. These tables are append-only, so that should not arise —
recorded because "should not arise" is not "cannot".
