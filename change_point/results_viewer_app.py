"""
시뮬레이션/라이브 결과 조회 앱

- 연속 불일치 높은 결과: 문제 케이스 우선 탐색
- 통계 대시보드: max_consecutive_failures 분포, 평균 정확도 등
- 상세 조회: run_id/grid_string_id로 직접 조회
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd

from svg_parser_module import get_change_point_db_connection
from results_storage import (
    query_simulation_by_grid_string_id,
    query_simulation_step_events,
    query_simulation_run_detail,
    query_simulation_runs_list,
    query_simulation_stats,
    query_simulation_high_failure_results,
    query_simulation_hypothesis_keys,
    query_simulation_consecutive_match_stats,
    query_simulation_top_consecutive_matches,
    query_simulation_consecutive_failure_stats,
    query_step_events_prefix_win_rate,
    query_step_events_prefix_win_rate_by_hypothesis,
    query_step_events_skip_summary_by_hypothesis,
    query_skipped_correct_insight_aggregates,
    query_skipped_correct_insight_aggregates_sim_wr_insufficient,
    query_skipped_sim_win_rates_extracted,
    query_skipped_sim_wr_insufficient_by_confidence,
    query_live_run_detail,
    query_live_runs_list,
    query_live_stats,
    query_live_high_failure_results,
)

st.set_page_config(
    page_title="결과 조회",
    page_icon="📋",
    layout="wide",
)


def _format_match(is_correct):
    if is_correct is True or is_correct == 1:
        return "O"
    if is_correct is False or is_correct == 0:
        return "X"
    return "-"


def _format_predicted_display(entry, is_live=False):
    predicted = entry.get("predicted")
    skipped = entry.get("skipped") == 1 if isinstance(entry.get("skipped"), (int, float)) else bool(entry.get("skipped"))
    skip_reason = entry.get("skip_reason") or ""
    if skipped:
        sp = entry.get("skipped_prediction")
        if sp is not None:
            return f"{sp} ({skip_reason})" if skip_reason else str(sp)
        return f"- ({skip_reason})" if skip_reason else "- (skip)"
    return str(predicted) if predicted else "-"


def _normalize_ws_prefix(ws, prefix):
    """(window_size, prefix) 키 정규화: 조회·매칭 시 동일하게 사용."""
    if ws is None:
        return None
    p = str(prefix).strip() if prefix is not None else ""
    return (int(ws), p)


def query_sim_win_rate_from_predictions_table(events):
    """
    events에 등장하는 (window_size, prefix)에 대해
    simulation_predictions_change_point 테이블의 sim_win_rate_pct(빈도 기반, threshold=0) 조회.
    Returns:
        dict: (window_size, prefix) -> 시뮬레이션 승률(%) 표시 문자열. 키는 _normalize_ws_prefix와 동일.
    """
    if not events:
        return {}
    keys = set()
    for e in events:
        k = _normalize_ws_prefix(e.get("window_size"), e.get("prefix", ""))
        if k is not None:
            keys.add(k)
    if not keys:
        return {}
    conn = get_change_point_db_connection()
    try:
        cur = conn.execute(
            "PRAGMA table_info(simulation_predictions_change_point)"
        )
        cols = [row[1] for row in cur.fetchall()]
        if "sim_win_rate_pct" not in cols:
            return {}
        df = pd.read_sql_query(
            """
            SELECT window_size, prefix, sim_win_rate_pct
            FROM simulation_predictions_change_point
            WHERE method = '빈도 기반' AND threshold = 0
            """,
            conn,
        )
        out = {}
        for _, r in df.iterrows():
            k = _normalize_ws_prefix(r.get("window_size"), r.get("prefix"))
            if k is not None and k in keys:
                v = r.get("sim_win_rate_pct")
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    out[k] = f"{float(v):.1f}%"
                else:
                    out[k] = "-"
        return out
    except Exception:
        return {}
    finally:
        conn.close()


def query_sim_win_rate_numeric_from_predictions_table(events):
    """
    events에 등장하는 (window_size, prefix)에 대해
    simulation_predictions_change_point의 sim_win_rate_pct 수치 조회 (필터/비교용).
    Returns:
        dict: (window_size, prefix) -> float (승률 %) 또는 None. 키는 _normalize_ws_prefix와 동일.
    """
    if not events:
        return {}
    keys = set()
    for e in events:
        k = _normalize_ws_prefix(e.get("window_size"), e.get("prefix", ""))
        if k is not None:
            keys.add(k)
    if not keys:
        return {}
    conn = get_change_point_db_connection()
    try:
        cur = conn.execute(
            "PRAGMA table_info(simulation_predictions_change_point)"
        )
        cols = [row[1] for row in cur.fetchall()]
        if "sim_win_rate_pct" not in cols:
            return {}
        df = pd.read_sql_query(
            """
            SELECT window_size, prefix, sim_win_rate_pct
            FROM simulation_predictions_change_point
            WHERE method = '빈도 기반' AND threshold = 0
            """,
            conn,
        )
        out = {}
        for _, r in df.iterrows():
            k = _normalize_ws_prefix(r.get("window_size"), r.get("prefix"))
            if k is not None and k in keys:
                v = r.get("sim_win_rate_pct")
                if v is not None and not (isinstance(v, float) and pd.isna(v)):
                    out[k] = float(v)
                else:
                    out[k] = None
        return out
    except Exception:
        return {}
    finally:
        conn.close()


def filter_events_by_sim_win_rate(events, win_rate_numeric_map, min_pct):
    """시뮬레이션 승률이 min_pct 이상인 스텝만 반환. win_rate_numeric_map에 없거나 None이면 제외."""
    if not win_rate_numeric_map or min_pct is None or min_pct <= 0:
        return list(events) if events else []
    out = []
    for e in events or []:
        k = _normalize_ws_prefix(e.get("window_size"), e.get("prefix", ""))
        if k is None:
            continue
        v = win_rate_numeric_map.get(k)
        if v is not None and v >= min_pct:
            out.append(e)
    return out


def filter_events_by_sim_win_rate_per_window(events, win_rate_numeric_map, min_pct_w9, min_pct_w10):
    """
    윈도우별 시뮬레이션 승률 기준 적용.
    - window_size==9: min_pct_w9 이상인 스텝만 포함. 0이면 필터 없음(전체 포함).
    - window_size==10: min_pct_w10 이상인 스텝만 포함. 0이면 필터 없음(전체 포함).
    - 그 외 윈도우: 필터 없이 포함.
    """
    if not win_rate_numeric_map:
        return list(events) if events else []
    out = []
    for e in events or []:
        ws = e.get("window_size")
        k = _normalize_ws_prefix(ws, e.get("prefix", ""))
        if k is None:
            continue
        v = win_rate_numeric_map.get(k)
        if v is None:
            continue
        if ws == 9:
            if min_pct_w9 is not None and min_pct_w9 > 0 and v < min_pct_w9:
                continue
        elif ws == 10:
            if min_pct_w10 is not None and min_pct_w10 > 0 and v < min_pct_w10:
                continue
        out.append(e)
    return out


def filter_events_by_w9_w10_pairs(events, win_rate_numeric_map, min_pct_w9):
    """
    (윈도우9, 윈도우10) 쌍 단위 필터. events는 ORDER BY step으로 윈도우9→윈도우10 순서 가정.
    - 윈도우9 승률 >= min_pct_w9 이면 해당 쌍(윈도우9+윈도우10) 전부 포함.
    - 미만이면 해당 쌍 전부 제외. min_pct_w9가 0이면 필터 없이 전체 포함.
    """
    if not events:
        return []
    if min_pct_w9 is None or min_pct_w9 <= 0:
        return list(events)
    if not win_rate_numeric_map:
        return []
    out = []
    i = 0
    while i + 1 < len(events):
        e9 = events[i]
        e10 = events[i + 1]
        if e9.get("window_size") != 9 or e10.get("window_size") != 10:
            i += 1
            continue
        k = _normalize_ws_prefix(9, e9.get("prefix", ""))
        if k is None:
            i += 2
            continue
        v = win_rate_numeric_map.get(k)
        if v is not None and v >= min_pct_w9:
            out.append(e9)
            out.append(e10)
        i += 2
    return out


def compute_stats_from_step_events(events):
    """
    step_events 리스트에서 통계 계산 (스킵 제외한 예측만 사용).
    Returns:
        dict: total_steps, total_predictions, total_failures, total_skipped, accuracy, max_consecutive_failures
    """
    total_steps = len(events) if events else 0
    predictions = 0
    failures = 0
    skipped = 0
    max_consec = 0
    cur_consec = 0
    for e in events or []:
        if e.get("skipped"):
            skipped += 1
            continue
        predictions += 1
        if e.get("is_correct") is False or e.get("is_correct") == 0:
            failures += 1
            cur_consec += 1
            max_consec = max(max_consec, cur_consec)
        else:
            cur_consec = 0
    acc = (100.0 * (predictions - failures) / predictions) if predictions > 0 else 0.0
    return {
        "total_steps": total_steps,
        "total_predictions": predictions,
        "total_failures": failures,
        "total_skipped": skipped,
        "accuracy": acc,
        "max_consecutive_failures": max_consec,
    }


def build_history_table_rows(events, is_live=False, sim_win_rate_map=None):
    """step_events 또는 live_step_events를 테이블 행 리스트로 변환. 시뮬레이션일 때 예측 테이블의 시뮬레이션 승률 표시."""
    rows = []
    for e in events or []:
        is_correct = e.get("is_correct")
        match_status = _format_match(is_correct)
        pred_display = _format_predicted_display(e, is_live)
        conf = e.get("confidence", 0) or 0
        conf_str = f"{conf:.1f}%" if e.get("predicted") else "-"

        skipped = e.get("skipped", 0) if isinstance(e.get("skipped"), (int, float)) else (e.get("skipped") is True)
        row = {
            "Step": e.get("step", 0),
            "Position": e.get("position", ""),
            "Anchor": e.get("anchor", ""),
            "Window Size": e.get("window_size", ""),
            "Prefix": e.get("prefix", ""),
            "예측": pred_display,
            "실제값": e.get("actual", "-"),
            "일치": match_status,
            "신뢰도": conf_str,
            "예측 사유": (e.get("entry_reason", "") or "") if not skipped else "",
            "스킵 사유": e.get("skip_reason", "") or "",
        }
        if not is_live:
            row["선택 윈도우"] = e.get("selected_window_size", "")
            k = _normalize_ws_prefix(e.get("window_size"), e.get("prefix", ""))
            row["시뮬레이션 승률"] = (sim_win_rate_map.get(k, "-") if sim_win_rate_map and k else "-")
        rows.append(row)
    return rows


def get_grid_string_by_id(grid_string_id):
    """preprocessed_grid_strings에서 grid_string 조회 (change_point DB)."""
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?",
            conn,
            params=[grid_string_id],
        )
        return df.iloc[0]["grid_string"] if len(df) > 0 else None
    finally:
        conn.close()


def query_prediction_table_confidence_stats(window_sizes=(9, 10, 11)):
    """
    simulation_predictions_change_point 테이블에서
    지정 윈도우·모든 method별 신뢰도 요약 조회.
    """
    conn = get_change_point_db_connection()
    try:
        placeholders = ",".join("?" * len(window_sizes))
        q = f"""
            SELECT
                method AS method,
                window_size AS window_size,
                COUNT(*) AS record_count,
                AVG(confidence) AS avg_confidence,
                MIN(confidence) AS min_confidence,
                MAX(confidence) AS max_confidence
            FROM simulation_predictions_change_point
            WHERE window_size IN ({placeholders})
            GROUP BY method, window_size
            ORDER BY method, window_size
        """
        df = pd.read_sql_query(q, conn, params=list(window_sizes))
        return df
    except Exception as e:
        return pd.DataFrame()
    finally:
        conn.close()


def query_prediction_table_freq_weight_window_9_10(threshold=0):
    """
    simulation_predictions_change_point 테이블에서
    빈도 기반 · 가중치 기반 · 윈도우 9, 10 데이터 조회. 예측 테이블 신뢰도와 비교용.
    Returns:
        DataFrame with columns: window_size, prefix, method, predicted_value, confidence, b_ratio, p_ratio, pred_frequency, sim_win_rate_pct
    """
    conn = get_change_point_db_connection()
    try:
        cur = conn.execute("PRAGMA table_info(simulation_predictions_change_point)")
        cols = [row[1] for row in cur.fetchall()]
        has_freq = "pred_frequency" in cols
        has_sim_wr = "sim_win_rate_pct" in cols
        sel = "window_size, prefix, method, predicted_value, confidence, b_ratio, p_ratio"
        if has_freq:
            sel += ", pred_frequency"
        if has_sim_wr:
            sel += ", sim_win_rate_pct"
        q = f"""
            SELECT {sel}
            FROM simulation_predictions_change_point
            WHERE method IN ('빈도 기반', '가중치 기반') AND threshold = ? AND window_size IN (9, 10)
            ORDER BY window_size, prefix, method
        """
        df = pd.read_sql_query(q, conn, params=[threshold])
        if not has_freq:
            df["pred_frequency"] = None
        if not has_sim_wr:
            df["sim_win_rate_pct"] = None
        return df
    except Exception:
        return pd.DataFrame()
    finally:
        conn.close()


def query_prediction_table_prefix_detail(window_sizes=(9, 10, 11)):
    """
    simulation_predictions_change_point 테이블에서
    모든 prefix별 신뢰도 상세 조회. pred_frequency 컬럼이 있으면 포함.
    """
    conn = get_change_point_db_connection()
    try:
        placeholders = ",".join("?" * len(window_sizes))
        q_with_freq = f"""
            SELECT
                window_size,
                prefix,
                method,
                threshold,
                predicted_value,
                confidence,
                b_ratio,
                p_ratio,
                pred_frequency
            FROM simulation_predictions_change_point
            WHERE window_size IN ({placeholders})
            ORDER BY window_size, prefix, method
        """
        df = pd.read_sql_query(q_with_freq, conn, params=list(window_sizes))
        return df
    except Exception:
        q_no_freq = f"""
            SELECT
                window_size,
                prefix,
                method,
                threshold,
                predicted_value,
                confidence,
                b_ratio,
                p_ratio
            FROM simulation_predictions_change_point
            WHERE window_size IN ({placeholders})
            ORDER BY window_size, prefix, method
        """
        try:
            df = pd.read_sql_query(q_no_freq, conn, params=list(window_sizes))
            df["pred_frequency"] = None
            return df
        except Exception:
            return pd.DataFrame()
    finally:
        conn.close()


def _render_freq518_win50_section():
    """윈도우9 빈도+승률(first_anchor_window9_freq518_win50) 가설: 스킵 요약 + 스텝별(prefix별) 승률."""
    hypothesis_key = "first_anchor_window9_freq518_win50"
    st.markdown("### 윈도우9 빈도+승률 (freq518_win50)")

    skip_data = query_step_events_skip_summary_by_hypothesis(hypothesis_key)
    total_steps = skip_data["total_steps"]
    total_skipped = skip_data["total_skipped"]
    total_predictions = skip_data["total_predictions"]

    if total_steps == 0:
        st.info("해당 가설의 시뮬레이션 결과가 없습니다. (hypothesis_key: first_anchor_window9_freq518_win50)")
        return

    # 스킵 요약
    st.markdown("#### 스킵 요약")
    skip_pct = 100.0 * total_skipped / total_steps if total_steps > 0 else 0.0
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("전체 스텝 수", f"{total_steps:,}")
    with c2:
        st.metric("스킵 수", f"{total_skipped:,}")
    with c3:
        st.metric("실제 예측 수", f"{total_predictions:,}")
    with c4:
        st.metric("스킵 비율", f"{skip_pct:.1f}%")

    skip_reason_counts = skip_data["skip_reason_counts"]
    if skip_reason_counts:
        df_skip = pd.DataFrame(skip_reason_counts)
        total_skipped_val = df_skip["count"].sum()
        df_skip["비율 (%)"] = (100.0 * df_skip["count"] / total_skipped_val).round(1)
        df_skip = df_skip.rename(columns={"skip_reason": "스킵 사유", "count": "건수"})
        st.dataframe(df_skip[["스킵 사유", "건수", "비율 (%)"]], use_container_width=True, hide_index=True)
    else:
        st.caption("스킵 사유별 데이터 없음 (skipped=1인 행 없음)")

    # 스텝별(prefix별) 승률
    st.markdown("#### 스텝별 승률 (window_size=9, prefix별)")
    df_win = query_step_events_prefix_win_rate_by_hypothesis(hypothesis_key, (9,))
    if df_win is None or len(df_win) == 0:
        st.info("해당 가설의 스텝 승률 데이터가 없습니다. (실제 예측이 발생한 prefix가 없음)")
        return
    # 스텝별 전체 승률 요약 (모든 prefix 합산)
    overall_total = int(df_win["total"].sum())
    overall_correct = int(df_win["correct"].sum())
    overall_win_rate_pct = 100.0 * overall_correct / overall_total if overall_total > 0 else 0.0
    st.markdown("**전체 승률 요약**")
    w1, w2, w3 = st.columns(3)
    with w1:
        st.metric("총 예측 수", f"{overall_total:,}")
    with w2:
        st.metric("정답 수", f"{overall_correct:,}")
    with w3:
        st.metric("전체 승률", f"{overall_win_rate_pct:.1f}%")
    df_win = df_win.sort_values("total", ascending=False).reset_index(drop=True)
    df_display = df_win[["prefix", "total", "correct", "win_rate_pct"]].copy()
    df_display = df_display.rename(columns={"prefix": "prefix", "total": "총 횟수", "correct": "정답", "win_rate_pct": "승률 (%)"})
    df_display["승률 (%)"] = df_display["승률 (%)"].round(1)
    st.dataframe(df_display, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(df_display):,}개 prefix (스킵 제외한 실제 예측만 집계, total 내림차순)")


def _render_skipped_correct_insight_section():
    """스킵했지만 일치한 스텝 분석 — 인사이트만 표시(그래프 없음)."""
    hypothesis_key = "first_anchor_window9_freq518_win50"
    st.markdown("### 스킵했지만 일치한 스텝 분석")
    st.caption("스킵된 스텝 중 예측 평가 가능한 행(predicted/actual 있음)에 대해, 일치(is_correct=1)인 경우의 신뢰도·스킵 사유 특징을 요약합니다. 그래프 없이 인사이트만 표시합니다.")

    agg = query_skipped_correct_insight_aggregates(hypothesis_key)
    total_eval = agg["total_evaluable"]
    if total_eval == 0:
        st.info("해당 가설에서 예측 평가 가능한 스킵 스텝이 없습니다. (스킵 시 predicted/actual이 채워진 행 없음)")
        return

    total_correct = agg["total_correct"]
    total_wrong = agg["total_wrong"]
    pct_correct = 100.0 * total_correct / total_eval if total_eval else 0

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("예측 평가 가능 스킵 스텝", f"{total_eval:,}")
    with c2:
        st.metric("스킵했지만 일치", f"{total_correct:,} ({pct_correct:.1f}%)")
    with c3:
        st.metric("스킵했지만 불일치", f"{total_wrong:,}")

    if total_correct > 0:
        st.markdown("#### 일치한 스킵 구간 신뢰도")
        cc1, cc2, cc3, cc4 = st.columns(4)
        with cc1:
            st.metric("평균", f"{agg['confidence_mean_correct']:.1f}%")
        with cc2:
            st.metric("중앙값", f"{agg['confidence_median_correct']:.1f}%")
        with cc3:
            st.metric("25% 백분위", f"{agg['confidence_p25_correct']:.1f}%")
        with cc4:
            st.metric("75% 백분위", f"{agg['confidence_p75_correct']:.1f}%")

        st.markdown("#### 스킵 사유별 (일치한 스킵)")
        if agg["skip_reason_counts_correct"]:
            df_correct = pd.DataFrame(agg["skip_reason_counts_correct"])
            df_correct = df_correct.rename(columns={"skip_reason": "스킵 사유", "count": "건수", "pct": "비율 (%)"})
            st.dataframe(df_correct, use_container_width=True, hide_index=True)
        else:
            st.caption("데이터 없음")

        st.markdown("#### 인사이트")
        mean_c = agg["confidence_mean_correct"]
        top_reasons = [r["skip_reason"] for r in agg["skip_reason_counts_correct"][:3]]
        st.markdown(
            f"- 스킵했지만 예측이 맞은 경우 **{total_correct:,}건** (예측 평가 가능 스킵 대비 {pct_correct:.1f}%). "
            f"이 구간의 신뢰도 평균은 **{mean_c:.1f}%**이며, 기준(51.3%) 미만이라 스킵되었으나 실제로는 맞은 ‘놓친 예측’에 해당합니다."
        )
        st.markdown(
            f"- 일치한 스킵의 스킵 사유 상위: **{', '.join(top_reasons)}**. "
            "예측 테이블에 값 없음/빈도 기반 없음은 예측값이 없어 이 집계에 포함되지 않으며, 주로 신뢰도 부족 또는 시뮬 승률 부족 사유가 해당합니다."
        )
    else:
        st.markdown("#### 인사이트")
        st.markdown("예측 평가 가능한 스킵 스텝 중 일치(is_correct=1)인 건이 없습니다.")

    if total_wrong > 0 and agg["skip_reason_counts_wrong"]:
        st.markdown("#### 스킵 사유별 (불일치한 스킵, 대비)")
        df_wrong = pd.DataFrame(agg["skip_reason_counts_wrong"])
        df_wrong = df_wrong.rename(columns={"skip_reason": "스킵 사유", "count": "건수", "pct": "비율 (%)"})
        st.dataframe(df_wrong, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### 시뮬레이션 승률 부족 케이스 (스킵 사유가 '시뮬레이션 승률 부족'인 경우만)")
    st.caption("스킵 사유가 '시뮬레이션 승률 부족 (X% < 50%)' 형태인 스텝만 대상으로 동일한 인사이트를 요약합니다.")
    agg_sw = query_skipped_correct_insight_aggregates_sim_wr_insufficient(hypothesis_key)
    total_eval_sw = agg_sw["total_evaluable"]
    if total_eval_sw == 0:
        st.info("해당 가설에서 스킵 사유가 '시뮬레이션 승률 부족'인 예측 평가 가능 스텝이 없습니다.")
    else:
        total_correct_sw = agg_sw["total_correct"]
        total_wrong_sw = agg_sw["total_wrong"]
        pct_correct_sw = 100.0 * total_correct_sw / total_eval_sw if total_eval_sw else 0
        sc1, sc2, sc3 = st.columns(3)
        with sc1:
            st.metric("시뮬 승률 부족 스킵 (평가 가능)", f"{total_eval_sw:,}")
        with sc2:
            st.metric("일치", f"{total_correct_sw:,} ({pct_correct_sw:.1f}%)")
        with sc3:
            st.metric("불일치", f"{total_wrong_sw:,}")
        if total_correct_sw > 0:
            st.markdown("**신뢰도 (일치한 스킵)**")
            sw1, sw2, sw3, sw4 = st.columns(4)
            with sw1:
                st.metric("평균", f"{agg_sw['confidence_mean_correct']:.1f}%")
            with sw2:
                st.metric("중앙값", f"{agg_sw['confidence_median_correct']:.1f}%")
            with sw3:
                st.metric("25% 백분위", f"{agg_sw['confidence_p25_correct']:.1f}%")
            with sw4:
                st.metric("75% 백분위", f"{agg_sw['confidence_p75_correct']:.1f}%")
            st.markdown(
                f"시뮬 승률 부족으로 스킵된 스텝 중 예측이 맞은 경우 **{total_correct_sw:,}건** (해당 구간 대비 {pct_correct_sw:.1f}%). "
                f"이 구간의 신뢰도 평균 **{agg_sw['confidence_mean_correct']:.1f}%** — 신뢰도는 기준(51.3%) 이상이었으나 시뮬 승률 50% 미만으로만 스킵된 ‘놓친 예측’에 해당합니다."
            )
        else:
            st.caption("시뮬 승률 부족 스킵 중 일치(is_correct=1)인 건이 없습니다.")

    st.markdown("---")
    st.markdown("#### 스킵 사유에서 추출한 시뮬 승률 (해당 스텝 승률) 분포")
    st.caption("스킵 사유 '시뮬레이션 승률 부족 (50.0% < 52.0%)' 형식에서 **첫 번째 숫자(50.0)** — 해당 스텝의 시뮬 승률 — 만 취합해 분석합니다.")
    sim_wr_agg = query_skipped_sim_win_rates_extracted(hypothesis_key)
    if sim_wr_agg["count"] == 0:
        st.info("스킵 사유에서 시뮬 승률을 추출할 수 있는 행이 없습니다.")
    else:
        st.metric("추출한 시뮬 승률 건수", f"{sim_wr_agg['count']:,}")
        wr1, wr2, wr3, wr4 = st.columns(4)
        with wr1:
            st.metric("평균", f"{sim_wr_agg['mean']:.1f}%")
        with wr2:
            st.metric("중앙값", f"{sim_wr_agg['median']:.1f}%")
        with wr3:
            st.metric("25% 백분위", f"{sim_wr_agg['p25']:.1f}%")
        with wr4:
            st.metric("75% 백분위", f"{sim_wr_agg['p75']:.1f}%")
        bc = sim_wr_agg["by_correct"]
        st.markdown("**일치/불일치별 해당 스텝 시뮬 승률**")
        def _fmt_mm(c):
            if c["count"] == 0:
                return "—"
            return f"평균 **{c['mean']:.1f}%** (중앙값 {c['median']:.1f}%)"
        st.markdown(
            f"- 일치한 스텝: **{bc['correct']['count']:,}건**, {_fmt_mm(bc['correct'])}. "
            f"불일치한 스텝: **{bc['wrong']['count']:,}건**, {_fmt_mm(bc['wrong'])}."
        )

    st.markdown("---")
    st.markdown("#### 시뮬 승률 부족 케이스 상세: 신뢰도 구간별 시뮬 승률")
    st.caption(
        "신뢰도 구간별로 해당 스텝의 시뮬 승률(추출값) 평균·중앙값과 일치율을 표시합니다. "
        "**일치율** = (일치 건수 / 해당 구간 **스킵된 전체 스텝 수**) × 100 — 즉, 해당 구간에서 스킵된 모든 스텝을 분모로 한 비율입니다. "
        "시뮬 승률 평균·중앙값은 스킵 사유에서 추출 가능한 스텝만으로 계산합니다."
    )
    detail = query_skipped_sim_wr_insufficient_by_confidence(hypothesis_key)
    if detail["total_count"] == 0:
        st.info("시뮬 승률 부족 스킵 케이스가 없습니다.")
    else:
        if detail["summary_by_confidence_band"]:
            df_band = pd.DataFrame(detail["summary_by_confidence_band"])
            df_band = df_band.rename(columns={
                "confidence_band": "신뢰도 구간",
                "count": "스킵 전체(건수)",
                "sim_wr_valid_count": "시뮬 승률 추출 N",
                "mean_sim_win_rate": "시뮬 승률 평균(%)",
                "median_sim_win_rate": "시뮬 승률 중앙값(%)",
                "correct_count": "일치 건수",
                "correct_rate_pct": "일치율(%)",
            })
            df_band["시뮬 승률 평균(%)"] = df_band["시뮬 승률 평균(%)"].round(1)
            df_band["시뮬 승률 중앙값(%)"] = df_band["시뮬 승률 중앙값(%)"].round(1)
            st.dataframe(df_band, use_container_width=True, hide_index=True)
        high = detail["high_confidence"]
        low = detail["lower_confidence"]
        th = high["threshold"]
        st.markdown("**신뢰도 높음 vs 낮음 비교**")
        def _row_summary(r, label):
            if r["count"] == 0:
                return f"{label}: 0건"
            wr = f"시뮬 승률 평균 **{r['mean_sim_win_rate']:.1f}%** (중앙값 {r['median_sim_win_rate']:.1f}%)" if r.get("mean_sim_win_rate") is not None else "시뮬 승률 추출 없음"
            return f"{label}: **{r['count']:,}건**, {wr}, 일치율 **{r['correct_rate_pct']:.1f}%** (스킵 전체 기준)"
        st.markdown(f"- {_row_summary(high, f'신뢰도 {th}% 이상')}")
        st.markdown(f"- {_row_summary(low, f'신뢰도 {th}% 미만')}")
        if high["count"] and low["count"] and high["mean_sim_win_rate"] is not None and low["mean_sim_win_rate"] is not None:
            diff = high["mean_sim_win_rate"] - low["mean_sim_win_rate"]
            st.markdown(
                f"**인사이트**: 신뢰도가 높은 구간({th}% 이상)의 시뮬 승률 평균은 **{high['mean_sim_win_rate']:.1f}%**, "
                f"낮은 구간은 **{low['mean_sim_win_rate']:.1f}%**로, 높은 신뢰도 케이스가 시뮬 승률이 **{abs(diff):.1f}%** {'더 높음' if diff > 0 else '더 낮음' if diff < 0 else '비슷함'}."
            )


def main():
    st.title("시뮬레이션 / 라이브 결과 조회")
    st.markdown("통계, 연속 불일치 높은 결과 파악, 상세 히스토리 조회")
    st.markdown("---")

    _render_freq518_win50_section()
    st.markdown("---")
    _render_skipped_correct_insight_section()
    st.markdown("---")

    t1, t2, t3, t4, t5, t6 = st.tabs([
        "연속 불일치 높은 결과",
        "통계 대시보드",
        "상세 조회",
        "윈도우 9·10 전용 · 연속 일치",
        "윈도우 9·10 전용 · 연속 불일치",
        "승률 기준 결과 조회",
    ])

    with t1:
        _render_high_failure_tab()
    with t2:
        _render_stats_tab()
    with t3:
        _render_detail_tab()
    with t4:
        _render_window910_consecutive_match_tab()
    with t5:
        _render_window910_consecutive_failure_tab()
    with t6:
        _render_win_rate_filter_tab()

    st.markdown("---")
    st.markdown("## 예측 테이블 신뢰도")
    _render_prediction_table_confidence_section()

    _render_prediction_table_freq_w9_w10()


def _render_high_failure_tab():
    """연속 불일치 높은 결과 탭."""
    st.markdown("### 연속 불일치 높은 결과")
    st.caption("문제 케이스를 우선적으로 파악합니다.")

    run_type = st.radio("Run 유형", ["시뮬레이션", "라이브"], horizontal=True, key="hf_run_type")

    min_mcf_opts = [(0, "0회 이상"), (1, "1회 이상"), (2, "2회 이상"), (3, "3회 이상"), (4, "4회 이상"), (5, "5회 이상")]
    min_mcf = st.selectbox(
        "최소 연속 불일치",
        [o[0] for o in min_mcf_opts],
        format_func=lambda x: next((o[1] for o in min_mcf_opts if o[0] == x), str(x)),
        index=1,
        key="hf_min_mcf",
    )

    hypothesis_key = None
    if run_type == "시뮬레이션":
        hyp_keys = query_simulation_hypothesis_keys()
        hyp_opts = [None] + hyp_keys
        hyp_idx = st.selectbox(
            "가설 필터",
            range(len(hyp_opts)),
            format_func=lambda i: "전체" if hyp_opts[i] is None else hyp_opts[i],
            key="hf_hypothesis",
        )
        hypothesis_key = hyp_opts[hyp_idx]

    if st.button("조회", key="hf_query"):
        if run_type == "시뮬레이션":
            st.session_state["hf_sim_results"] = query_simulation_high_failure_results(
                min_max_consecutive_failures=min_mcf,
                limit=100,
                hypothesis_key=hypothesis_key,
            )
            st.session_state["hf_run_type"] = "sim"
        else:
            st.session_state["hf_live_results"] = query_live_high_failure_results(
                min_max_consecutive_failures=min_mcf,
                limit=100,
            )
            st.session_state["hf_run_type"] = "live"

    if run_type == "시뮬레이션" and "hf_sim_results" in st.session_state:
        results = st.session_state["hf_sim_results"]
        if not results:
            st.info("조건에 맞는 시뮬레이션 결과가 없습니다.")
        else:
            st.markdown(f"#### 시뮬레이션 결과 ({len(results)}건)")
            df = pd.DataFrame([
                {
                    "run_id": r.get("run_id", ""),
                    "hypothesis": r.get("hypothesis_key", ""),
                    "cutoff": r.get("cutoff_grid_string_id", ""),
                    "grid_string_id": r.get("grid_string_id", ""),
                    "최대연속불일치": r.get("max_consecutive_failures", 0),
                    "정확도": f"{r.get('accuracy', 0):.2f}%",
                    "예측수": r.get("total_predictions", 0),
                    "스킵": r.get("total_skipped", 0),
                    "created_at": str(r.get("created_at", ""))[:19],
                }
                for r in results
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            selected_idx = st.selectbox(
                "상세 조회할 행 선택",
                range(len(results)),
                format_func=lambda i: f"run={str(results[i].get('run_id',''))[:8]}... gid={results[i].get('grid_string_id')} mcf={results[i].get('max_consecutive_failures')}",
                key="hf_sim_row_select",
            )
            if st.button("상세 조회", key="hf_sim_go_detail"):
                r = results[selected_idx]
                st.session_state["detail_run_id"] = r.get("run_id")
                st.session_state["detail_run_type"] = "sim"
                st.session_state["detail_grid_string_id"] = r.get("grid_string_id")
                st.rerun()

    elif run_type == "라이브" and "hf_live_results" in st.session_state:
        results = st.session_state["hf_live_results"]
        if not results:
            st.info("조건에 맞는 라이브 결과가 없습니다.")
        else:
            st.markdown(f"#### 라이브 결과 ({len(results)}건)")
            df = pd.DataFrame([
                {
                    "run_id": r.get("run_id", ""),
                    "최대연속불일치": r.get("max_consecutive_failures", 0),
                    "정확도": f"{r.get('accuracy', 0):.2f}%",
                    "예측수": r.get("total_predictions", 0),
                    "grid_string 미리보기": (r.get("grid_string_preview", "") or "")[:50],
                    "created_at": str(r.get("created_at", ""))[:19],
                }
                for r in results
            ])
            st.dataframe(df, use_container_width=True, hide_index=True)

            selected_idx = st.selectbox(
                "상세 조회할 행 선택",
                range(len(results)),
                format_func=lambda i: f"run={str(results[i].get('run_id',''))[:8]}... mcf={results[i].get('max_consecutive_failures')}",
                key="hf_live_row_select",
            )
            if st.button("상세 조회", key="hf_live_go_detail"):
                r = results[selected_idx]
                st.session_state["detail_run_id"] = r.get("run_id")
                st.session_state["detail_run_type"] = "live"
                st.session_state["detail_grid_string_id"] = None
                st.rerun()


def _render_stats_tab():
    """통계 대시보드 탭."""
    st.markdown("### 통계 대시보드")

    sim_stats = query_simulation_stats()
    live_stats = query_live_stats()

    st.markdown("#### 시뮬레이션")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("총 Run 수", sim_stats.get("total_runs", 0))
    with c2:
        st.metric("총 Grid Result 수", sim_stats.get("total_grid_results", 0))
    with c3:
        st.metric("평균 정확도", f"{sim_stats.get('avg_accuracy_overall', 0):.2f}%")
    with c4:
        st.metric("평균 최대 연속 불일치", f"{sim_stats.get('avg_max_consecutive_failures', 0):.2f}")
    with c5:
        st.metric("최악 최대 연속 불일치", sim_stats.get("worst_max_consecutive_failures", 0))

    dist = sim_stats.get("max_consecutive_failures_dist", {})
    if dist:
        dist_sorted = sorted(dist.items())
        labels = [f"{k}회" for k, _ in dist_sorted]
        values = [v for _, v in dist_sorted]
        if values:
            st.bar_chart(pd.DataFrame({"개수": values}, index=labels))
        st.caption("연속 불일치 분포 (grid_results 기준)")

    st.markdown("---")
    st.markdown("#### 라이브")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.metric("총 Run 수", live_stats.get("total_runs", 0))
    with c2:
        st.metric("평균 정확도", f"{live_stats.get('avg_accuracy_overall', 0):.2f}%")
    with c3:
        st.metric("평균 최대 연속 불일치", f"{live_stats.get('avg_max_consecutive_failures', 0):.2f}")
    with c4:
        st.metric("최악 최대 연속 불일치", live_stats.get("worst_max_consecutive_failures", 0))

    dist_live = live_stats.get("max_consecutive_failures_dist", {})
    if dist_live:
        dist_sorted = sorted(dist_live.items())
        labels = [f"{k}회" for k, _ in dist_sorted]
        values = [v for _, v in dist_sorted]
        if values:
            st.bar_chart(pd.DataFrame({"개수": values}, index=labels))
        st.caption("연속 불일치 분포 (live_run_summary 기준)")


def _render_detail_tab():
    """상세 조회 탭. detail_run_id가 있으면 자동 표시."""
    st.markdown("### 상세 조회")

    # 탭 1에서 선택된 경우 자동 표시
    detail_run_id = st.session_state.get("detail_run_id")
    detail_run_type = st.session_state.get("detail_run_type")
    detail_grid_string_id = st.session_state.get("detail_grid_string_id")

    if detail_run_id:
        st.info(f"선택된 Run: {detail_run_id} ({detail_run_type})")
        run_id = detail_run_id
        run_type = "시뮬레이션" if detail_run_type == "sim" else "라이브"
    else:
        run_type = st.radio("Run 유형", ["시뮬레이션", "라이브"], horizontal=True, key="dt_run_type")
        if run_type == "시뮬레이션":
            sim_runs = query_simulation_runs_list(limit=50)
            if sim_runs:
                opts = [(r["run_id"], f"{r['run_id']} | {r.get('hypothesis_key','')} | {r.get('created_at','')}") for r in sim_runs]
                idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="dt_sim_select")
                run_id = opts[idx][0]
            else:
                run_id = st.text_input("Run ID 입력", placeholder="UUID", key="dt_sim_input")
        else:
            live_runs = query_live_runs_list(limit=50)
            if live_runs:
                opts = [(r["run_id"], f"{r['run_id']} | {r.get('created_at','')} | {r.get('grid_string_preview','')[:40]}") for r in live_runs]
                idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="dt_live_select")
                run_id = opts[idx][0]
            else:
                run_id = st.text_input("Run ID 입력", placeholder="UUID", key="dt_live_input")

    if st.button("조회", key="dt_query") and run_id:
        if run_type == "시뮬레이션":
            detail = query_simulation_run_detail(run_id)
            st.session_state["run_detail"] = detail
            st.session_state["run_detail_type"] = "sim"
        else:
            detail = query_live_run_detail(run_id)
            st.session_state["run_detail"] = detail
            st.session_state["run_detail_type"] = "live"
        if detail_run_id:
            st.session_state.pop("detail_run_id", None)
            st.session_state.pop("detail_run_type", None)
            st.session_state.pop("detail_grid_string_id", None)
        st.rerun()

    # detail_run_id가 있고 아직 조회하지 않은 경우 자동 조회
    if detail_run_id and "run_detail" not in st.session_state and run_id:
        if detail_run_type == "sim":
            detail = query_simulation_run_detail(run_id)
        else:
            detail = query_live_run_detail(run_id)
        if detail:
            st.session_state["run_detail"] = detail
            st.session_state["run_detail_type"] = detail_run_type or "sim"
            st.session_state.pop("detail_run_id", None)
            st.session_state.pop("detail_run_type", None)
            st.session_state.pop("detail_grid_string_id", None)
            st.rerun()

    if "run_detail" in st.session_state:
        detail = st.session_state["run_detail"]
        is_live = st.session_state.get("run_detail_type") == "live"

        if detail is None:
            st.warning("해당 run_id를 찾을 수 없습니다.")
        else:
            meta = detail.get("run_meta", {})
            sm = detail.get("run_summary", {})

            st.markdown("#### Run 메타")
            st.json({k: str(v) for k, v in meta.items()})

            st.markdown("#### 요약")
            if is_live:
                c1, c2, c3, c4, c5 = st.columns(5)
                with c1:
                    st.metric("총 스텝", sm.get("total_steps", 0))
                with c2:
                    st.metric("총 예측", sm.get("total_predictions", 0))
                with c3:
                    st.metric("총 실패", sm.get("total_failures", 0))
                with c4:
                    st.metric("스킵", sm.get("total_skipped", 0))
                with c5:
                    st.metric("정확도", f"{sm.get('accuracy', 0):.2f}%")
                if meta.get("grid_string"):
                    st.markdown("#### Grid String")
                    st.code(meta.get("grid_string", ""))
                events = detail.get("step_events", [])
            else:
                grid_results = detail.get("grid_results", [])
                st.markdown(f"#### Grid Results ({len(grid_results)}건)")
                if grid_results:
                    gr_df = pd.DataFrame([
                        {
                            "grid_string_id": r.get("grid_string_id"),
                            "accuracy": r.get("accuracy"),
                            "max_consecutive_failures": r.get("max_consecutive_failures"),
                            "total_predictions": r.get("total_predictions"),
                            "total_skipped": r.get("total_skipped"),
                        }
                        for r in grid_results
                    ])
                    st.dataframe(gr_df, use_container_width=True, hide_index=True)

                    gid_options = [r.get("grid_string_id") for r in grid_results]
                    default_idx = gid_options.index(detail_grid_string_id) if (detail_grid_string_id is not None and detail_grid_string_id in gid_options) else 0
                    selected_gid = st.selectbox(
                        "상세 히스토리 조회할 grid_string_id 선택",
                        gid_options,
                        format_func=lambda x: str(x),
                        index=default_idx,
                        key="dt_gid_select",
                    )
                    if selected_gid is not None:
                        events = query_simulation_step_events(meta.get("run_id"), selected_gid)
                    else:
                        events = []
                else:
                    events = []
                    st.info("grid_results가 없습니다.")

            if events:
                st.markdown("#### 상세 히스토리")
                sim_win_rate_map = query_sim_win_rate_from_predictions_table(events) if not is_live else None
                rows = build_history_table_rows(events, is_live=is_live, sim_win_rate_map=sim_win_rate_map)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption(f"총 {len(events)}개 스텝")

                if not is_live:
                    st.markdown("#### 시뮬레이션 승률 기준 필터")
                    min_win_rate = st.number_input(
                        "승률 기준 (%) 이상인 스텝만 사용",
                        min_value=0,
                        max_value=100,
                        value=0,
                        step=1,
                        key="dt_win_rate_filter",
                        help="예측 테이블에 저장된 (window_size, prefix)별 시뮬레이션 승률이 이 값 이상인 스텝만 통계·테이블에 반영",
                    )
                    if min_win_rate > 0:
                        win_rate_numeric = query_sim_win_rate_numeric_from_predictions_table(events)
                        filtered = filter_events_by_sim_win_rate(events, win_rate_numeric, min_win_rate)
                        if filtered:
                            st.markdown(f"**필터 적용 통계** (승률 {min_win_rate}% 이상 스텝만, {len(filtered)}개)")
                            fstats = compute_stats_from_step_events(filtered)
                            fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                            with fc1:
                                st.metric("필터된 스텝 수", len(filtered))
                            with fc2:
                                st.metric("최대 연속 불일치", fstats["max_consecutive_failures"])
                            with fc3:
                                st.metric("정확도", f"{fstats['accuracy']:.2f}%")
                            with fc4:
                                st.metric("총 예측", fstats["total_predictions"])
                            with fc5:
                                st.metric("총 실패", fstats["total_failures"])
                            st.markdown("##### 필터된 상세 히스토리")
                            f_map = query_sim_win_rate_from_predictions_table(filtered)
                            f_rows = build_history_table_rows(filtered, is_live=False, sim_win_rate_map=f_map)
                            st.dataframe(pd.DataFrame(f_rows), use_container_width=True, hide_index=True)
                        else:
                            st.info(f"승률 **{min_win_rate}%** 이상인 스텝이 없습니다. (예측 테이블의 시뮬레이션 승률 기준)")

    # grid_string_id 직접 조회 모드
    st.markdown("---")
    st.markdown("#### grid_string_id로 조회 (시뮬레이션)")
    gid_input = st.number_input("Grid String ID", min_value=1, value=1, step=1, key="dt_gid_input")
    if st.button("조회 (grid_string_id)", key="dt_gid_query"):
        runs = query_simulation_by_grid_string_id(int(gid_input))
        st.session_state["gid_results"] = runs
        st.session_state["gid_value"] = int(gid_input)

    if "gid_results" in st.session_state and st.session_state.get("gid_value") == gid_input:
        runs = st.session_state["gid_results"]
        if not runs:
            st.info(f"grid_string_id {gid_input}에 대한 시뮬레이션 결과가 없습니다.")
        else:
            grid_string = get_grid_string_by_id(gid_input)
            if grid_string:
                st.markdown("##### Grid String")
                st.code(grid_string)
            run_options = [(r.get("run_id"), f"{r.get('run_id','')[:8]}... | {r.get('run_meta',{}).get('hypothesis_key','')}") for r in runs]
            idx = st.selectbox("Run 선택", range(len(run_options)), format_func=lambda i: run_options[i][1], key="dt_gid_run_select")
            selected_run_id = run_options[idx][0]
            selected = runs[idx]
            gr = selected.get("grid_result", {})
            st.metric("정확도", f"{gr.get('accuracy', 0):.2f}%")
            events = query_simulation_step_events(selected_run_id, gid_input)
            if events:
                sim_win_rate_map = query_sim_win_rate_from_predictions_table(events)
                rows = build_history_table_rows(events, is_live=False, sim_win_rate_map=sim_win_rate_map)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.markdown("##### 시뮬레이션 승률 기준 필터")
                min_win_rate_gid = st.number_input(
                    "승률 기준 (%) 이상인 스텝만",
                    min_value=0,
                    max_value=100,
                    value=0,
                    step=1,
                    key="dt_gid_win_rate_filter",
                )
                if min_win_rate_gid > 0:
                    win_rate_numeric = query_sim_win_rate_numeric_from_predictions_table(events)
                    filtered = filter_events_by_sim_win_rate(events, win_rate_numeric, min_win_rate_gid)
                    if filtered:
                        st.markdown(f"**필터 적용 통계** (승률 {min_win_rate_gid}% 이상, {len(filtered)}개 스텝)")
                        fstats = compute_stats_from_step_events(filtered)
                        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                        with fc1:
                            st.metric("필터된 스텝 수", len(filtered))
                        with fc2:
                            st.metric("최대 연속 불일치", fstats["max_consecutive_failures"])
                        with fc3:
                            st.metric("정확도", f"{fstats['accuracy']:.2f}%")
                        with fc4:
                            st.metric("총 예측", fstats["total_predictions"])
                        with fc5:
                            st.metric("총 실패", fstats["total_failures"])
                        f_map = query_sim_win_rate_from_predictions_table(filtered)
                        f_rows = build_history_table_rows(filtered, is_live=False, sim_win_rate_map=f_map)
                        st.dataframe(pd.DataFrame(f_rows), use_container_width=True, hide_index=True)
                    else:
                        st.info(f"승률 {min_win_rate_gid}% 이상인 스텝이 없습니다.")


def _render_window910_consecutive_match_tab():
    """윈도우 9·10 전용 가설(first_anchor_window9_10) 결과만 조회 · 연속 일치 통계 및 상위 10개 스트링."""
    st.markdown("### 윈도우 9·10 전용 · 연속 일치")
    st.caption("가설 `first_anchor_window9_10` 시뮬레이션 결과만 대상으로, 연속 일치(consecutive correct) 통계와 연속 일치가 가장 많은 스트링 상위 10건을 표시합니다.")

    hypothesis_key = "first_anchor_window9_10"

    stats = query_simulation_consecutive_match_stats(hypothesis_key)
    total = stats.get("total_grid_results", 0)
    if total == 0:
        st.info("해당 가설의 시뮬레이션 결과가 없습니다. (hypothesis_key: first_anchor_window9_10)")
        return

    st.markdown("#### 연속 일치 통계")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("총 Grid Result 수", total)
    with c2:
        st.metric("평균 연속 일치(최대)", f"{stats.get('avg_max_consecutive_matches', 0):.2f}회")
    with c3:
        st.metric("최고 연속 일치", f"{stats.get('best_max_consecutive_matches', 0)}회")
    with c4:
        st.metric("평균 정확도", f"{stats.get('avg_accuracy_overall', 0):.2f}%")
    with c5:
        st.metric("가설", hypothesis_key)

    dist = stats.get("max_consecutive_matches_dist", {})
    if dist:
        dist_sorted = sorted(dist.items())
        labels = [f"{k}회" for k, _ in dist_sorted]
        values = [v for _, v in dist_sorted]
        if values:
            st.bar_chart(pd.DataFrame({"개수": values}, index=labels))
        st.caption("연속 일치(최대) 분포 — grid_string 단위")

    st.markdown("#### 연속 일치가 가장 많은 스트링 (상위 10건)")
    top_list = query_simulation_top_consecutive_matches(hypothesis_key, limit=10)
    if not top_list:
        st.info("데이터가 없습니다.")
    else:
        table_df = pd.DataFrame([
            {
                "run_id": r.get("run_id", ""),
                "grid_string_id": r.get("grid_string_id"),
                "최대 연속 일치": r.get("max_consecutive_matches", 0),
                "정확도 (%)": f"{r.get('accuracy', 0):.2f}" if r.get("accuracy") is not None else "-",
                "총 예측": r.get("total_predictions", 0),
                "스킵": r.get("total_skipped", 0),
                "실패": r.get("total_failures", 0),
                "created_at": str(r.get("created_at", ""))[:19] if r.get("created_at") else "-",
            }
            for r in top_list
        ])
        st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### grid_string_id로 상세 히스토리 조회")
    gid_input = st.number_input(
        "Grid String ID",
        min_value=1,
        value=st.session_state.get("w910_gid_value", 1),
        step=1,
        key="w910_gid_input",
    )
    if st.button("상세 히스토리 검색", key="w910_gid_query"):
        runs = query_simulation_by_grid_string_id(int(gid_input))
        runs = [r for r in runs if (r.get("run_meta") or {}).get("hypothesis_key") == hypothesis_key]
        st.session_state["w910_gid_results"] = runs
        st.session_state["w910_gid_value"] = int(gid_input)

    if "w910_gid_results" in st.session_state and st.session_state.get("w910_gid_value") == gid_input:
        runs = st.session_state["w910_gid_results"]
        if not runs:
            st.info(f"grid_string_id **{gid_input}**에 대한 윈도우 9·10 전용 시뮬레이션 결과가 없습니다.")
        else:
            grid_string = get_grid_string_by_id(gid_input)
            if grid_string:
                st.markdown("##### Grid String")
                st.code(grid_string)
            run_id = runs[0]["run_id"] if len(runs) == 1 else None
            if run_id is None:
                opts = [(r["run_id"], f"{r['run_id'][:8]}... | {r.get('run_meta', {}).get('created_at', '')}") for r in runs]
                idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="w910_run_select")
                run_id = opts[idx][0]
            events = query_simulation_step_events(run_id, gid_input)
            if not events:
                st.warning("해당 run에 대한 step_events가 없습니다.")
            else:
                st.markdown("##### 상세 히스토리")
                sim_win_rate_map = query_sim_win_rate_from_predictions_table(events)
                rows = build_history_table_rows(events, is_live=False, sim_win_rate_map=sim_win_rate_map)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption(f"총 {len(events)}개 스텝 (run_id: {run_id})")
                st.markdown("##### 시뮬레이션 승률 기준 필터")
                min_win_rate_w910 = st.number_input(
                    "승률 기준 (%) 이상인 스텝만",
                    min_value=0,
                    max_value=100,
                    value=0,
                    step=1,
                    key="w910_match_win_rate_filter",
                )
                if min_win_rate_w910 > 0:
                    win_rate_numeric = query_sim_win_rate_numeric_from_predictions_table(events)
                    filtered = filter_events_by_sim_win_rate(events, win_rate_numeric, min_win_rate_w910)
                    if filtered:
                        st.markdown(f"**필터 적용 통계** (승률 {min_win_rate_w910}% 이상, {len(filtered)}개 스텝)")
                        fstats = compute_stats_from_step_events(filtered)
                        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                        with fc1:
                            st.metric("필터된 스텝 수", len(filtered))
                        with fc2:
                            st.metric("최대 연속 불일치", fstats["max_consecutive_failures"])
                        with fc3:
                            st.metric("정확도", f"{fstats['accuracy']:.2f}%")
                        with fc4:
                            st.metric("총 예측", fstats["total_predictions"])
                        with fc5:
                            st.metric("총 실패", fstats["total_failures"])
                        f_map = query_sim_win_rate_from_predictions_table(filtered)
                        f_rows = build_history_table_rows(filtered, is_live=False, sim_win_rate_map=f_map)
                        st.dataframe(pd.DataFrame(f_rows), use_container_width=True, hide_index=True)
                    else:
                        st.info(f"승률 {min_win_rate_w910}% 이상인 스텝이 없습니다.")


def _render_window910_consecutive_failure_tab():
    """윈도우 9·10 전용 가설(first_anchor_window9_10) 결과만 조회 · 연속 불일치 통계 및 상위 10개 스트링 + grid_string_id 상세 조회."""
    st.markdown("### 윈도우 9·10 전용 · 연속 불일치")
    st.caption("가설 `first_anchor_window9_10` 시뮬레이션 결과만 대상으로, 연속 불일치(consecutive failures) 통계와 연속 불일치가 가장 많은 스트링 상위 10건을 표시합니다.")

    hypothesis_key = "first_anchor_window9_10"

    stats = query_simulation_consecutive_failure_stats(hypothesis_key)
    total = stats.get("total_grid_results", 0)
    if total == 0:
        st.info("해당 가설의 시뮬레이션 결과가 없습니다. (hypothesis_key: first_anchor_window9_10)")
        return

    st.markdown("#### 연속 불일치 통계")
    c1, c2, c3, c4, c5 = st.columns(5)
    with c1:
        st.metric("총 Grid Result 수", total)
    with c2:
        st.metric("평균 연속 불일치(최대)", f"{stats.get('avg_max_consecutive_failures', 0):.2f}회")
    with c3:
        st.metric("최악 연속 불일치", f"{stats.get('worst_max_consecutive_failures', 0)}회")
    with c4:
        st.metric("평균 정확도", f"{stats.get('avg_accuracy_overall', 0):.2f}%")
    with c5:
        st.metric("가설", hypothesis_key)

    dist = stats.get("max_consecutive_failures_dist", {})
    if dist:
        dist_sorted = sorted(dist.items())
        labels = [f"{k}회" for k, _ in dist_sorted]
        values = [v for _, v in dist_sorted]
        if values:
            st.bar_chart(pd.DataFrame({"개수": values}, index=labels))
        st.caption("연속 불일치(최대) 분포 — grid_string 단위")

    st.markdown("#### 연속 불일치가 가장 많은 스트링 (상위 10건)")
    top_list = query_simulation_high_failure_results(
        min_max_consecutive_failures=0,
        limit=10,
        hypothesis_key=hypothesis_key,
    )
    if not top_list:
        st.info("데이터가 없습니다.")
    else:
        table_df = pd.DataFrame([
            {
                "run_id": r.get("run_id", ""),
                "grid_string_id": r.get("grid_string_id"),
                "최대 연속 불일치": r.get("max_consecutive_failures", 0),
                "정확도 (%)": f"{r.get('accuracy', 0):.2f}" if r.get("accuracy") is not None else "-",
                "총 예측": r.get("total_predictions", 0),
                "스킵": r.get("total_skipped", 0),
                "실패": r.get("total_failures", 0),
                "created_at": str(r.get("created_at", ""))[:19] if r.get("created_at") else "-",
            }
            for r in top_list
        ])
        st.dataframe(table_df, use_container_width=True, hide_index=True)

    st.markdown("---")
    st.markdown("#### grid_string_id로 상세 히스토리 조회")
    gid_input = st.number_input(
        "Grid String ID",
        min_value=1,
        value=st.session_state.get("w910_fail_gid_value", 1),
        step=1,
        key="w910_fail_gid_input",
    )
    if st.button("상세 히스토리 검색", key="w910_fail_gid_query"):
        runs = query_simulation_by_grid_string_id(int(gid_input))
        runs = [r for r in runs if (r.get("run_meta") or {}).get("hypothesis_key") == hypothesis_key]
        st.session_state["w910_fail_gid_results"] = runs
        st.session_state["w910_fail_gid_value"] = int(gid_input)

    if "w910_fail_gid_results" in st.session_state and st.session_state.get("w910_fail_gid_value") == gid_input:
        runs = st.session_state["w910_fail_gid_results"]
        if not runs:
            st.info(f"grid_string_id **{gid_input}**에 대한 윈도우 9·10 전용 시뮬레이션 결과가 없습니다.")
        else:
            grid_string = get_grid_string_by_id(gid_input)
            if grid_string:
                st.markdown("##### Grid String")
                st.code(grid_string)
            run_id = runs[0]["run_id"] if len(runs) == 1 else None
            if run_id is None:
                opts = [(r["run_id"], f"{r['run_id'][:8]}... | {r.get('run_meta', {}).get('created_at', '')}") for r in runs]
                idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="w910_fail_run_select")
                run_id = opts[idx][0]
            events = query_simulation_step_events(run_id, gid_input)
            if not events:
                st.warning("해당 run에 대한 step_events가 없습니다.")
            else:
                st.markdown("##### 상세 히스토리")
                sim_win_rate_map = query_sim_win_rate_from_predictions_table(events)
                rows = build_history_table_rows(events, is_live=False, sim_win_rate_map=sim_win_rate_map)
                st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
                st.caption(f"총 {len(events)}개 스텝 (run_id: {run_id})")
                st.markdown("##### 시뮬레이션 승률 기준 필터")
                min_win_rate_fail = st.number_input(
                    "승률 기준 (%) 이상인 스텝만",
                    min_value=0,
                    max_value=100,
                    value=0,
                    step=1,
                    key="w910_fail_win_rate_filter",
                )
                if min_win_rate_fail > 0:
                    win_rate_numeric = query_sim_win_rate_numeric_from_predictions_table(events)
                    filtered = filter_events_by_sim_win_rate(events, win_rate_numeric, min_win_rate_fail)
                    if filtered:
                        st.markdown(f"**필터 적용 통계** (승률 {min_win_rate_fail}% 이상, {len(filtered)}개 스텝)")
                        fstats = compute_stats_from_step_events(filtered)
                        fc1, fc2, fc3, fc4, fc5 = st.columns(5)
                        with fc1:
                            st.metric("필터된 스텝 수", len(filtered))
                        with fc2:
                            st.metric("최대 연속 불일치", fstats["max_consecutive_failures"])
                        with fc3:
                            st.metric("정확도", f"{fstats['accuracy']:.2f}%")
                        with fc4:
                            st.metric("총 예측", fstats["total_predictions"])
                        with fc5:
                            st.metric("총 실패", fstats["total_failures"])
                        f_map = query_sim_win_rate_from_predictions_table(filtered)
                        f_rows = build_history_table_rows(filtered, is_live=False, sim_win_rate_map=f_map)
                        st.dataframe(pd.DataFrame(f_rows), use_container_width=True, hide_index=True)
                    else:
                        st.info(f"승률 {min_win_rate_fail}% 이상인 스텝이 없습니다.")


def _render_win_rate_filter_tab():
    """
    시나리오: 1) 시뮬레이션 승률 기준 설정 → 2) 결과 조회 → 3) 전체 스트링별 스텝을 해당 기준으로 필터링 → 4) 통계 요약 표시
    """
    st.markdown("### 승률 기준 결과 조회")
    st.caption("(윈도우9, 윈도우10) 쌍 단위: 윈도우9가 기준 이상이면 해당 쌍만 포함하고, 윈도우9 충족 시 해당 쌍의 윈도우10은 무조건 포함.")

    min_win_rate_w9 = st.number_input(
        "1. 윈도우 9 승률 기준 (%)",
        min_value=0,
        max_value=100,
        value=56,
        step=1,
        key="wr_filter_min_pct_w9",
        help="윈도우9가 이 값 이상인 (윈도우9, 윈도우10) 쌍만 포함. 윈도우9 충족 시 해당 쌍의 윈도우10은 무조건 포함. 0이면 필터 없이 전체 포함.",
    )
    hyp_keys = [None] + query_simulation_hypothesis_keys()
    default_hyp_idx = hyp_keys.index("first_anchor_window9_10") if "first_anchor_window9_10" in hyp_keys else 0
    hyp_idx = st.selectbox(
        "2. 가설 필터",
        range(len(hyp_keys)),
        format_func=lambda i: "전체" if hyp_keys[i] is None else hyp_keys[i],
        index=default_hyp_idx,
        key="wr_filter_hyp",
    )
    hypothesis_key = hyp_keys[hyp_idx]
    run_limit_input = st.number_input(
        "최근 Run 개수 (0 = 전체)",
        min_value=0,
        max_value=500,
        value=0,
        step=1,
        key="wr_filter_run_limit",
        help="0이면 조건에 맞는 run 전체를 조회합니다.",
    )
    run_limit = int(run_limit_input) if run_limit_input and run_limit_input > 0 else 9999

    if st.button("3. 결과 조회", key="wr_filter_query", type="primary"):
        if min_win_rate_w9 is None or min_win_rate_w9 < 0:
            st.warning("승률 기준을 0 이상으로 설정하세요.")
        else:
            runs = query_simulation_runs_list(limit=min(run_limit, 2000))
            if hypothesis_key:
                runs = [r for r in runs if r.get("hypothesis_key") == hypothesis_key]
            runs = runs[:run_limit]
            if not runs:
                st.info("조건에 맞는 run이 없습니다.")
            else:
                rows = []
                with st.status("스트링별 스텝 필터링 및 통계 계산 중...") as status:
                    for run in runs:
                        run_id = run.get("run_id")
                        detail = query_simulation_run_detail(run_id)
                        if not detail or not detail.get("grid_results"):
                            continue
                        for gr in detail["grid_results"]:
                            gid = gr.get("grid_string_id")
                            events = query_simulation_step_events(run_id, gid)
                            if not events:
                                continue
                            win_rate_numeric = query_sim_win_rate_numeric_from_predictions_table(events)
                            filtered = filter_events_by_w9_w10_pairs(
                                events, win_rate_numeric, min_win_rate_w9
                            )
                            if not filtered:
                                rows.append({
                                    "run_id": run_id[:8] + "…",
                                    "run_id_full": run_id,
                                    "grid_string_id": gid,
                                    "원본 스텝 수": len(events),
                                    "필터된 스텝 수": 0,
                                    "최대 연속 불일치": 0,
                                    "정확도 (%)": "-",
                                    "총 예측": 0,
                                    "총 실패": 0,
                                })
                                continue
                            fstats = compute_stats_from_step_events(filtered)
                            rows.append({
                                "run_id": run_id[:8] + "…",
                                "run_id_full": run_id,
                                "grid_string_id": gid,
                                "원본 스텝 수": len(events),
                                "필터된 스텝 수": len(filtered),
                                "최대 연속 불일치": fstats["max_consecutive_failures"],
                                "정확도 (%)": f"{fstats['accuracy']:.2f}",
                                "총 예측": fstats["total_predictions"],
                                "총 실패": fstats["total_failures"],
                            })
                    status.update(label="완료", state="complete")
                st.session_state["wr_filter_rows"] = rows
                st.session_state["wr_filter_min_pct_w9_used"] = min_win_rate_w9
                st.rerun()

    if "wr_filter_rows" in st.session_state:
        rows = st.session_state["wr_filter_rows"]
        used_w9 = st.session_state.get("wr_filter_min_pct_w9_used", 56)
        filter_label = f"윈도우 9 {used_w9}% 이상인 쌍만 (윈도우10은 선행 윈도우9 충족 시 포함)" if used_w9 and used_w9 > 0 else "필터 없음 (전체)"
        st.markdown(f"#### 4. 통계 요약 (**{filter_label}**)")

        if not rows:
            st.info("필터된 결과가 없습니다.")
        else:
            df = pd.DataFrame(rows)
            total_grids = len(df)
            filtered_nonzero = df[df["필터된 스텝 수"] > 0]
            if len(filtered_nonzero) == 0:
                st.warning("승률 기준 이상인 스텝이 하나도 없는 스트링만 있습니다.")
            else:
                count_max_fail_0 = int((filtered_nonzero["최대 연속 불일치"] == 0).sum())
                c1, c2, c3, c4, c5, c6 = st.columns(6)
                with c1:
                    st.metric("총 Grid 수", total_grids)
                with c2:
                    st.metric("필터 적용된 Grid 수", len(filtered_nonzero))
                with c3:
                    acc_series = pd.to_numeric(filtered_nonzero["정확도 (%)"], errors="coerce")
                    avg_acc = acc_series.mean()
                    st.metric("평균 정확도 (%)", f"{avg_acc:.2f}" if not pd.isna(avg_acc) else "-")
                with c4:
                    worst = int(filtered_nonzero["최대 연속 불일치"].max())
                    st.metric("최악 최대 연속 불일치", worst)
                with c5:
                    st.metric("최대 연속 불일치 0인 케이스", count_max_fail_0)
                with c6:
                    wr_criterion = f"W9 {used_w9}% 이상 쌍" if used_w9 and used_w9 > 0 else "전체"
                    st.metric("승률 기준", wr_criterion)

                dist = filtered_nonzero["최대 연속 불일치"].value_counts().sort_index()
                if len(dist) > 0:
                    st.bar_chart(pd.DataFrame({"개수": dist}), use_container_width=True)
                    st.caption("최대 연속 불일치 분포 (승률 기준 적용 스트링만)")

            st.markdown("#### 스트링별 결과")
            display_df = df.drop(columns=["run_id_full"], errors="ignore")
            total_rows = len(display_df)
            filtered_count = len(df[df["필터된 스텝 수"] > 0]) if "필터된 스텝 수" in df.columns else total_rows
            st.caption(f"전체 **{total_rows}**건 (필터 적용된 Grid: **{filtered_count}**건)")
            st.dataframe(display_df, use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("#### grid_string_id로 필터된 상세 히스토리 조회")
            wr_gid = st.number_input(
                "Grid String ID",
                min_value=1,
                value=st.session_state.get("wr_filter_gid", 1),
                step=1,
                key="wr_filter_gid_input",
            )
            if st.button("필터된 상세 히스토리 조회", key="wr_filter_gid_btn"):
                st.session_state["wr_filter_gid"] = int(wr_gid)
                matches = [r for r in rows if r.get("grid_string_id") == int(wr_gid)]
                st.session_state["wr_filter_gid_matches"] = matches
                st.rerun()

            if "wr_filter_gid_matches" in st.session_state and st.session_state.get("wr_filter_gid") == wr_gid:
                matches = st.session_state["wr_filter_gid_matches"]
                if not matches:
                    st.info(f"grid_string_id **{wr_gid}**는 이번 조회 결과에 없습니다.")
                else:
                    used_w9 = st.session_state.get("wr_filter_min_pct_w9_used", 56)
                    run_id_sel = matches[0].get("run_id_full") if len(matches) == 1 else None
                    if run_id_sel is None:
                        opts = [(m["run_id_full"], m["run_id"] + " | 필터된 스텝 " + str(m.get("필터된 스텝 수", 0))) for m in matches]
                        sel_idx = st.selectbox("Run 선택", range(len(opts)), format_func=lambda i: opts[i][1], key="wr_gid_run_sel")
                        run_id_sel = opts[sel_idx][0]
                    if run_id_sel:
                        events = query_simulation_step_events(run_id_sel, int(wr_gid))
                        win_rate_numeric = query_sim_win_rate_numeric_from_predictions_table(events)
                        filtered = filter_events_by_w9_w10_pairs(events, win_rate_numeric, used_w9)
                        if filtered:
                            pair_label = f"윈도우 9 {used_w9}% 이상인 쌍만" if used_w9 and used_w9 > 0 else "전체"
                            st.markdown(f"##### 필터된 상세 히스토리 ({pair_label}, {len(filtered)}개 스텝)")
                            f_map = query_sim_win_rate_from_predictions_table(filtered)
                            f_rows = build_history_table_rows(filtered, is_live=False, sim_win_rate_map=f_map)
                            st.dataframe(pd.DataFrame(f_rows), use_container_width=True, hide_index=True)
                        else:
                            st.info(f"grid_string_id {wr_gid}에서 조건에 맞는 스텝이 없습니다.")


def _render_prediction_table_confidence_section():
    """예측 테이블(simulation_predictions_change_point) 신뢰도 섹션. 하단 탭으로 요약/prefix별 상세."""
    window_sizes = (9, 10, 11)
    st.caption("simulation_predictions_change_point · 윈도우 9, 10, 11 · 모든 메소드")

    sub_t1, sub_t2, sub_t3 = st.tabs(["요약", "prefix별 메소드 비교", "prefix별 상세(원본)"])

    with sub_t1:
        _render_prediction_confidence_summary(window_sizes)

    with sub_t2:
        _render_prediction_prefix_method_comparison(window_sizes)

    with sub_t3:
        _render_prediction_prefix_detail(window_sizes)


def _render_prediction_confidence_summary(window_sizes):
    """메소드·윈도우별 신뢰도 요약 테이블."""
    df = query_prediction_table_confidence_stats(window_sizes=window_sizes)

    if df is None or len(df) == 0:
        st.info("예측 테이블에 데이터가 없거나 조회 결과가 없습니다. (윈도우 9, 10, 11 · 모든 method)")
        return

    display_df = pd.DataFrame({
        "메소드": df["method"],
        "윈도우 크기": df["window_size"],
        "레코드 수": df["record_count"].astype(int),
        "평균 신뢰도 (%)": (df["avg_confidence"].round(2)),
        "최소 신뢰도 (%)": (df["min_confidence"].round(2)),
        "최대 신뢰도 (%)": (df["max_confidence"].round(2)),
    })
    display_df.insert(0, "No", range(1, len(display_df) + 1))
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(display_df)}개 (메소드×윈도우) 조합 · 윈도우: {window_sizes}")


def _format_pred_frequency(value, method):
    """예측 빈도 표시: 빈도 기반/안전 우선은 정수, 가중치 기반은 소수 1자리."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if method == "가중치 기반":
        return f"{float(value):.1f}"
    return int(value)


