"""윈도우 9 라이브게임 저장 결과 분석 (Q1: 적중률, Q3: prefix별)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from pattern_predictions_compare_app import build_comparison_df
from pattern_list2_final_rules import (
    FINAL_RULE_INFO,
    build_rule_engine,
    classify_final_rule,
    parse_rule_version,
    rule_description,
    rule_label,
)

DB_PATH = Path(__file__).resolve().parent / "pattern_list2.db"
LIVE_STEP_RESULTS_TABLE = "live_step_results"
PREDICTIONS_TABLE = "simulation_predictions_change_point"
METHOD = "빈도 기반"
THRESHOLD = 0
WINDOW_SIZE = 9

VOLUME_BUCKETS = (
    ("n=1", 1, 1),
    ("n=2~4", 2, 4),
    ("n=5~9", 5, 9),
    ("n≥10", 10, None),
)


def get_db_connection():
    conn = sqlite3.connect(DB_PATH, timeout=20.0, check_same_thread=False)
    conn.execute("PRAGMA journal_mode=WAL;")
    return conn


def load_valid_w9_steps(
    conn=None,
    date_from: str | None = None,
    date_to: str | None = None,
) -> pd.DataFrame:
    """유효 예측(window_size=9, skipped=0, is_correct 0/1) 행만 로드."""
    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()
    try:
        q = f"""
            SELECT id, created_at, step, position, anchor, window_size, prefix,
                   predicted, actual, is_correct, confidence, skipped, skip_reason
            FROM {LIVE_STEP_RESULTS_TABLE}
            WHERE window_size = ?
              AND skipped = 0
              AND is_correct IN (0, 1)
              AND LOWER(predicted) IN ('b', 'p')
        """
        params: list = [WINDOW_SIZE]
        if date_from:
            q += " AND created_at >= ?"
            params.append(date_from)
        if date_to:
            q += " AND created_at <= ?"
            params.append(date_to)
        q += " ORDER BY created_at, step, id"
        df = pd.read_sql_query(q, conn, params=params)
        if len(df) > 0:
            df["prefix"] = df["prefix"].astype(str).str.strip().str.lower()
            df["predicted"] = df["predicted"].astype(str).str.strip().str.lower()
            df["actual"] = df["actual"].astype(str).str.strip().str.lower()
        return df
    finally:
        if own_conn:
            conn.close()


def summarize_overall(df: pd.DataFrame) -> dict:
    """Q1: 전체 라이브 적중률 요약."""
    if df is None or len(df) == 0:
        return {
            "total_predictions": 0,
            "total_hits": 0,
            "total_failures": 0,
            "accuracy_pct": 0.0,
            "max_consecutive_failures": 0,
            "verdict": "no_data",
            "verdict_label": "데이터 없음",
        }

    hits = int((df["is_correct"] == 1).sum())
    n = len(df)
    failures = n - hits
    acc = 100.0 * hits / n if n > 0 else 0.0

    max_consec = 0
    cur = 0
    for ok in df["is_correct"].tolist():
        if ok == 0:
            cur += 1
            max_consec = max(max_consec, cur)
        else:
            cur = 0

    if n < 20:
        verdict, label = "insufficient", "판단 보류 (표본 부족)"
    elif acc >= 55.0:
        verdict, label = "valid", "유효 가능"
    elif acc <= 52.0:
        verdict, label = "invalid", "유효하지 않음"
    else:
        verdict, label = "uncertain", "추가 데이터 필요"

    return {
        "total_predictions": n,
        "total_hits": hits,
        "total_failures": failures,
        "accuracy_pct": round(acc, 2),
        "max_consecutive_failures": max_consec,
        "verdict": verdict,
        "verdict_label": label,
    }


def _load_prediction_meta_lookup(conn) -> dict[str, dict]:
    """sim_win_rate_pct, pred_frequency 조회."""
    cur = conn.execute(f"PRAGMA table_info({PREDICTIONS_TABLE})")
    cols = {row[1] for row in cur.fetchall()}

    sel = ["prefix", "predicted_value"]
    if "sim_win_rate_pct" in cols:
        sel.append("sim_win_rate_pct")
    if "pred_frequency" in cols:
        sel.append("pred_frequency")

    df = pd.read_sql_query(
        f"""
        SELECT {", ".join(sel)}
        FROM {PREDICTIONS_TABLE}
        WHERE window_size = ? AND method = ? AND threshold = ?
        """,
        conn,
        params=[WINDOW_SIZE, METHOD, THRESHOLD],
    )
    out: dict[str, dict] = {}
    for _, r in df.iterrows():
        p = str(r["prefix"]).strip().lower()
        if not p:
            continue
        meta: dict = {}
        pv = r.get("predicted_value")
        if pv is not None and not (isinstance(pv, float) and pd.isna(pv)):
            pvs = str(pv).strip().lower()
            meta["predicted"] = pvs if pvs in ("b", "p") else None
        else:
            meta["predicted"] = None
        if "sim_win_rate_pct" in df.columns:
            v = r.get("sim_win_rate_pct")
            meta["sim_win_rate_pct"] = (
                float(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else None
            )
        if "pred_frequency" in df.columns:
            v = r.get("pred_frequency")
            meta["pred_frequency"] = (
                float(v) if v is not None and not (isinstance(v, float) and pd.isna(v)) else None
            )
        out[p] = meta
    return out


def load_final_rule_lookup() -> dict[str, dict]:
    """pattern_list2 취합비교 기준 ws9 prefix → 결정 규칙 lookup."""
    try:
        from pattern_list_profiles import get_profile
        from pattern_list2_snapshot import get_active_rule_version

        cmp_df, _ = build_comparison_df("list2")
        rule_version = get_active_rule_version(get_profile("list2"))
        enabled = parse_rule_version(rule_version)
        _, classify_fn, _ = build_rule_engine(enabled)
    except Exception:
        return {}

    if cmp_df is None or cmp_df.empty:
        return {}

    out: dict[str, dict] = {}
    for _, row in cmp_df.iterrows():
        prefix = str(row.get("ws9_core") or "").strip().lower()
        if not prefix:
            continue
        rule_id = classify_fn(row)
        out[prefix] = {
            "final_rule": rule_id,
            "final_rule_label": rule_label(rule_id),
            "final_rule_desc": rule_description(rule_id),
        }
    return out


def _classify_impact(live_share_pct: float, live_accuracy_pct: float, gap_pct) -> str:
    """고빈도 prefix의 적중 특성 라벨."""
    if live_share_pct >= 5.0 and live_accuracy_pct < 50.0:
        return "핵심 리스크"
    if live_share_pct >= 5.0 and live_accuracy_pct >= 55.0:
        return "핵심 강점"
    if (
        live_share_pct >= 3.0
        and gap_pct is not None
        and not pd.isna(gap_pct)
        and gap_pct <= -10.0
    ):
        return "기대↓실제"
    if live_share_pct >= 3.0 and live_accuracy_pct < 45.0:
        return "주의"
    return ""


def summarize_by_prefix(df: pd.DataFrame, conn=None) -> pd.DataFrame:
    """
    Q3: prefix별 요약 — 라이브 빈도(중요) 중심.

    정렬 기본: live n 내림차순 (자주 나온 prefix가 상단).
    """
    empty_cols = [
        "rank_by_volume", "prefix", "predicted", "final_rule", "final_rule_label", "final_rule_desc",
        "live_n", "live_share_pct", "cumulative_share_pct",
        "hits", "misses", "miss_share_pct", "live_accuracy_pct",
        "avg_confidence", "pred_frequency", "sim_win_rate_pct", "gap_pct", "impact",
    ]
    if df is None or len(df) == 0:
        return pd.DataFrame(columns=empty_cols)

    own_conn = conn is None
    if own_conn:
        conn = get_db_connection()
    try:
        meta_map = _load_prediction_meta_lookup(conn)
        rule_map = load_final_rule_lookup()
        unknown = FINAL_RULE_INFO["unknown"]
        total_n = len(df)
        total_hits = int((df["is_correct"] == 1).sum())
        total_misses = total_n - total_hits

        grouped = (
            df.groupby("prefix", as_index=False)
            .agg(
                live_n=("is_correct", "count"),
                hits=("is_correct", lambda s: int((s == 1).sum())),
                avg_confidence=("confidence", "mean"),
            )
        )
        grouped["misses"] = grouped["live_n"] - grouped["hits"]
        grouped["live_accuracy_pct"] = (
            100.0 * grouped["hits"] / grouped["live_n"]
        ).round(2)
        grouped["live_share_pct"] = (
            100.0 * grouped["live_n"] / total_n
        ).round(2)
        grouped["miss_share_pct"] = grouped["misses"].apply(
            lambda m: round(100.0 * m / total_misses, 2) if total_misses > 0 else 0.0
        )
        grouped["avg_confidence"] = grouped["avg_confidence"].round(2)

        grouped["final_rule"] = grouped["prefix"].map(
            lambda p: (rule_map.get(p) or {}).get("final_rule", "unknown")
        )
        grouped["final_rule_label"] = grouped["prefix"].map(
            lambda p: (rule_map.get(p) or {}).get("final_rule_label", unknown["label"])
        )
        grouped["final_rule_desc"] = grouped["prefix"].map(
            lambda p: (rule_map.get(p) or {}).get("final_rule_desc", unknown["description"])
        )

        grouped["predicted"] = grouped["prefix"].map(
            lambda p: (meta_map.get(p) or {}).get("predicted")
        )

        grouped["pred_frequency"] = grouped["prefix"].map(
            lambda p: (meta_map.get(p) or {}).get("pred_frequency")
        )
        grouped["sim_win_rate_pct"] = grouped["prefix"].map(
            lambda p: (meta_map.get(p) or {}).get("sim_win_rate_pct")
        )
        grouped["gap_pct"] = grouped.apply(
            lambda r: (
                round(r["live_accuracy_pct"] - r["sim_win_rate_pct"], 2)
                if r["sim_win_rate_pct"] is not None and not pd.isna(r["sim_win_rate_pct"])
                else None
            ),
            axis=1,
        )
        grouped["impact"] = grouped.apply(
            lambda r: _classify_impact(
                r["live_share_pct"], r["live_accuracy_pct"], r["gap_pct"]
            ),
            axis=1,
        )

        grouped = grouped.sort_values(
            ["live_n", "live_accuracy_pct"], ascending=[False, True]
        ).reset_index(drop=True)
        grouped["rank_by_volume"] = range(1, len(grouped) + 1)
        grouped["cumulative_share_pct"] = grouped["live_share_pct"].cumsum().round(2)

        return grouped[empty_cols]
    finally:
        if own_conn:
            conn.close()


def summarize_volume_focus(df: pd.DataFrame, prefix_df: pd.DataFrame) -> dict:
    """
    빈도 상위 prefix가 전체 적중률에 미치는 영향 요약.

    - top80: 누적 live_share 80%까지 prefix들의 가중 적중률
    - top10: live n 상위 10개 prefix 가중 적중률
    - high_volume: live_n >= 5 prefix 가중 적중률
    """
    overall = summarize_overall(df)
    if prefix_df is None or len(prefix_df) == 0:
        return {
            "overall_accuracy_pct": overall["accuracy_pct"],
            "top80_prefix_count": 0,
            "top80_live_n": 0,
            "top80_share_pct": 0.0,
            "top80_accuracy_pct": None,
            "top80_gap_vs_overall": None,
            "top10_accuracy_pct": None,
            "high_volume_accuracy_pct": None,
            "high_volume_prefix_count": 0,
            "high_volume_live_n": 0,
        }

    sorted_df = prefix_df.sort_values("live_n", ascending=False)

    top80 = sorted_df[sorted_df["cumulative_share_pct"] <= 80.0]
    if len(top80) == 0:
        top80 = sorted_df.head(1)
    else:
        # 80% 경계를 넘는 첫 prefix 포함
        next_idx = len(top80)
        if next_idx < len(sorted_df):
            top80 = pd.concat([top80, sorted_df.iloc[[next_idx]]], ignore_index=True)

    top10 = sorted_df.head(min(10, len(sorted_df)))
    high_vol = sorted_df[sorted_df["live_n"] >= 5]

    def _weighted_acc(sub: pd.DataFrame):
        if len(sub) == 0:
            return None
        n = int(sub["live_n"].sum())
        hits = int(sub["hits"].sum())
        return round(100.0 * hits / n, 2) if n > 0 else None

    top80_acc = _weighted_acc(top80)
    top10_acc = _weighted_acc(top10)
    hv_acc = _weighted_acc(high_vol)

    return {
        "overall_accuracy_pct": overall["accuracy_pct"],
        "top80_prefix_count": len(top80),
        "top80_live_n": int(top80["live_n"].sum()),
        "top80_share_pct": round(float(top80["live_share_pct"].sum()), 2),
        "top80_accuracy_pct": top80_acc,
        "top80_gap_vs_overall": (
            round(top80_acc - overall["accuracy_pct"], 2)
            if top80_acc is not None else None
        ),
        "top10_accuracy_pct": top10_acc,
        "high_volume_accuracy_pct": hv_acc,
        "high_volume_prefix_count": len(high_vol),
        "high_volume_live_n": int(high_vol["live_n"].sum()) if len(high_vol) > 0 else 0,
    }


def _weighted_accuracy(sub: pd.DataFrame) -> float | None:
    if sub is None or len(sub) == 0:
        return None
    live_n = int(sub["live_n"].sum())
    if live_n <= 0:
        return None
    hits = int(sub["hits"].sum())
    return round(100.0 * hits / live_n, 2)


def _weighted_sim_win_rate(sub: pd.DataFrame) -> float | None:
    if sub is None or len(sub) == 0:
        return None
    valid = sub[sub["sim_win_rate_pct"].notna()]
    if len(valid) == 0:
        return None
    live_n = int(valid["live_n"].sum())
    if live_n <= 0:
        return None
    weighted = (valid["sim_win_rate_pct"] * valid["live_n"]).sum() / live_n
    return round(float(weighted), 2)


def summarize_by_rule(prefix_df: pd.DataFrame) -> pd.DataFrame:
    """규칙별 빈도 가중·prefix 단위·고빈도 적중률 집계."""
    cols = [
        "final_rule", "final_rule_label", "final_rule_desc",
        "prefix_count", "live_n", "live_share_pct",
        "hits", "misses", "weighted_accuracy_pct",
        "unweighted_prefix_mean_pct", "prefix_ge_50_pct",
        "high_volume_n", "high_volume_accuracy_pct",
        "sim_weighted_win_rate_pct", "sim_gap_pct",
    ]
    if prefix_df is None or len(prefix_df) == 0:
        return pd.DataFrame(columns=cols)

    total_n = int(prefix_df["live_n"].sum())
    rule_order = ["R1", "R2", "R3", "unknown"]
    rows = []
    for rule_id in rule_order:
        sub = prefix_df[prefix_df["final_rule"] == rule_id]
        if len(sub) == 0:
            continue
        live_n = int(sub["live_n"].sum())
        hits = int(sub["hits"].sum())
        weighted_acc = _weighted_accuracy(sub)
        high_vol = sub[sub["live_n"] >= 5]
        sim_wr = _weighted_sim_win_rate(sub)
        info = FINAL_RULE_INFO.get(rule_id, FINAL_RULE_INFO["unknown"])
        rows.append({
            "final_rule": rule_id,
            "final_rule_label": info["label"],
            "final_rule_desc": info["description"],
            "prefix_count": len(sub),
            "live_n": live_n,
            "live_share_pct": round(100.0 * live_n / total_n, 2) if total_n > 0 else 0.0,
            "hits": hits,
            "misses": live_n - hits,
            "weighted_accuracy_pct": weighted_acc,
            "unweighted_prefix_mean_pct": round(float(sub["live_accuracy_pct"].mean()), 2),
            "prefix_ge_50_pct": round(
                100.0 * (sub["live_accuracy_pct"] >= 50).sum() / len(sub), 1
            ),
            "high_volume_n": int(high_vol["live_n"].sum()) if len(high_vol) > 0 else 0,
            "high_volume_accuracy_pct": _weighted_accuracy(high_vol),
            "sim_weighted_win_rate_pct": sim_wr,
            "sim_gap_pct": (
                round(weighted_acc - sim_wr, 2)
                if weighted_acc is not None and sim_wr is not None else None
            ),
        })
    return pd.DataFrame(rows)


def build_rule_commentary(
    prefix_df: pd.DataFrame,
    rule_df: pd.DataFrame,
    overall: dict,
) -> list[str]:
    """
    규칙별 성과 자동 해석 (빈도 가중 중심).
    prefix 단위 평균 vs live n 가중 적중률 차이, 특히 R1 착시 설명.
    """
    if prefix_df is None or len(prefix_df) == 0 or rule_df is None or len(rule_df) == 0:
        return ["규칙별 분석 데이터가 없습니다."]

    lines: list[str] = []
    total_n = int(prefix_df["live_n"].sum())
    overall_acc = overall.get("accuracy_pct", 0.0)

    lines.append(
        f"**전체 {total_n}건 · 가중 적중률 {overall_acc:.2f}%** — "
        "아래 해석은 prefix 개수 평균이 아니라 **실제 베팅 횟수(live n) 가중** 기준입니다."
    )

    ranked = rule_df.sort_values("weighted_accuracy_pct", ascending=False)
    best = ranked.iloc[0]
    worst = ranked.iloc[-1]

    lines.append(
        f"- **빈도 가중 1위: {best['final_rule']}** "
        f"({best['weighted_accuracy_pct']:.2f}%, live n {int(best['live_n'])} · "
        f"비중 {best['live_share_pct']:.1f}%)"
    )
    if len(ranked) > 1:
        lines.append(
            f"- **빈도 가중 최하: {worst['final_rule']}** "
            f"({worst['weighted_accuracy_pct']:.2f}%, live n {int(worst['live_n'])})"
        )

    # R1 착시: prefix 평균 vs 가중
    r1_row = None
    r1_df = rule_df[rule_df["final_rule"] == "R1"]
    if len(r1_df) > 0:
        r1_row = r1_df.iloc[0]
        illusion_gap = round(
            float(r1_row["unweighted_prefix_mean_pct"])
            - float(r1_row["weighted_accuracy_pct"]),
            2,
        )
        if illusion_gap >= 3.0:
            lines.append(
                f"- **R1 주의 — prefix 평균 착시:** prefix별 단순 평균 "
                f"**{r1_row['unweighted_prefix_mean_pct']:.1f}%** (≥50% prefix "
                f"{r1_row['prefix_ge_50_pct']:.0f}%) vs live n 가중 "
                f"**{r1_row['weighted_accuracy_pct']:.2f}%** "
                f"(차이 **−{illusion_gap:.1f}%p**). "
                "적중률 좋은 prefix가 많아 보여도, **자주 나오는 고빈도 prefix**가 "
                "전체 성과를 끌어내리면 실전 적중률은 낮아집니다."
            )
            r1_sub = prefix_df[prefix_df["final_rule"] == "R1"].sort_values(
                "live_n", ascending=False
            )
            top_bad = r1_sub[
                (r1_sub["live_n"] >= 5) & (r1_sub["live_accuracy_pct"] < 50)
            ].head(3)
            if len(top_bad) > 0:
                examples = ", ".join(
                    f"`{r.prefix}`(n={int(r.live_n)}, {r.live_accuracy_pct:.0f}%)"
                    for _, r in top_bad.iterrows()
                )
                lines.append(f"  - R1 고빈도 부진 예: {examples}")

    for _, row in rule_df.iterrows():
        rule_id = row["final_rule"]
        if rule_id == "unknown":
            continue

        hv_n = int(row["high_volume_n"])
        hv_acc = row["high_volume_accuracy_pct"]
        weighted = row["weighted_accuracy_pct"]
        unweighted = row["unweighted_prefix_mean_pct"]
        sim_gap = row["sim_gap_pct"]

        detail_parts = [
            f"**{rule_id}** — 가중 {weighted:.2f}% (live n {int(row['live_n'])}, "
            f"비중 {row['live_share_pct']:.1f}%)",
        ]
        if hv_n > 0 and hv_acc is not None:
            detail_parts.append(f"고빈도(n≥5) {hv_acc:.2f}% / n {hv_n}")
        detail_parts.append(f"prefix 평균 {unweighted:.1f}%")
        if sim_gap is not None:
            direction = "라이브↑" if sim_gap > 0 else "라이브↓"
            detail_parts.append(f"시뮬 대비 {sim_gap:+.1f}%p ({direction})")
        lines.append("- " + " · ".join(detail_parts))

    # 실무 제안
    r2_df = rule_df[rule_df["final_rule"] == "R2"]
    r3_df = rule_df[rule_df["final_rule"] == "R3"]
    suggestions: list[str] = []

    if len(r2_df) > 0 and r2_df.iloc[0]["weighted_accuracy_pct"] < overall_acc - 2:
        suggestions.append(
            f"R2({r2_df.iloc[0]['weighted_accuracy_pct']:.1f}%)는 전체보다 낮고 "
            "sim≠ngram 역베팅 성격 — pass 전환 1순위 검토"
        )
    if len(r3_df) > 0 and r1_row is not None:
        r3_acc = float(r3_df.iloc[0]["weighted_accuracy_pct"])
        r1_acc = float(r1_row["weighted_accuracy_pct"])
        if r3_acc > r1_acc:
            suggestions.append(
                f"빈도 가중 기준 R3({r3_acc:.1f}%) > R1({r1_acc:.1f}%) — "
                "R1 prefix 평균이 높아도 **실전 베팅 비중**은 R3 쪽이 더 유효"
            )
    if r1_row is not None:
        r1_hv = r1_row["high_volume_accuracy_pct"]
        r1_uw = r1_row["unweighted_prefix_mean_pct"]
        if (
            r1_uw is not None
            and r1_hv is not None
            and float(r1_uw) >= 53
            and float(r1_hv) < 50
        ):
            suggestions.append(
                f"R1은 prefix 평균 {r1_uw:.0f}%로 유효해 보이나, "
                f"고빈도(n≥5)만 보면 {r1_hv:.1f}% — **빈도 반영 시 재판단 필요**"
            )

    best_acc = float(best["weighted_accuracy_pct"])
    if suggestions:
        lines.append("**실무 시사점**")
        lines.extend(f"- {s}" for s in suggestions)
    if best_acc < 55:
        lines.append(
            f"- 현재 최고 규칙({best['final_rule']})도 "
            f"**{best_acc:.1f}%**로 55% 유효 기준 미달 — 추가 데이터 축적 후 재검증 권장"
        )

    return lines


def summarize_volume_buckets(prefix_df: pd.DataFrame) -> pd.DataFrame:
    """라이브 빈도 구간별 가중 적중률 — 고빈도 구간 성과 한눈에."""
    cols = [
        "volume_bucket", "prefix_count", "live_n", "live_share_pct",
        "hits", "misses", "weighted_accuracy_pct",
    ]
    if prefix_df is None or len(prefix_df) == 0:
        return pd.DataFrame(columns=cols)

    total_n = int(prefix_df["live_n"].sum())
    rows = []
    for label, lo, hi in VOLUME_BUCKETS:
        if hi is None:
            sub = prefix_df[prefix_df["live_n"] >= lo]
        else:
            sub = prefix_df[(prefix_df["live_n"] >= lo) & (prefix_df["live_n"] <= hi)]
        live_n = int(sub["live_n"].sum()) if len(sub) > 0 else 0
        hits = int(sub["hits"].sum()) if len(sub) > 0 else 0
        misses = live_n - hits
        acc = round(100.0 * hits / live_n, 2) if live_n > 0 else None
        rows.append({
            "volume_bucket": label,
            "prefix_count": len(sub),
            "live_n": live_n,
            "live_share_pct": round(100.0 * live_n / total_n, 2) if total_n > 0 else 0.0,
            "hits": hits,
            "misses": misses,
            "weighted_accuracy_pct": acc,
        })
    return pd.DataFrame(rows)


RULE_VIZ_ORDER = ("R3", "R1", "R2")


def build_prefix_viz_groups(
    prefix_df: pd.DataFrame,
    search: str = "",
    rule_order: tuple[str, ...] = RULE_VIZ_ORDER,
) -> list[dict]:
    """규칙별 그룹 · 그룹 내 적중률 내림차순 (시각화용)."""
    if prefix_df is None or len(prefix_df) == 0:
        return []

    df = prefix_df.copy()
    q = (search or "").strip().lower()
    if q:
        df = df[df["prefix"].astype(str).str.lower().str.contains(q, na=False, regex=False)]

    groups: list[dict] = []
    for rule_id in rule_order:
        sub = df[df["final_rule"] == rule_id].sort_values(
            ["live_accuracy_pct", "live_n"],
            ascending=[False, False],
        )
        if len(sub) == 0:
            continue
        info = FINAL_RULE_INFO.get(rule_id, {})
        groups.append({
            "rule_id": rule_id,
            "rule_label": info.get("label", rule_id),
            "rule_desc": info.get("description", ""),
            "items": sub.to_dict("records"),
        })
    return groups


def filter_prefix_steps(df: pd.DataFrame, prefix: str) -> pd.DataFrame:
    """선택 prefix의 raw step 목록."""
    if df is None or len(df) == 0 or not prefix:
        return pd.DataFrame()
    p = str(prefix).strip().lower()
    out = df[df["prefix"] == p].copy()
    cols = [
        "step", "created_at", "position", "prefix",
        "predicted", "actual", "is_correct", "confidence",
    ]
    return out[[c for c in cols if c in out.columns]].reset_index(drop=True)
