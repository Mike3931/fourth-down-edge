"""Inventory logical-identity duplicates in a populated database.

The migration refuses to add a unique constraint while duplicates exist,
and refusing is the easy part. This is the part that makes the refusal
actionable: for every colliding identity it reports which rows collide,
what they disagree about, what depends on them, and whether anything
downstream has already treated one of them as true.

It reads only. It never deletes, consolidates, or picks a winner — those
require a governed resolution manifest, because choosing between two rows
that disagree about a settled fact is a decision about evidence somebody
wrote, not a migration detail.

Run:  python scripts/identity_duplicate_inventory.py [--database-url URL]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import bindparam, create_engine, text
from sqlalchemy.engine import Engine

ROOT = Path(__file__).resolve().parents[1]
ARTIFACT = ROOT.parents[1] / "reports" / "integrity" / "identity-duplicate-inventory.json"

# Row-level detail for the tables the constraint covers, and the column
# whose value distinguishes one row from another for a reader.
TABLES: tuple[tuple[str, str], ...] = (
    ("consensus_snapshots", "consensus_snapshot"),
    ("forward_ledger", "research_evaluation"),
    ("availability_assessments", "availability_assessment"),
    ("manual_book_price_entries", "price_observation"),
)


def _identity_values(table: str, row: Any) -> tuple[dict, dict]:
    """Reuse the migration's field extraction, so the inventory and the
    migration cannot disagree about what an identity is."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "identity_migration",
        ROOT / "migrations" / "versions" / "b7e2f9c41a68_domain_identity_hashes.py",
    )
    assert spec and spec.loader
    if not hasattr(_identity_values, "_module"):
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _identity_values._module = module  # type: ignore[attr-defined]
    module = _identity_values._module  # type: ignore[attr-defined]
    logical, content = module._row_values(table, row)
    return module._ensure_aware(logical), module._ensure_aware(content)