def _build_prefix_method_comparison_df(df_raw, window_sizes):
    """
    같은 prefix에 대해 메소드별 예측값·신뢰도·예측 빈도를 나란히 보여주는 피벗 테이블 생성.
    threshold=0 기준 (라이브 앱과 동일). 시뮬레이션 승률(%) 컬럼 포함.
    """
    if df_raw is None or len(df_raw) == 0:
        return pd.DataFrame()
    df = df_raw[df_raw["threshold"] == 0].copy()
    if len(df) == 0:
        return pd.DataFrame()
    df["confidence"] = df["confidence"].round(2)
    methods = df["method"].unique().tolist()
    method_order = ["빈도 기반", "가중치 기반", "안전 우선"]
    methods = [m for m in method_order if m in methods] or methods
    rows = []
    for (ws, prefix), grp in df.groupby(["window_size", "prefix"]):
        row = {"윈도우": ws, "prefix": prefix}
        sub_freq = grp[grp["method"] == "빈도 기반"]
        total_freq = sub_freq.iloc[0].get("pred_frequency") if len(sub_freq) > 0 else None
        if total_freq is not None and not (isinstance(total_freq, float) and pd.isna(total_freq)):
            row["전체 빈도"] = int(total_freq)
        else:
            row["전체 빈도"] = "-"
        for m in methods:
            sub = grp[grp["method"] == m]
            if len(sub) > 0:
                r = sub.iloc[0]
                row[f"{m}_예측"] = r["predicted_value"] if pd.notna(r["predicted_value"]) and r["predicted_value"] else "-"
                row[f"{m}_신뢰도(%)"] = r["confidence"] if pd.notna(r["confidence"]) else "-"
                row[f"{m}_예측빈도"] = _format_pred_frequency(r.get("pred_frequency"), m)
            else:
                row[f"{m}_예측"] = "-"
                row[f"{m}_신뢰도(%)"] = "-"
                row[f"{m}_예측빈도"] = "-"
        rows.append(row)
    display_df = pd.DataFrame(rows)
    df_win = query_step_events_prefix_win_rate(window_sizes)
    if len(df_win) > 0:
        display_df = display_df.merge(
            df_win[["window_size", "prefix", "win_rate_pct"]],
            left_on=["윈도우", "prefix"],
            right_on=["window_size", "prefix"],
            how="left",
        )
        display_df["시뮬레이션 승률(%)"] = display_df["win_rate_pct"].apply(
            lambda x: f"{x:.1f}" if pd.notna(x) else "-"
        )
        drop_cols = ["window_size", "win_rate_pct"]
        if "prefix_y" in display_df.columns:
            drop_cols.append("prefix_y")
        display_df = display_df.drop(columns=drop_cols)
    else:
        display_df["시뮬레이션 승률(%)"] = "-"
    return display_df


