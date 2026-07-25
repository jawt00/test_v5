#!/usr/bin/env python3
"""rule_confidence 스키마·값 spot-check (list2/list3 TEST DB)."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import get_profile

TABLE_SIM = "simulation_predictions_change_point"
OVERLAP_PREFIX = "bpbbbbpp"


def _check_db(profile_name: str, prefix: str | None) -> int:
    profile = get_profile(profile_name)
    db = profile.predictions_db
    if not db.is_file():
        print(f"[{profile_name}] DB 없음: {db}")
        return 1

    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        cols = {
            row[1]
            for row in conn.execute(
                f"PRAGMA table_info({TABLE_SIM})"
            ).fetchall()
        }
        required = {
            "final_rule",
            "rule_confidence",
            "conf_source",
            "agree_count",
            "confidence",
            "legacy_confidence",
        }
        missing = required - cols
        if missing:
            print(f"[{profile_name}] missing columns: {sorted(missing)}")
            return 1

        q = f"""
            SELECT prefix, predicted_value, final_rule, rule_confidence,
                   conf_source, agree_count, legacy_confidence, confidence
            FROM {TABLE_SIM}
            WHERE prefix = ?
        """
        pfx = prefix or OVERLAP_PREFIX
        row = conn.execute(q, (pfx,)).fetchone()
        if row is None:
            print(f"[{profile_name}] prefix `{pfx}` not found")
            return 1

        rc = row["rule_confidence"]
        conf = row["confidence"]
        if rc != conf and not (rc is None and conf is None):
            print(
                f"[{profile_name}] confidence != rule_confidence "
                f"({conf} vs {rc}) for {pfx}"
            )
            return 1

        mismatch = conn.execute(
            f"""
            SELECT COUNT(*) FROM {TABLE_SIM}
            WHERE (confidence IS NOT rule_confidence)
               OR (confidence IS NULL AND rule_confidence IS NOT NULL)
               OR (confidence IS NOT NULL AND rule_confidence IS NULL)
            """
        ).fetchone()[0]
        if mismatch:
            print(f"[{profile_name}] {mismatch} rows with confidence != rule_confidence")
            return 1

        print(
            f"[{profile_name}] OK prefix={pfx} "
            f"pred={row['predicted_value']} rule={row['final_rule']} "
            f"rule_conf={rc} source={row['conf_source']} "
            f"agree={row['agree_count']}"
        )
        return 0
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", choices=["list2", "list3", "both"], default="both")
    parser.add_argument("--prefix", default=OVERLAP_PREFIX)
    args = parser.parse_args()

    profiles = ["list2", "list3"] if args.profile == "both" else [args.profile]
    code = 0
    for p in profiles:
        code |= _check_db(p, args.prefix)
    raise SystemExit(code)


if __name__ == "__main__":
    main()
