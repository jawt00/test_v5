"""pattern_list2 최종 예측 규칙 엔진 (R1–R5 선택 적용)."""

from __future__ import annotations

from collections import Counter
from typing import Callable

import pandas as pd

RULE_ORDER = ("R1", "R2", "R3", "R5")
DEFAULT_ENABLED_RULES = frozenset({"R1", "R2", "R3", "R5"})

FINAL_RULE_INFO: dict[str, dict[str, str]] = {
    "R1": {
        "label": "R1 · 3-way 일치",
        "short": "3-way",
        "description": "grid10 = grid12 = ngram12 일치 → 해당 예측값 사용",
    },
    "R2": {
        "label": "R2 · ngram 불일치",
        "short": "sim≠ngram",
        "description": "sim ≠ ngram12 → sim 예측값 사용",
    },
    "R3": {
        "label": "R3 · sim=ngram=grid10",
        "short": "sim=ngram=grid10",
        "description": "sim = ngram12 = grid10 → ngram12 예측값 사용",
    },
    "R5": {
        "label": "R5 · sim+ngram / 3-way 다수",
        "short": "R5",
        "description": "sim=ngram, grid10 불일치 → sim · grid10/12/ngram12 2-of-3 다수 → 다수값",
    },
    "R4": {
        "label": "R4 · pass",
        "short": "pass",
        "description": "선택된 규칙에 해당 없음 → 예측 없음 (pass)",
    },
    "unknown": {
        "label": "미분류",
        "short": "-",
        "description": "pattern_list2에 없는 prefix (규칙 역산 불가)",
    },
}


def _fmt_pred_upper(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value == "":
        return None
    s = str(value).strip().upper()
    return s[0] if s else None


def _three_way_majority(grid10, grid12, ngram12) -> str | None:
    """grid10/grid12/ngram12 중 2-of-3 다수값 (동률이면 None)."""
    vals = [_fmt_pred_upper(v) for v in (grid10, grid12, ngram12)]
    vals = [v for v in vals if v]
    if len(vals) < 2:
        return None
    best, count = Counter(vals).most_common(1)[0]
    return best if count >= 2 else None


def normalize_enabled_rules(enabled_rules) -> frozenset[str]:
    if enabled_rules is None:
        return DEFAULT_ENABLED_RULES
    out = {str(r).strip().upper() for r in enabled_rules if str(r).strip()}
    valid = out & set(RULE_ORDER)
    return frozenset(valid) if valid else DEFAULT_ENABLED_RULES


def format_rule_version(enabled_rules: frozenset[str]) -> str:
    ordered = [r for r in RULE_ORDER if r in enabled_rules]
    if ordered == ["R1", "R2", "R3"]:
        return "final_pred_v2"
    if ordered == ["R1", "R2", "R3", "R5"]:
        return "final_pred_v3"
    return "final_pred:" + "+".join(ordered)


def parse_rule_version(rule_version: str | None) -> frozenset[str]:
    if not rule_version:
        return DEFAULT_ENABLED_RULES
    rv = str(rule_version).strip()
    if rv == "final_pred_v2":
        return frozenset({"R1", "R2", "R3"})
    if rv == "final_pred_v3":
        return frozenset({"R1", "R2", "R3", "R5"})
    if rv.startswith("final_pred:"):
        return normalize_enabled_rules(rv.split(":", 1)[1].split("+"))
    return DEFAULT_ENABLED_RULES


def rule_label(rule_id: str) -> str:
    return FINAL_RULE_INFO.get(rule_id, FINAL_RULE_INFO["unknown"])["label"]


def rule_description(rule_id: str) -> str:
    return FINAL_RULE_INFO.get(rule_id, FINAL_RULE_INFO["unknown"])["description"]


def _resolve_r5(row: pd.Series) -> tuple[bool, str | None]:
    """R5a → R5b 순으로 매칭. (matched, prediction)."""
    agree_sim_ngram = row.get("agree_sim_ngram")
    agree_sim_grid10 = row.get("agree_sim_grid10")
    sim = _fmt_pred_upper(row.get("sim_pred"))
    grid10 = _fmt_pred_upper(row.get("grid10_pred"))
    grid12 = _fmt_pred_upper(row.get("grid12_pred"))
    ngram12 = _fmt_pred_upper(row.get("ngram12_pred"))

    if agree_sim_ngram is True and agree_sim_grid10 is False:
        return True, sim

    maj = _three_way_majority(grid10, grid12, ngram12)
    if maj:
        return True, maj

    return False, None


def build_rule_engine(
    enabled_rules=None,
) -> tuple[Callable[[pd.Series], str], Callable[[pd.Series], str], str]:
    """
    선택된 규칙만 적용하는 (compute_final_prediction, classify_final_rule, rule_version) 반환.
    매칭되는 규칙이 없거나 비활성 → pass (R4).
    """
    enabled = normalize_enabled_rules(enabled_rules)
    rule_version = format_rule_version(enabled)

    def classify_final_rule(row: pd.Series) -> str:
        if "R1" in enabled and row.get("agree_new_three") is True:
            return "R1"
        if "R2" in enabled and row.get("agree_sim_ngram") is False:
            return "R2"
        if (
            "R3" in enabled
            and row.get("agree_sim_ngram") is True
            and row.get("agree_sim_grid10") is True
        ):
            return "R3"
        if "R5" in enabled:
            matched, _ = _resolve_r5(row)
            if matched:
                return "R5"
        return "R4"

    def compute_final_prediction(row: pd.Series) -> str:
        agree_new_three = row.get("agree_new_three")
        agree_sim_ngram = row.get("agree_sim_ngram")
        agree_sim_grid10 = row.get("agree_sim_grid10")

        ngram12 = _fmt_pred_upper(row.get("ngram12_pred"))
        grid10 = _fmt_pred_upper(row.get("grid10_pred"))
        grid12 = _fmt_pred_upper(row.get("grid12_pred"))
        sim = _fmt_pred_upper(row.get("sim_pred"))

        if "R1" in enabled and agree_new_three is True:
            final = ngram12 or grid10 or grid12
            return final if final else "pass"

        if "R2" in enabled and agree_sim_ngram is False:
            return sim if sim else "pass"

        if (
            "R3" in enabled
            and agree_sim_ngram is True
            and agree_sim_grid10 is True
        ):
            return ngram12 if ngram12 else "pass"

        if "R5" in enabled:
            matched, pred = _resolve_r5(row)
            if matched:
                return pred if pred else "pass"

        return "pass"

    return compute_final_prediction, classify_final_rule, rule_version


compute_final_prediction, classify_final_rule, DEFAULT_RULE_VERSION = build_rule_engine()