def _render_prediction_prefix_method_comparison(window_sizes):
    """같은 prefix에 대해 메소드별 예측값·신뢰도 차이 비교 테이블. 윈도우 크기별 필터 지원."""
    df = query_prediction_table_prefix_detail(window_sizes=window_sizes)
    display_df = _build_prefix_method_comparison_df(df, window_sizes)

    if display_df is None or len(display_df) == 0:
        st.info("예측 테이블에 데이터가 없습니다. (threshold=0 기준)")
        return

    # 윈도우 크기별 필터
    ws_options = ["전체"] + [int(x) for x in sorted(display_df["윈도우"].unique())]
    selected_ws = st.selectbox(
        "윈도우 크기",
        range(len(ws_options)),
        format_func=lambda i: "전체" if ws_options[i] == "전체" else f"{ws_options[i]}",
        key="prefix_method_ws_filter",
    )
    ws_value = ws_options[selected_ws]

    if ws_value != "전체":
        display_df = display_df[display_df["윈도우"] == ws_value].copy()
        # 윈도우 컬럼은 동일값이므로 숨겨도 됨 (선택 시 제거하면 테이블 간결)
        display_df = display_df.drop(columns=["윈도우"])

    display_df.insert(0, "No", range(1, len(display_df) + 1))
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    caption_ws = f"윈도우 {ws_value}" if ws_value != "전체" else f"윈도우: {window_sizes}"
    st.caption(
        f"총 {len(display_df):,}개 prefix · {caption_ws} · "
        "같은 행에서 메소드별 예측값·신뢰도 차이 비교"
    )