def _downstream(engine: Engine, table: str, row_ids: list[Any]) -> dict[str, Any]:
    """What refers to these rows, and whether any has been treated as true.

    A duplicate nothing points at is a tidiness problem. A duplicate that a
    settlement or a CLV figure was computed from is a correctness problem,
    and the difference decides whether consolidation is even conceivable.
    """
    out: dict[str, Any] = {"references": {}, "used_downstream": False}
    with engine.connect() as c:
        if table == "consensus_snapshots":
            hits = list(c.execute(text(
                "SELECT id FROM closing_captures WHERE consensus_snapshot_id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)), {"ids": row_ids or [None]}))
            out["references"]["closing_captures"] = [r[0] for r in hits]
            out["used_downstream"] = bool(hits)
        elif table == "forward_ledger":
            settled = list(c.execute(text(
                "SELECT id, result, settled_at, clv_line, clv_probability "
                "FROM forward_ledger WHERE id IN :ids"
            ).bindparams(bindparam("ids", expanding=True)), {"ids": row_ids or [None]}))
            out["references"]["settlement_or_clv"] = [
                {"id": r[0], "result": r[1],
                 "settled_at": str(r[2]) if r[2] else None,
                 "clv_line": r[3], "clv_probability": r[4]}
                for r in settled
            ]
            out["used_downstream"] = any(
                r[1] is not None or r[2] is not None
                or r[3] is not None or r[4] is not None
                for r in settled
            )
    return out


def inventory(engine: Engine) -> dict[str, Any]:
    report: dict[str, Any] = {
        "artifact": "identity-duplicate-inventory",
        "artifact_schema_version": "identity-duplicate-inventory-v1",
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "database": str(engine.url).split("://")[0],
        "tables": {},
        "conflicting_total": 0,
        "exact_duplicate_total": 0,
        "automatic_resolution_prohibited": True,
        "note": (
            "Read-only. No row is deleted, consolidated, or chosen. "
            "Resolution requires a governed manifest."
        ),
    }
    from fde_api.forward.domain_identity import (
        BY_ENTITY,
        CONTENT_HASH_VERSION,
        LOGICAL_IDENTITY_VERSION,
    )

    for table, entity in TABLES:
        identity = BY_ENTITY[entity]
        with engine.connect() as c:
            rows = list(c.execute(text(f"SELECT * FROM {table}")))

        groups: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            logical, content = _identity_values(table, row)
            lid = identity.logical_hash(logical)
            groups.setdefault(lid, []).append({
                "row_id": row._mapping["id"],
                "content_hash": identity.content_hash(content),
                "logical_fields": {k: str(v) for k, v in sorted(logical.items())},
                "content": {k: (str(v) if v is not None else None)
                            for k, v in sorted(content.items())},
                "created_at": str(row._mapping.get("created_at", "")) or None,
                "cohort": str(row._mapping.get("data_mode")
                              or row._mapping.get("cohort") or ""),
                "policy_version": row._mapping.get("policy_version"),
                "model_version": row._mapping.get("model_version"),
                "code_commit": row._mapping.get("code_commit"),
            })

        conflicting: list[dict[str, Any]] = []
        exact: list[dict[str, Any]] = []
        for lid, members in sorted(groups.items()):
            if len(members) == 1:
                continue
            hashes = {m["content_hash"] for m in members}
            record = {
                "table": table,
                "entity": entity,
                "logical_identity_version": LOGICAL_IDENTITY_VERSION,
                "logical_identity_hash": lid,
                "content_hash_version": CONTENT_HASH_VERSION,
                "row_ids": [m["row_id"] for m in members],
                "content_hashes": sorted(hashes),
                "canonical_identity_fields": members[0]["logical_fields"],
                "members": members,
                "differing_content_fields": sorted({
                    k for k in members[0]["content"]
                    if len({json.dumps(m["content"].get(k), default=str)
                            for m in members}) > 1
                }),
                "downstream": _downstream(engine, table, [m["row_id"] for m in members]),
                "automatic_resolution_prohibited": True,
            }
            if len(hashes) == 1:
                record["classification"] = "EXACT_DUPLICATE"
                record["recommended_disposition"] = "CANONICALIZE_EXACT_DUPLICATES"
                exact.append(record)
            else:
                record["classification"] = "CONFLICTING_DUPLICATE"
                record["recommended_disposition"] = (
                    "MANUAL_REVIEW_UNRESOLVED - the rows disagree about content. "
                    "Choosing between them discards evidence; use "
                    "KEEP_DISTINCT_AFTER_IDENTITY_CORRECTION only if the identity "
                    "definition genuinely failed to distinguish legitimate versions."
                )
                conflicting.append(record)

        report["tables"][table] = {
            "entity": entity,
            "rows": len(rows),
            "distinct_logical_identities": len(groups),
            "UNIQUE": sum(1 for m in groups.values() if len(m) == 1),
            "EXACT_DUPLICATE": len(exact),
            "CONFLICTING_DUPLICATE": len(conflicting),
            "conflicting": conflicting,
            "exact": exact,
        }
        report["conflicting_total"] += len(conflicting)
        report["exact_duplicate_total"] += len(exact)

    body = json.dumps(
        {k: v for k, v in report.items() if k not in ("generated_at_utc", "sha256")},
        sort_keys=True, separators=(",", ":"), default=str,
    )
    report["sha256"] = hashlib.sha256(body.encode("utf-8")).hexdigest()
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", default=None)
    parser.add_argument("--out", default=str(ARTIFACT))
    args = parser.parse_args()

    from fde_api.config import settings

    url = args.database_url or settings.database_url
    engine = create_engine(url, future=True)
    report = inventory(engine)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
                   encoding="utf-8", newline="\n")
    print(f"wrote {out}")
    print(f"  conflicting duplicates : {report['conflicting_total']}")
    print(f"  exact duplicates       : {report['exact_duplicate_total']}")
    print(f"  sha256                 : {report['sha256']}")
    for table, data in report["tables"].items():
        if data["CONFLICTING_DUPLICATE"] or data["EXACT_DUPLICATE"]:
            print(f"  {table}: {data['CONFLICTING_DUPLICATE']} conflicting, "
                  f"{data['EXACT_DUPLICATE']} exact")
    return 0


if __name__ == "__main__":
    sys.exit(main())
