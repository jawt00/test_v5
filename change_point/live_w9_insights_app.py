"""윈도우 9 라이브 인사이트 — Q1 적중률, Q3 prefix 분석."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st
import pandas as pd

from live_w9_insights import (
    DB_PATH,
    build_prefix_viz_groups,
    build_rule_commentary,
    filter_prefix_steps,
    get_db_connection,
    load_valid_w9_steps,
    summarize_by_prefix,
    summarize_by_rule,
    summarize_overall,
    summarize_volume_buckets,
    summarize_volume_focus,
)
from pattern_list2_final_rules import FINAL_RULE_INFO
from prefix_pattern_svg import prefix_pattern_svg_viz

st.set_page_config(
    page_title="W9 라이브 인사이트",
    page_icon="📊",
    layout="wide",
)

VERDICT_STYLE = {
    "valid": ("success", "유효 가능"),
    "invalid": ("error", "유효하지 않음"),
    "insufficient": ("warning", "판단 보류 (표본 부족)"),
    "uncertain": ("info", "추가 데이터 필요"),
    "no_data": ("info", "데이터 없음"),
}


def _verdict_banner(verdict: str, label: str):
    msg = f"**판정:** {label}"
    if verdict == "valid":
        st.success(msg)
    elif verdict == "invalid":
        st.error(msg)
    elif verdict == "insufficient":
        st.warning(msg)
    else:
        st.info(msg)


def _fmt_pct(v):
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "-"
    return f"{float(v):.2f}"


def _filter_by_prefix_search(df: pd.DataFrame, query: str) -> pd.DataFrame:
    """prefix 부분 일치 검색 (대소문자 무시)."""
    q = (query or "").strip().lower()
    if not q or df is None or len(df) == 0:
        return df
    return df[df["prefix"].astype(str).str.lower().str.contains(q, na=False, regex=False)]


def _format_prefix_table(prefix_df: pd.DataFrame) -> pd.DataFrame:
    out = prefix_df.copy()
    out["sim_win_rate_pct"] = out["sim_win_rate_pct"].apply(_fmt_pct)
    out["gap_pct"] = out["gap_pct"].apply(_fmt_pct)
    out["pred_frequency"] = out["pred_frequency"].apply(
        lambda v: int(v) if v is not None and not pd.isna(v) else "-"
    )
    return out.rename(columns={
        "rank_by_volume": "빈도순위",
        "prefix": "Prefix",
        "final_rule_label": "결정 규칙",
        "final_rule_desc": "규칙 설명",
        "live_n": "라이브 n",
        "live_share_pct": "라이브 비중(%)",
        "cumulative_share_pct": "누적 비중(%)",
        "hits": "적중",
        "misses": "미적중",
        "miss_share_pct": "미적중 기여(%)",
        "live_accuracy_pct": "라이브 적중률(%)",
        "avg_confidence": "평균 신뢰도",
        "pred_frequency": "시뮬 빈도",
        "sim_win_rate_pct": "시뮬 승률(%)",
        "gap_pct": "갭(라이브-시뮬)",
        "impact": "영향",
    })


def _render_rule_reference() -> None:
    with st.expander("최종 예측 규칙 설명 (list2 취합비교)"):
        st.markdown(
            "라이브 예측값은 `pattern_list2.db` · `simulation_predictions_change_point`에 "
            "저장되며, 아래 우선순위로 결정됩니다."
        )
        for rule_id in ("R1", "R2", "R3", "R5"):
            info = FINAL_RULE_INFO[rule_id]
            st.markdown(f"**{info['label']}** — {info['description']}")
        st.markdown(f"**{FINAL_RULE_INFO['R4']['label']}** — {FINAL_RULE_INFO['R4']['description']}")
        st.caption(
            "유효 라이브 예측(B/P)은 선택된 규칙(R1~R5)으로 결정된 prefix만 포함됩니다. "
            "R4(pass)는 라이브에서 skipped 처리됩니다."
        )


def _accuracy_border_color(pct: float) -> str:
    if pct >= 55:
        return "#22c55e"
    if pct < 50:
        return "#ef4444"
    return "#94a3b8"


def _prediction_badge_html(predicted: str | None) -> str:
    p = (predicted or "").strip().lower()
    if p == "b":
        color = "#c00"
        label = "B"
    elif p == "p":
        color = "#06c"
        label = "P"
    else:
        return ""
    return (
        f'<span style="display:inline-block;font-weight:700;font-size:14px;'
        f'color:{color};border:1.5px solid {color};border-radius:4px;'
        f'padding:1px 6px;margin-left:6px;vertical-align:middle;">{label}</span>'
    )


def _render_prefix_rule_visualization(prefix_df: pd.DataFrame, search: str = "") -> None:
    """화면 하단: R3 → R1 → R2 규칙별 prefix 도형 시각화."""
    groups = build_prefix_viz_groups(prefix_df, search=search)
    st.markdown("---")
    st.markdown("## Prefix 규칙별 도형 시각화")
    st.caption(
        "그룹 순서 R3 → R1 → R2 · 그룹 내 적중률 높은 순 · "
        "b=빨강 p=파랑 · 마지막 문자 우하단 1/4 채움 · 배지=예측값(B/P)"
    )

    if not groups:
        q = search.strip()
        if q:
            st.info(f"검색 `{q}`에 해당하는 prefix가 없습니다.")
        else:
            st.info("시각화할 prefix가 없습니다.")
        return

    for group in groups:
        rule_id = group["rule_id"]
        items = group["items"]
        st.markdown(f"### {group['rule_label']}")
        st.caption(f"{group['rule_desc']} · {len(items)}개 prefix")

        cards_html = [
            '<div style="display:flex;flex-wrap:wrap;gap:10px;margin-bottom:8px;">'
        ]
        for item in items:
            prefix = str(item.get("prefix") or "").strip().lower()
            acc = float(item.get("live_accuracy_pct") or 0)
            live_n = int(item.get("live_n") or 0)
            hits = int(item.get("hits") or 0)
            predicted = item.get("predicted")
            border = _accuracy_border_color(acc)
            svg = prefix_pattern_svg_viz(prefix)
            svg_block = svg if svg else "—"
            pred_badge = _prediction_badge_html(predicted)
            cards_html.append(
                f'<div style="border:2px solid {border};border-radius:8px;'
                f'padding:8px 10px;min-width:120px;background:#fafafa;">'
                f'<div style="font-family:monospace;font-size:12px;margin-bottom:4px;">'
                f'{prefix}</div>'
                f'<div style="margin-bottom:6px;display:flex;align-items:center;gap:6px;">'
                f'{svg_block}{pred_badge}</div>'
                f'<div style="font-size:12px;color:#333;">'
                f'<strong>{acc:.1f}%</strong> · n={live_n} · {hits}/{live_n}'
                f'</div></div>'
            )
        cards_html.append("</div>")
        st.markdown("".join(cards_html), unsafe_allow_html=True)


def main():
    st.title("윈도우 9 라이브 인사이트")
    st.caption(
        f"DB: `{DB_PATH.name}` · `live_step_results` · window_size=9 · "
        "유효 예측(skipped=0, B/P)만 집계"
    )

    st.markdown("---")
    st.markdown("### 필터")
    c1, c2 = st.columns(2)
    with c1:
        date_from = st.text_input(
            "created_at 시작 (YYYY-MM-DD, 선택)",
            value="",
            placeholder="예: 2026-07-01",
        )
    with c2:
        date_to = st.text_input(
            "created_at 종료 (YYYY-MM-DD, 선택)",
            value="",
            placeholder="예: 2026-07-31",
        )

    df_from = date_from.strip() or None
    df_to = date_to.strip() or None
    if df_to and len(df_to) == 10:
        df_to = df_to + " 23:59:59"

    conn = get_db_connection()
    try:
        df = load_valid_w9_steps(conn, date_from=df_from, date_to=df_to)
        prefix_df = summarize_by_prefix(df, conn=conn)
    finally:
        conn.close()

    st.markdown("---")
    st.markdown("## Q1. 라이브 적중률 (전체 누적)")

    summary = summarize_overall(df)
    _verdict_banner(summary["verdict"], summary["verdict_label"])

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("유효 예측 수", summary["total_predictions"])
    m2.metric("적중", summary["total_hits"])
    m3.metric("적중률 (%)", f"{summary['accuracy_pct']:.2f}")
    m4.metric("연속 실패 최대", summary["max_consecutive_failures"])

    if summary["total_predictions"] < 20:
        st.caption("유효 예측 20건 미만 — 판정은 참고용입니다.")
    else:
        st.caption("판정 기준: N≥20 & ≥55% → 유효 가능 / ≤52% → 유효하지 않음")

    st.markdown("---")
    st.markdown("## Q3. Prefix 분석 (빈도 중심)")

    if len(prefix_df) == 0:
        st.info("유효 예측 데이터가 없습니다. 라이브게임에서 결과 저장 후 다시 확인하세요.")
        return

    focus = summarize_volume_focus(df, prefix_df)
    buckets = summarize_volume_buckets(prefix_df)
    rule_df = summarize_by_rule(prefix_df)

    _render_rule_reference()

    if len(rule_df) > 0:
        st.markdown("#### 규칙별 라이브 성과")
        rule_show = rule_df.rename(columns={
            "final_rule_label": "결정 규칙",
            "final_rule_desc": "규칙 설명",
            "prefix_count": "prefix 수",
            "live_n": "라이브 n",
            "live_share_pct": "비중(%)",
            "hits": "적중",
            "misses": "미적중",
            "weighted_accuracy_pct": "가중 적중률(%)",
            "unweighted_prefix_mean_pct": "prefix 평균(%)",
            "prefix_ge_50_pct": "≥50% prefix(%)",
            "high_volume_n": "고빈도 n(≥5)",
            "high_volume_accuracy_pct": "고빈도 적중(%)",
            "sim_weighted_win_rate_pct": "시뮬 승률(%)",
            "sim_gap_pct": "갭(라이브-시뮬)",
        })
        st.dataframe(rule_show, use_container_width=True, hide_index=True)
        st.caption(
            "**가중 적중률** = live n로 가중한 실전 적중률 · "
            "**prefix 평균** = prefix마다 1표씩 단순 평균 (빈도 미반영) · "
            "두 값 차이가 크면 prefix 목록만 보고 판단하기 어렵습니다."
        )

        commentary = build_rule_commentary(prefix_df, rule_df, summary)
        st.markdown("#### 규칙별 해석 (자동)")
        for line in commentary:
            st.markdown(line)

    st.markdown("### 고빈도 prefix가 전체 성과를 좌우합니다")
    st.caption(
        "라이브에서 자주 등장하는 prefix의 가중 적중률이 전체 Q1과 얼마나 다른지 확인하세요. "
        "빈도는 낮아도 미적중 기여(%)가 큰 prefix는 아래 표에서 `미적중 기여` 열로 확인할 수 있습니다."
    )

    f1, f2, f3, f4 = st.columns(4)
    f1.metric(
        "상위 80% 구간 적중률",
        _fmt_pct(focus["top80_accuracy_pct"]),
        delta=(
            f"{focus['top80_gap_vs_overall']:+.2f}%p vs 전체"
            if focus["top80_gap_vs_overall"] is not None else None
        ),
    )
    f2.metric(
        "상위 80% 구간",
        f"{focus['top80_prefix_count']}개 prefix",
        delta=f"live n {focus['top80_live_n']}건 ({focus['top80_share_pct']}%)",
    )
    f3.metric(
        "빈도 Top10 적중률",
        _fmt_pct(focus["top10_accuracy_pct"]),
    )
    f4.metric(
        "live n≥5 적중률",
        _fmt_pct(focus["high_volume_accuracy_pct"]),
        delta=f"{focus['high_volume_prefix_count']}개 prefix · n {focus['high_volume_live_n']}",
    )

    st.markdown("#### 라이브 빈도 구간별 요약")
    bucket_show = buckets.rename(columns={
        "volume_bucket": "구간",
        "prefix_count": "prefix 수",
        "live_n": "라이브 n",
        "live_share_pct": "비중(%)",
        "hits": "적중",
        "misses": "미적중",
        "weighted_accuracy_pct": "가중 적중률(%)",
    })
    st.dataframe(bucket_show, use_container_width=True, hide_index=True)
    st.caption(
        "구간별 가중 적중률: 고빈도 구간(n≥10, n=5~9)이 전체 Q1보다 높으면 "
        "예측은 고빈도 prefix에서 유효하고, 저빈도 noise가 전체를 끌어내리는 패턴일 수 있습니다."
    )

    st.markdown("#### Prefix 전체 목록")
    search_c1, search_c2 = st.columns([2, 3])
    with search_c1:
        prefix_search = st.text_input(
            "Prefix 검색",
            value="",
            placeholder="예: bppp / pbpppp",
            key="prefix_search",
            help="전체 목록과 상세 선택 목록에 동시 적용됩니다.",
        )
    with search_c2:
        sort_mode = st.radio(
            "정렬",
            ["빈도순 (기본)", "미적중 기여순", "적중률 낮은 순", "리스크 우선", "규칙별"],
            horizontal=True,
            key="prefix_sort",
        )

    sorted_df = prefix_df.copy()
    if sort_mode == "미적중 기여순":
        sorted_df = sorted_df.sort_values(
            ["miss_share_pct", "live_n"], ascending=[False, False]
        )
    elif sort_mode == "적중률 낮은 순":
        sorted_df = sorted_df.sort_values(
            ["live_accuracy_pct", "live_n"], ascending=[True, False]
        )
    elif sort_mode == "리스크 우선":
        risk_order = {"핵심 리스크": 0, "기대↓실제": 1, "주의": 2, "핵심 강점": 3, "": 4}
        sorted_df["_risk_ord"] = sorted_df["impact"].map(risk_order)
        sorted_df = sorted_df.sort_values(
            ["_risk_ord", "live_n"], ascending=[True, False]
        ).drop(columns=["_risk_ord"])
    elif sort_mode == "규칙별":
        rule_order = {"R1": 0, "R2": 1, "R3": 2, "unknown": 3}
        sorted_df["_rule_ord"] = sorted_df["final_rule"].map(rule_order)
        sorted_df = sorted_df.sort_values(
            ["_rule_ord", "live_n"], ascending=[True, False]
        ).drop(columns=["_rule_ord"])
    else:
        sorted_df = sorted_df.sort_values(
            ["live_n", "live_accuracy_pct"], ascending=[False, True]
        )

    filtered_df = _filter_by_prefix_search(sorted_df, prefix_search)
    if len(filtered_df) == 0:
        st.info(f"검색 `{prefix_search.strip()}`에 해당하는 prefix가 없습니다.")
    else:
        display_df = _format_prefix_table(filtered_df)
        st.dataframe(display_df, use_container_width=True, hide_index=True)
    search_q = prefix_search.strip()
    if search_q:
        st.caption(
            f"검색 `{search_q}` · 표시 {len(filtered_df)} / {len(prefix_df)}개 prefix"
        )
    else:
        st.caption(
            f"총 {len(prefix_df)}개 prefix 전체 표시 · "
            "`결정 규칙`=list2 취합비교 최종 예측 규칙 · "
            "`라이브 n`=실제 베팅 횟수 · `시뮬 빈도`=예측 테이블 학습 빈도 · "
            "`갭`=라이브 적중률−시뮬 승률 · `영향`: 핵심 리스크=비중≥5% & 적중<50%"
        )

    st.markdown("#### Prefix 상세 (drill-down)")
    drill_base = prefix_df.sort_values("live_n", ascending=False)
    drill_filtered = _filter_by_prefix_search(drill_base, prefix_search)
    drill_prefixes = drill_filtered["prefix"].tolist()

    if not drill_prefixes:
        st.warning(f"검색 `{prefix_search.strip()}`에 해당하는 prefix가 없습니다.")
    else:
        selected = st.selectbox(
            "Prefix 선택",
            drill_prefixes,
            key="prefix_drilldown",
        )
        if prefix_search.strip():
            st.caption(f"선택 목록 {len(drill_prefixes)}개 · 검색 `{prefix_search.strip()}`")
        steps = filter_prefix_steps(df, selected)
        if len(steps) == 0:
            st.info("해당 prefix 스텝이 없습니다.")
        else:
            row = prefix_df[prefix_df["prefix"] == selected].iloc[0]
            d1, d2, d3, d4, d5 = st.columns(5)
            d1.metric("라이브 n", int(row["live_n"]))
            d2.metric("라이브 적중률", f"{row['live_accuracy_pct']:.2f}%")
            d3.metric("시뮬 승률", _fmt_pct(row["sim_win_rate_pct"]))
            d4.metric("미적중 기여", f"{row['miss_share_pct']:.2f}%")
            d5.metric("결정 규칙", row["final_rule_label"])
            st.caption(row["final_rule_desc"])

            show = steps.copy()
            show["is_correct"] = show["is_correct"].map({1: "O", 0: "X"})
            show = show.rename(columns={
                "step": "Step",
                "created_at": "저장 시각",
                "position": "Position",
                "prefix": "Prefix",
                "predicted": "예측",
                "actual": "실제",
                "is_correct": "일치",
                "confidence": "신뢰도",
            })
            st.dataframe(show, use_container_width=True, hide_index=True)

    _render_prefix_rule_visualization(prefix_df, search=prefix_search)


if __name__ == "__main__":
    main()