def _render_prediction_prefix_detail(window_sizes):
    """모든 prefix별 신뢰도 상세 테이블 (원본 행 구조). 시뮬레이션 승률(%) 컬럼 포함."""
    df = query_prediction_table_prefix_detail(window_sizes=window_sizes)

    if df is None or len(df) == 0:
        st.info("예측 테이블에 데이터가 없습니다.")
        return

    pred_freq_display = df.apply(
        lambda r: _format_pred_frequency(r.get("pred_frequency"), r["method"]),
        axis=1,
    )
    freq_total = (
        df[df["method"] == "빈도 기반"][["window_size", "prefix", "pred_frequency"]]
        .drop_duplicates()
        .rename(columns={"pred_frequency": "total_freq"})
    )
    display_df = pd.DataFrame({
        "윈도우": df["window_size"],
        "prefix": df["prefix"],
        "메소드": df["method"],
        "임계값": df["threshold"],
        "예측값": df["predicted_value"],
        "예측 빈도": pred_freq_display,
        "신뢰도 (%)": (df["confidence"].round(2)),
        "B 비율 (%)": (df["b_ratio"].round(2)),
        "P 비율 (%)": (df["p_ratio"].round(2)),
    })
    if len(freq_total) > 0:
        display_df = display_df.merge(
            freq_total,
            left_on=["윈도우", "prefix"],
            right_on=["window_size", "prefix"],
            how="left",
        )
        display_df["전체 빈도"] = display_df["total_freq"].apply(
            lambda x: int(x) if pd.notna(x) and x is not None else "-"
        )
        display_df = display_df.drop(columns=["window_size", "total_freq"], errors="ignore")
        if "prefix_y" in display_df.columns:
            display_df = display_df.drop(columns=["prefix_y"])
        lead = ["윈도우", "prefix", "전체 빈도"]
        rest = [c for c in display_df.columns if c not in lead]
        display_df = display_df[lead + rest]
    else:
        display_df["전체 빈도"] = "-"
        lead = ["윈도우", "prefix", "전체 빈도"]
        rest = [c for c in display_df.columns if c not in lead]
        display_df = display_df[lead + rest]
    df_win = query_step_events_prefix_win_rate(window_sizes)
    if len(df_win) > 0:
        display_df = display_df.merge(
            df_win[["window_size", "prefix", "win_rate_pct"]],
            left_on=["윈도우", "prefix"],
            right_on=["window_size", "prefix"],
            how="left",
        )
        display_df["시뮬레이션 승률(%)"] = display_df["win_rate_pct"].apply(
            lambda x: f"{x:.1f}" if pd.notna(x) else "-"
        )
        drop_cols = ["window_size", "win_rate_pct"]
        if "prefix_y" in display_df.columns:
            drop_cols.append("prefix_y")
        display_df = display_df.drop(columns=drop_cols)
    else:
        display_df["시뮬레이션 승률(%)"] = "-"
    display_df.insert(0, "No", range(1, len(display_df) + 1))
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(display_df):,}개 레코드 · 윈도우: {window_sizes}")


