"""신규 3-way 일치 건을 prefix·prediction JSON으로 export."""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import types
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import PROJECT_ROOT, get_profile

OUTPUT_DIR = PROJECT_ROOT / "pattern"


def _load_compare_module():
    sys.modules["streamlit"] = types.ModuleType("streamlit")
    spec = importlib.util.spec_from_file_location(
        "cmp", PROJECT_ROOT / "change_point" / "pattern_predictions_compare_app.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_rules(profile_name: str) -> list[dict]:
    profile = get_profile(profile_name)
    mod = _load_compare_module()
    df, _ = mod.build_comparison_df(profile_name)
    matched = df[df["agree_new_three"] == True].reset_index(drop=True)  # noqa: E712

    rules = []
    seen: set[tuple[str, str]] = set()
    for _, r in matched.iterrows():
        prefix = (r["ws9_core"] or "").strip().lower()
        pred = (r["grid10_pred"] or "b").upper()
        if len(pred) > 1:
            pred = pred[0]
        if pred not in ("B", "P"):
            pred = "B"
        key = (prefix, pred)
        if not prefix or key in seen:
            continue
        seen.add(key)
        rules.append({"prefix": prefix, "prediction": pred})
    return rules


def _pad_prefix_ws10(ws9_prefix: str) -> str:
    p = (ws9_prefix or "").strip().lower()
    if not p:
        return ""
    return p[0] + p


def build_rules_ws10(rules_ws9: list[dict]) -> list[dict]:
    return [
        {"prefix": _pad_prefix_ws10(r["prefix"]), "prediction": r["prediction"]}
        for r in rules_ws9
        if r.get("prefix")
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Export new 3-way agree rules JSON")
    parser.add_argument(
        "--profile",
        default="list1",
        choices=["list1", "list2"],
        help="pattern list profile (default: list1)",
    )
    args = parser.parse_args()
    profile = get_profile(args.profile)

    rules_ws9 = build_rules(args.profile)
    rules_ws10 = build_rules_ws10(rules_ws9)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    prefix = profile.json_name_prefix
    path_ws9 = OUTPUT_DIR / f"{prefix}_ws9_{stamp}.json"
    path_ws10 = OUTPUT_DIR / f"{prefix}_ws10_{stamp}.json"

    path_ws9.write_text(
        json.dumps(rules_ws9, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    path_ws10.write_text(
        json.dumps(rules_ws10, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Profile: {profile.name}")
    print(f"written {path_ws9} ({len(rules_ws9)} rules)")
    print(f"written {path_ws10} ({len(rules_ws10)} rules)")


if __name__ == "__main__":
    main()
