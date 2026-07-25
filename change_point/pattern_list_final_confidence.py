"""최종 예측 rule_confidence · agree 메트릭 (list2/list3 공용)."""

from __future__ import annotations

from typing import Literal

import pandas as pd

ProfileName = Literal["list2", "list3"]

EXTRA_SIM_COLUMNS: dict[str, str] = {
    "final_rule": "TEXT",
    "rule_version": "TEXT",
    "agree_count": "INTEGER",
    "agree_count_new_three": "INTEGER",
    "rule_confidence": "REAL",
    "conf_source": "TEXT",
    "min_agree_conf": "REAL",
    "mean_agree_conf": "REAL",
    "legacy_confidence": "REAL",
}

SIM_INSERT_EXTRA_COLUMNS = list(EXTRA_SIM_COLUMNS.keys())


def migrate_sim_extra_columns(conn, table: str) -> None:
    cur = conn.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cur.fetchall()}
    for col, ddl_type in EXTRA_SIM_COLUMNS.items():
        if col not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl_type}")


def _norm_pred(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    return s if s in ("b", "p") else None


def _norm_conf(value) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _source_defs(profile: ProfileName) -> tuple[tuple[str, str, str], tuple[str, str, str]]:
    """(all_sources, new_three_sources) as (pred_col, conf_col, name) tuples.

    row는 list2 컬럼명 (list3는 cmp_row_for_rules 적용 후) — profile 분기 없음.
    """
    _ = profile
    all_src = (
        ("sim_pred", "sim_conf", "sim"),
        ("grid10_pred", "grid10_conf", "grid10"),
        ("grid12_pred", "grid12_conf", "grid12"),
        ("ngram12_pred", "ngram12_conf", "ngram"),
    )
    new_three = all_src[1:]
    return all_src, new_three


def _sources_matching_final(
    row: pd.Series,
    final_pred: str,
    sources: tuple[tuple[str, str, str], ...],
) -> list[tuple[str, float]]:
    fp = final_pred.lower()
    out: list[tuple[str, float]] = []
    for pred_col, conf_col, _name in sources:
        p = _norm_pred(row.get(pred_col))
        c = _norm_conf(row.get(conf_col))
        if p == fp and c is not None:
            out.append((pred_col, c))
    return out


def _agg_min_mean(matches: list[tuple[str, float]]) -> tuple[float | None, float | None]:
    if not matches:
        return None, None
    vals = [c for _, c in matches]
    return min(vals), sum(vals) / len(vals)


def _is_r5a(row: pd.Series) -> bool:
    return (
        row.get("agree_sim_ngram") is True
        and row.get("agree_sim_grid10") is False
    )


def compute_final_confidence(
    row: pd.Series,
    *,
    final_rule: str,
    final_pred: str,
    profile: ProfileName = "list2",
) -> dict:
    """
    규칙별 rule_confidence (conservative min) 및 agree 카운트.

    row: list2 컬럼명 (list3는 cmp_row_for_rules 적용 후).
    """
    all_src, new_three = _source_defs(profile)
    fp = final_pred.lower() if final_pred and final_pred != "pass" else ""

    agree_all = _sources_matching_final(row, fp, all_src) if fp else []
    agree_new = _sources_matching_final(row, fp, new_three) if fp else []
    agree_count = len(agree_all)
    agree_count_new_three = len(agree_new)

    if final_rule == "R4" or not fp:
        return {
            "final_rule": final_rule,
            "agree_count": 0,
            "agree_count_new_three": 0,
            "rule_confidence": None,
            "conf_source": "pass",
            "min_agree_conf": None,
            "mean_agree_conf": None,
        }

    rule_confidence: float | None = None
    conf_source = "unknown"
    pick: list[tuple[str, float]] = []

    if final_rule == "R1":
        pick = agree_new
        conf_source = "consensus_min"
    elif final_rule == "R2":
        sim_c = _norm_conf(row.get("sim_conf"))
        if sim_c is not None and _norm_pred(row.get("sim_pred")) == fp:
            pick = [("sim_conf", sim_c)]
        conf_source = "sim"
    elif final_rule == "R3":
        pick = []
        for pred_col, conf_col, _ in (
            ("sim_pred", "sim_conf", "sim"),
            ("ngram12_pred", "ngram12_conf", "ngram"),
            ("grid10_pred", "grid10_conf", "grid10"),
        ):
            p = _norm_pred(row.get(pred_col))
            c = _norm_conf(row.get(conf_col))
            if p == fp and c is not None:
                pick.append((conf_col, c))
        conf_source = "consensus_min"
    elif final_rule == "R5":
        if _is_r5a(row):
            sim_c = _norm_conf(row.get("sim_conf"))
            if sim_c is not None:
                pick = [("sim_conf", sim_c)]
            conf_source = "sim"
        else:
            pick = agree_new
            conf_source = "majority_min"
    else:
        pick = agree_all
        conf_source = "unknown"

    min_c, mean_c = _agg_min_mean(pick)
    rule_confidence = min_c

    return {
        "final_rule": final_rule,
        "agree_count": agree_count,
        "agree_count_new_three": agree_count_new_three,
        "rule_confidence": rule_confidence,
        "conf_source": conf_source,
        "min_agree_conf": min_c,
        "mean_agree_conf": mean_c,
    }


def empty_confidence_row(final_rule: str = "R4") -> dict:
    return {
        "final_rule": final_rule,
        "agree_count": 0,
        "agree_count_new_three": 0,
        "rule_confidence": None,
        "conf_source": "pass",
        "min_agree_conf": None,
        "mean_agree_conf": None,
    }