def _format_pred_freq_cell(value, method):
    """예측 빈도 셀: 빈도 기반은 정수, 가중치 기반은 소수 1자리."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    if method == "가중치 기반":
        return f"{float(value):.1f}"
    return str(int(value))


def _render_prediction_table_freq_w9_w10():
    """예측값 테이블(simulation_predictions_change_point) 빈도 기반 · 가중치 기반 · 윈도우 9, 10 데이터 테이블. 예측 테이블 신뢰도와 비교용."""
    st.markdown("---")
    st.markdown("## 예측값 테이블 (빈도 기반 · 가중치 기반 · 윈도우 9, 10)")
    st.caption("simulation_predictions_change_point · method 빈도/가중치 · window_size 9, 10 · 위 예측 테이블 신뢰도와 비교 확인용")

    df = query_prediction_table_freq_weight_window_9_10(threshold=0)
    if df is None or len(df) == 0:
        st.info("예측값 테이블에 빈도 기반·가중치 기반 · 윈도우 9·10 데이터가 없습니다.")
        return

    # (window_size, prefix)별로 빈도/가중치 나란히 한 행으로
    rows = []
    for (ws, prefix), grp in df.groupby(["window_size", "prefix"]):
        row = {"윈도우": ws, "prefix": prefix}
        sim_wr_vals = grp["sim_win_rate_pct"].dropna()
        sim_wr = float(sim_wr_vals.iloc[0]) if len(sim_wr_vals) > 0 else None
        for method in ["빈도 기반", "가중치 기반"]:
            sub = grp[grp["method"] == method]
            if len(sub) > 0:
                r = sub.iloc[0]
                row[f"{method}_예측"] = str(r["predicted_value"]) if pd.notna(r["predicted_value"]) and r["predicted_value"] else "-"
                row[f"{method}_신뢰도(%)"] = round(r["confidence"], 2) if pd.notna(r["confidence"]) else "-"
                row[f"{method}_예측빈도"] = _format_pred_freq_cell(r.get("pred_frequency"), method)
                row[f"{method}_B(%)"] = round(r["b_ratio"], 2) if pd.notna(r["b_ratio"]) else "-"
                row[f"{method}_P(%)"] = round(r["p_ratio"], 2) if pd.notna(r["p_ratio"]) else "-"
            else:
                row[f"{method}_예측"] = "-"
                row[f"{method}_신뢰도(%)"] = "-"
                row[f"{method}_예측빈도"] = "-"
                row[f"{method}_B(%)"] = "-"
                row[f"{method}_P(%)"] = "-"
        row["시뮬 승률 (%)"] = f"{float(sim_wr):.1f}" if sim_wr is not None else "-"
        rows.append(row)

    display_df = pd.DataFrame(rows)
    display_df.insert(0, "No", range(1, len(display_df) + 1))
    st.dataframe(display_df, use_container_width=True, hide_index=True)
    st.caption(f"총 {len(display_df):,}개 prefix · 빈도 기반·가중치 기반 · 윈도우 9, 10 (threshold=0)")


if __name__ == "__main__":
    main()
