# Governed dependency-advisory exceptions

Every entry here is a written applicability decision, not a suppression.
An advisory may only be added after the analysis below is completed and
recorded. The CI dependency gate lists these IDs explicitly so it stays red
for anything new.

---

## PYSEC-2026-113 — PyArrow

**Decision:**

```
NOT APPLICABLE — affected pre-buffering API is not exposed or invoked through this Python application.
```

| field | value |
| --- | --- |
| Resolved version | `pyarrow 21.0.0` (declared `pyarrow~=21.0.0`) |
| Fixed in | 23.0.1 |
| Reviewed | 2026-08-03 |
| Reviewer / tool | model-integrity gate on `fix/phase-2-replay-integrity`; evidence gathered with `git grep` over `apps/api/src` and `pip-audit` against the exported lock |
| Expires / re-evaluate | **2027-02-01**, or immediately on any of the triggers below, whichever comes first |
| Planned safe-upgrade target | `pyarrow >= 23.0.1`, in a dependency-policy revision that deliberately re-freezes `ftp-2026-v1` |

### Evidence

**Does any C++ extension directly invoke `RecordBatchFileReader::PreBufferMetadata`?**
No. There is no direct `pyarrow` call anywhere in `apps/api/src` — a grep
for `pyarrow` returns nothing. (A grep for `pa.` matches only a local list
named `pa` for *points-against* in `models_ml/baselines.py`, which is not
the Arrow module.) The project ships no native extension of its own.

**Are Arrow IPC files accepted from untrusted users?**
No. The advisory concerns the Arrow **IPC** format (`.arrow` / Feather).
This codebase reads **Parquet only**, via Polars `pl.scan_parquet`, in
`canonical/load_pbp.py` and `canonical/load_rosters.py`. Those files come
from the immutable raw-artifact store, sourced from nflverse under a
pinned, content-hashed manifest — not from users.

**Does any native component enable IPC pre-buffering?**
No. Nothing in the codebase configures pre-buffering, and the affected
reader is never constructed.

**Is there any upload path at all?**
No. Confirmed independently during the Starlette audit: no `UploadFile`,
no `File(...)`, no `Form(...)`, no multipart route, and `python-multipart`
is not an installed dependency. No endpoint accepts a file of any format.

### Mandatory re-evaluation triggers

This exception is void, and must be re-assessed before the next release, if
any of the following is introduced:

* native Arrow code, or any compiled extension linking Arrow;
* Arrow IPC or Feather reading anywhere in the codebase;
* a file-upload endpoint, or any path accepting user-supplied binary;
* a direct `pyarrow` import or API call in `apps/api/src`;
* enabling IPC pre-buffering in any component.

### Scope limit

This exception covers **PYSEC-2026-113 only**. It confers nothing on any
other PyArrow advisory: a new PyArrow finding fails the gate and requires
its own written decision.

### Why the lock was not changed

`uv.lock` is hashed into the frozen forward-test policy `ftp-2026-v1` via
`policy.dependency_lock_hash` and into every model-registry entry. Bumping
PyArrow to clear a scanner finding that does not apply would alter that
hash for no security benefit, which is an ungoverned change to a frozen
artifact. The upgrade to 23.0.1 or later is deferred to a dependency-policy
revision that re-freezes deliberately.

---

## PYSEC-2026-1845 — pytest

**Decision:**

```
NOT APPLICABLE — development-only dependency; not present in any runtime path.
```

| field | value |
| --- | --- |
| Resolved version | `pytest 8.4.2` |
| Fixed in | 9.0.3 |
| Reviewed | 2026-08-03 |
| Re-evaluate | when the test toolchain is next upgraded |

`pytest` is a test runner. It is not imported by `fde_api` at runtime, is
not deployed, and cannot be reached by any request. It is carried in the
lock because the lock covers the development environment.

---

## Cleared — no longer exceptions

| ID | Package | Cleared by |
| --- | --- | --- |
| PYSEC-2026-161 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-248 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-249 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-1942 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-2280 | starlette | 0.48.0 → 1.3.1 |
| PYSEC-2026-2281 | starlette | 0.48.0 → 1.3.1 |

Upgraded on the Phase 3 branch (`9ca85e6`) together with
`fastapi 0.116.2 → 0.141.1`. **Not backported to the Phase 2 lineage**:
the Phase 2 test environment resolves and passes on its own lock, so the
dependency change is not required there, and mixing it into the analytical
patch would violate the separation this gate requires.

---

## Phase 2 deployment status

```
The Phase 2 patch branch is retained for reproducible analytical research and is not approved for network deployment. API security dependency remediation is maintained on the active application lineage.
```

**Consequences of this classification, applied:**

* The Starlette/FastAPI remediation is **not** mixed into the analytical
  patch. `fix/phase-2-replay-integrity` keeps `fastapi 0.116.2` /
  `starlette 0.48.0` from its own lock, and its suite passes on them.
* The six Starlette advisories therefore **remain present** on this branch.
  That is a consequence of the archival classification, not an oversight,
  and is recorded rather than suppressed — they are not added to the
  exception list here, because the honest disposition is "this lineage is
  not exposed", not "these do not apply".
* The separate Phase 3 remediation record stands: commit `9ca85e6` on
  `feature/forward-data-capture`, `starlette 0.48.0 → 1.3.1` with
  `fastapi 0.116.2 → 0.141.1`, all six cleared.

**Non-deployment warning.** Do not expose this branch on a network. It
exists to reproduce the corrected Phase 2 analytical results. If it is ever
to be served, the deployable path is a new branch from
`phase-2-research-engine-v1.0.1` with the dependency remediation applied
separately and its own security patch tag — not an in-place upgrade here.
