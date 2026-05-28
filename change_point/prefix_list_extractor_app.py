"""
윈도우 9·10 Prefix 리스트 추출 웹앱

- ngram_chunks_change_point(원본)의 prefix·suffix 빈도와 step_events의 예측값을 결합
- "원본 최빈 suffix == step_events 최빈 predicted" 일치하는 prefix만 추출
- JSON으로 저장·다운로드 (라이브 게임 5연패 방지용)
- 추후 확장: 시뮬레이션 탭에서 rules 기반 시뮬 실행 예정
"""

import json
import sys
from pathlib import Path
from datetime import datetime

_root = Path(__file__).resolve().parent.parent
_changedir = Path(__file__).resolve().parent
sys.path.insert(0, str(_root))
sys.path.insert(0, str(_changedir))

import pandas as pd
import streamlit as st

from svg_parser_module import get_change_point_db_connection
from results_storage import get_results_db_connection, get_default_results_db_path

try:
    from change_point_prediction_module import load_preprocessed_grid_strings_cp
except ImportError:
    load_preprocessed_grid_strings_cp = None

st.set_page_config(
    page_title="Prefix 리스트 추출 (윈도우 9·10)",
    page_icon="📋",
    layout="wide",
)

WINDOW_SIZES = (9, 10)


def _prefix_pattern_svg(prefix: str, cell_px: int = 10, r_px: float = 4) -> str:
    """prefix 문자열을 꺽임에서 줄바꿈·횡으로 적용. b=빨강, p=파랑. (live_game_prefix_rules와 동일)"""
    if not prefix or not all(c in "bp" for c in prefix):
        return ""
    n = len(prefix)
    if n == 0:
        return ""
    runs = []
    i = 0
    while i < n:
        c = prefix[i]
        j = i
        while j < n and prefix[j] == c:
            j += 1
        runs.append((c, j - i))
        i = j
    circles = []
    idx = 0
    max_rows = 0
    for col, (char, run_len) in enumerate(runs):
        for row_in_run in range(run_len):
            if idx >= n:
                break
            c = prefix[idx]
            color = "#c00" if c == "b" else "#06c"
            is_last = idx == n - 1
            cx = col * cell_px + cell_px / 2
            cy = row_in_run * cell_px + cell_px / 2
            circles.append((cx, cy, color, is_last))
            idx += 1
        max_rows = max(max_rows, run_len)
    w = len(runs) * cell_px
    h = max_rows * cell_px
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" style="vertical-align:middle;">'
    ]
    for cx, cy, color, is_last in circles:
        svg_parts.append(f'<circle cx="{cx}" cy="{cy}" r="{r_px}" fill="none" stroke="{color}" stroke-width="1.2"/>')
        if is_last:
            svg_parts.append(
                f'<path d="M{cx+r_px},{cy} A{r_px},{r_px} 0 0 1 {cx},{cy+r_px} L{cx},{cy} Z" fill="{color}"/>'
            )
    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _fmt_dt(s):
    """created_at 등 datetime 표시용 (MM-DD HH:MM)."""
    if s is None:
        return ""
    try:
        if isinstance(s, str) and "T" in s:
            d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        else:
            d = pd.to_datetime(s)
        return d.strftime("%m-%d %H:%M")
    except Exception:
        return str(s)


def _norm_prefix(p):
    return (str(p).strip() if p is not None else "")


def _norm_pred(p):
    """predicted/suffix를 B 또는 P 한 글자로 정규화."""
    if p is None:
        return None
    s = str(p).strip().upper()
    if not s:
        return None
    return s[0] if s[0] in ("B", "P") else None


def _load_ngram_suffix_counts(conn):
    """ngram_chunks_change_point에서 (window_size, prefix, suffix)별 cnt. prefix 정규화."""
    q = """
        SELECT window_size, TRIM(prefix) AS prefix, suffix, COUNT(*) AS cnt
        FROM ngram_chunks_change_point
        WHERE window_size IN (9, 10)
        GROUP BY window_size, TRIM(prefix), suffix
    """
    df = pd.read_sql_query(q, conn)
    return df


def _ngram_mode_suffix_and_freq(df_suffix_counts):
    """(window_size, prefix)별 최빈 suffix, suffix_freq, freq_ngram 반환."""
    if df_suffix_counts.empty:
        return pd.DataFrame(columns=["window_size", "prefix", "mode_suffix", "suffix_freq", "freq_ngram"])
    # (window_size, prefix)별로 cnt 최대인 suffix 선택
    idx_max = df_suffix_counts.groupby(["window_size", "prefix"])["cnt"].transform("idxmax")
    df_max = df_suffix_counts.loc[idx_max].drop_duplicates(subset=["window_size", "prefix"]).copy()
    df_max = df_max.rename(columns={"suffix": "mode_suffix", "cnt": "suffix_freq"})
    # freq_ngram = (window_size, prefix)별 총 출현 수
    freq = df_suffix_counts.groupby(["window_size", "prefix"])["cnt"].sum().reset_index()
    freq = freq.rename(columns={"cnt": "freq_ngram"})
    df_max = df_max.merge(freq, on=["window_size", "prefix"])
    return df_max[["window_size", "prefix", "mode_suffix", "suffix_freq", "freq_ngram"]]


def _load_step_events_mode_predicted(conn):
    """step_events (skipped=0)에서 (window_size, prefix)별 predicted 최빈값. prefix 정규화 후 그룹화."""
    q = """
        SELECT window_size, prefix, predicted
        FROM step_events
        WHERE skipped = 0 AND window_size IN (9, 10)
    """
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return pd.DataFrame(columns=["window_size", "prefix", "mode_predicted"])
    df["prefix"] = df["prefix"].apply(_norm_prefix)
    # (window_size, prefix, predicted)별 개수
    cnt = df.groupby(["window_size", "prefix", "predicted"]).size().reset_index(name="c")
    # (window_size, prefix)별 c 최대인 predicted 선택
    idx_max = cnt.groupby(["window_size", "prefix"])["c"].transform("idxmax")
    out = cnt.loc[idx_max][["window_size", "prefix", "predicted"]].drop_duplicates(subset=["window_size", "prefix"])
    out = out.rename(columns={"predicted": "mode_predicted"})
    return out


def _load_step_events_win_rate(conn):
    """step_events (skipped=0)에서 (window_size, prefix)별 total, correct, win_rate_pct. prefix 정규화 후 집계."""
    q = """
        SELECT window_size, prefix, is_correct
        FROM step_events
        WHERE skipped = 0 AND window_size IN (9, 10)
    """
    df = pd.read_sql_query(q, conn)
    if df.empty:
        return pd.DataFrame(columns=["window_size", "prefix", "total", "correct", "win_rate_pct"])
    df["prefix"] = df["prefix"].apply(_norm_prefix)
    df["correct_one"] = (df["is_correct"] == 1).astype(int)
    agg = df.groupby(["window_size", "prefix"]).agg(
        total=("is_correct", "count"),
        correct=("correct_one", "sum"),
    ).reset_index()
    agg["win_rate_pct"] = (100.0 * agg["correct"].astype(float) / agg["total"]).round(2)
    return agg


def extract_prefix_rules_win9_10(ngram_conn, step_events_db_path):
    """
    추출: step_events에 있는 (window_size, prefix)만 후보,
    원본 최빈 suffix == step_events 최빈 predicted 일치 시 rules에 포함.

    Returns:
        tuple: (rules_list, meta_dict, df_display)
        - rules_list: [{"window_size", "prefix", "prediction", ...}, ...]
        - meta_dict: {"total_rules", "by_window", ...}
        - df_display: DataFrame for UI (optional columns)
    """
    # 1) ngram: (window_size, prefix) -> mode_suffix, suffix_freq, freq_ngram
    df_ngram_cnt = _load_ngram_suffix_counts(ngram_conn)
    df_ngram = _ngram_mode_suffix_and_freq(df_ngram_cnt)
    if df_ngram.empty:
        return [], {"total_rules": 0, "by_window": {"9": 0, "10": 0}}, pd.DataFrame()

    # 2) step_events: (window_size, prefix) -> mode_predicted, total, correct, win_rate_pct
    step_conn = get_results_db_connection(step_events_db_path)
    try:
        df_se_mode = _load_step_events_mode_predicted(step_conn)
        df_se_wr = _load_step_events_win_rate(step_conn)
    finally:
        step_conn.close()

    if df_se_mode.empty:
        return [], {"total_rules": 0, "by_window": {"9": 0, "10": 0}}, pd.DataFrame()

    # 3) 후보 = step_events에 있는 (window_size, prefix). prefix 정규화로 조인
    df_se_mode["mode_pred_norm"] = df_se_mode["mode_predicted"].apply(_norm_pred)
    merged = df_se_mode.merge(
        df_ngram,
        on=["window_size", "prefix"],
        how="inner",
    )
    merged = merged.merge(
        df_se_wr[["window_size", "prefix", "total", "correct", "win_rate_pct"]],
        on=["window_size", "prefix"],
        how="left",
    )
    # ngram mode_suffix 정규화 (b/p -> B/P)
    merged["mode_suffix_norm"] = merged["mode_suffix"].apply(lambda x: _norm_pred(x) if pd.notna(x) else None)

    # 4) 일치: mode_suffix_norm == mode_pred_norm
    matched = merged[merged["mode_suffix_norm"] == merged["mode_pred_norm"]].copy()
    matched["prediction"] = matched["mode_pred_norm"]

    rules_list = []
    for _, row in matched.iterrows():
        r = {
            "window_size": int(row["window_size"]),
            "prefix": _norm_prefix(row["prefix"]),
            "prediction": (row["prediction"] or "B"),
        }
        if pd.notna(row.get("freq_ngram")):
            r["freq_ngram"] = int(row["freq_ngram"])
        if pd.notna(row.get("suffix_freq")):
            r["suffix_freq"] = int(row["suffix_freq"])
        # suffix_freq 신뢰도: 원본에서 최빈 suffix가 차지하는 비율 (%)
        if pd.notna(row.get("freq_ngram")) and pd.notna(row.get("suffix_freq")) and row["freq_ngram"] > 0:
            r["suffix_confidence_pct"] = round(100.0 * float(row["suffix_freq"]) / float(row["freq_ngram"]), 2)
        if pd.notna(row.get("win_rate_pct")):
            r["win_rate_pct"] = round(float(row["win_rate_pct"]), 2)
        if pd.notna(row.get("total")):
            r["total_events"] = int(row["total"])
        rules_list.append(r)

    by_window = {"9": sum(1 for r in rules_list if r["window_size"] == 9), "10": sum(1 for r in rules_list if r["window_size"] == 10)}
    meta = {"total_rules": len(rules_list), "by_window": by_window}

    df_display = matched[["window_size", "prefix", "prediction", "freq_ngram", "suffix_freq", "win_rate_pct", "total"]].copy()
    df_display = df_display.rename(columns={"total": "total_events"})
    df_display["suffix_confidence_pct"] = (100.0 * df_display["suffix_freq"] / df_display["freq_ngram"].replace(0, float("nan"))).round(2)
    df_display = df_display.sort_values(["window_size", "prefix"]).reset_index(drop=True)

    return rules_list, meta, df_display


def build_export_json(rules_list, meta):
    """계획서 JSON 형식: window_sizes, generated_at, rules, meta."""
    return {
        "window_sizes": list(WINDOW_SIZES),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "rules": rules_list,
        "meta": meta,
    }


def _load_test_grid_strings(cutoff_id, end_id=None):
    """cutoff_id 초과, end_id 이하(지정 시)인 grid_string id·문자열 로드. id 오름차순."""
    conn = get_change_point_db_connection()
    try:
        if cutoff_id is None:
            if end_id is not None:
                q = "SELECT id, grid_string FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
                df = pd.read_sql_query(q, conn, params=[end_id])
            else:
                q = "SELECT id, grid_string FROM preprocessed_grid_strings ORDER BY id"
                df = pd.read_sql_query(q, conn)
        else:
            if end_id is not None:
                q = "SELECT id, grid_string FROM preprocessed_grid_strings WHERE id > ? AND id <= ? ORDER BY id"
                df = pd.read_sql_query(q, conn, params=[cutoff_id, end_id])
            else:
                q = "SELECT id, grid_string FROM preprocessed_grid_strings WHERE id > ? ORDER BY id"
                df = pd.read_sql_query(q, conn, params=[cutoff_id])
        return df
    finally:
        conn.close()


def _rules_to_map(rules_list, window_sizes):
    """rules_list에서 window_sizes에 해당하는 (window_size, norm_prefix) -> prediction 맵."""
    out = {}
    for r in rules_list:
        ws = r.get("window_size")
        if ws not in window_sizes:
            continue
        prefix = _norm_prefix(r.get("prefix"))
        if not prefix:
            continue
        pred = (r.get("prediction") or "B").strip().upper()
        if pred not in ("B", "P"):
            pred = "B"
        out[(ws, prefix)] = pred
    return out


def _run_single_grid_simulation(grid_string, grid_string_id, rules_map_full, window_sizes):
    """
    단일 grid_string에 대해 계획 5~9 규칙으로 시뮬 실행.
    앵커 미사용: 선택한 window_size별 모든 위치가 후보.
    반환: history 리스트 (dict: step, grid_string_id, position, window_size, prefix, predicted, actual, is_correct, list_size, skipped)
    """
    if not grid_string or not rules_map_full:
        return []
    gs = (grid_string or "").strip().lower()
    if len(gs) < max(window_sizes):
        return []

    # 후보: (pos, window_size) 오름차순 (pos 먼저, 같은 pos면 window_size 순)
    candidates = []
    for pos in range(max(window_sizes) - 1, len(gs)):
        for ws in sorted(window_sizes):
            if pos >= ws - 1 and pos < len(gs):
                candidates.append((pos, ws))
    # pos 오름차순, 같은 pos면 window_size 오름차순
    candidates.sort(key=lambda x: (x[0], x[1]))

    full_list = dict(rules_map_full)
    current_list = dict(full_list)
    history = []
    step = 0
    for pos, ws in candidates:
        prefix_len = ws - 1
        prefix = gs[pos - prefix_len : pos]
        actual_char = gs[pos]
        norm_prefix = prefix.strip().lower()
        key = (ws, norm_prefix)
        actual_norm = (actual_char or "b").strip().upper()
        if actual_norm not in ("B", "P"):
            actual_norm = "B"

        step += 1
        if key not in current_list:
            history.append({
                "step": step, "grid_string_id": grid_string_id, "position": pos, "window_size": ws,
                "prefix": prefix, "predicted": None, "actual": actual_char, "is_correct": None,
                "list_size": len(current_list), "skipped": True,
            })
            continue

        predicted = current_list[key]
        is_correct = (predicted == actual_norm)
        history.append({
            "step": step, "grid_string_id": grid_string_id, "position": pos, "window_size": ws,
            "prefix": prefix, "predicted": predicted, "actual": actual_char, "is_correct": is_correct,
            "list_size": len(current_list), "skipped": False,
        })
        if is_correct:
            current_list = dict(full_list)
        else:
            del current_list[key]
            if len(current_list) == 0:
                current_list = dict(full_list)
    return history


def _consecutive_stats_from_history(all_history):
    """all_history에서 예측 스텝만으로 연속 일치/불일치 통계 계산.
    반환: (max_consec_correct, max_consec_incorrect, runs_correct, runs_incorrect,
           runs_correct_details, runs_incorrect_details).
    details 각 항목: start_step, start_grid_string_id, end_step, end_grid_string_id, length."""
    pred_items = [(h["is_correct"], h) for h in all_history if not h.get("skipped")]
    max_consec_correct = max_consec_incorrect = 0
    runs_correct = runs_incorrect = 0
    runs_correct_details = []
    runs_incorrect_details = []

    curr_c, curr_i = 0, 0
    start_c_step, start_c_gid, end_c_step, end_c_gid = None, None, None, None
    start_i_step, start_i_gid, end_i_step, end_i_gid = None, None, None, None

    for is_correct, h in pred_items:
        step = h.get("step")
        gid = h.get("grid_string_id")
        if is_correct is True:
            if curr_c == 0:
                start_c_step, start_c_gid = step, gid
            curr_c += 1
            end_c_step, end_c_gid = step, gid
            if curr_i > 0:
                runs_incorrect_details.append({
                    "start_step": start_i_step, "start_grid_string_id": start_i_gid,
                    "end_step": end_i_step, "end_grid_string_id": end_i_gid, "length": curr_i,
                })
                curr_i = 0
            if curr_c == 1:
                runs_correct += 1
        else:
            if curr_i == 0:
                start_i_step, start_i_gid = step, gid
            curr_i += 1
            end_i_step, end_i_gid = step, gid
            if curr_c > 0:
                runs_correct_details.append({
                    "start_step": start_c_step, "start_grid_string_id": start_c_gid,
                    "end_step": end_c_step, "end_grid_string_id": end_c_gid, "length": curr_c,
                })
                curr_c = 0
            if curr_i == 1:
                runs_incorrect += 1
        max_consec_correct = max(max_consec_correct, curr_c)
        max_consec_incorrect = max(max_consec_incorrect, curr_i)

    if curr_c > 0:
        runs_correct_details.append({
            "start_step": start_c_step, "start_grid_string_id": start_c_gid,
            "end_step": end_c_step, "end_grid_string_id": end_c_gid, "length": curr_c,
        })
    if curr_i > 0:
        runs_incorrect_details.append({
            "start_step": start_i_step, "start_grid_string_id": start_i_gid,
            "end_step": end_i_step, "end_grid_string_id": end_i_gid, "length": curr_i,
        })

    return max_consec_correct, max_consec_incorrect, runs_correct, runs_incorrect, runs_correct_details, runs_incorrect_details


def build_live_game_prefix_rules_compatible_json(rules_list):
    """
    live_game_prefix_rules.py의 _import_rules_from_json과 호환되는 형식.
    최상위 배열 [{"prefix": "...", "prediction": "B"|"P"}, ...] (prefix strip·lower, prediction B/P).
    """
    out = []
    for r in rules_list:
        prefix = (r.get("prefix") or "").strip().lower()
        if not prefix:
            continue
        pred = (r.get("prediction") or "B").strip().upper()
        if pred not in ("B", "P"):
            pred = "B"
        out.append({"prefix": prefix, "prediction": pred})
    return out


def min_win_rate_pct_for_max_5consecutive_fail_prob(max_fail_prob_pct):
    """
    5연패 확률이 max_fail_prob_pct 이하가 되려면 필요한 최소 승률(%).
    P(5연패) = (1-p)^5 <= max_fail_prob_pct/100  =>  p >= 1 - (max_fail_prob_pct/100)^(1/5).
    """
    if max_fail_prob_pct <= 0 or max_fail_prob_pct >= 100:
        return 0.0
    p_min = 1.0 - (max_fail_prob_pct / 100.0) ** (1.0 / 5.0)
    return round(100.0 * p_min, 2)


def filter_rules_by_threshold(
    rules_list,
    min_win_rate_pct,
    min_total_events=None,
    min_suffix_confidence_pct=None,
    min_total_events_by_window=None,
):
    """5연패 확률·관측 수·신뒤도 기준으로 규칙 필터. rules_list 항목에 win_rate_pct, total_events, suffix_confidence_pct(선택) 있어야 함.
    min_total_events_by_window가 있으면 윈도우별 최소 관측 수 적용 (예: {9: 100, 10: 10}); 없으면 min_total_events 단일값 사용."""
    out = []
    for r in rules_list:
        wr = r.get("win_rate_pct")
        total = r.get("total_events")
        if wr is None:
            continue
        ws = r.get("window_size")
        if min_total_events_by_window is not None and ws is not None:
            min_req = min_total_events_by_window.get(ws)
            if min_req is not None and total is not None and total < min_req:
                continue
        elif total is not None and min_total_events is not None and total < min_total_events:
            continue
        if wr < min_win_rate_pct:
            continue
        if min_suffix_confidence_pct is not None:
            sc = r.get("suffix_confidence_pct")
            if sc is not None and sc < min_suffix_confidence_pct:
                continue
        out.append(r)
    return out


def main():
    st.title("📋 Prefix 리스트 추출 (윈도우 9·10)")
    st.markdown("""
    **원본 데이터**(ngram_chunks_change_point)의 prefix·suffix 빈도와 **step_events** 예측값을 결합해  
    **원본 최빈 suffix = step_events 최빈 predicted** 인 prefix만 추출합니다.  
    step_events에 없는 prefix는 제외됩니다. 추출된 리스트는 라이브 게임에서 5연패 방지용으로 사용할 수 있습니다.
    """)

    # 탭: 추출/JSON 내보내기 | 시뮬레이션 | 필터 탐색
    tab_extract, tab_sim, tab_search = st.tabs(["추출 / JSON 내보내기", "시뮬레이션", "필터 탐색"])

    with tab_extract:
        st.markdown("---")
        col_refresh, col_run, _ = st.columns([1, 1, 4])
        with col_refresh:
            refresh_clicked = st.button("🔄 데이터 새로고침", key="prefix_extract_refresh", use_container_width=True)
        with col_run:
            run_clicked = st.button("▶ 추출 실행", type="primary", key="prefix_extract_run", use_container_width=True)
        if refresh_clicked:
            st.success("✅ 새로고침되었습니다.")
            st.rerun()

        # 세션에 결과 저장
        if "prefix_extract_result" not in st.session_state:
            st.session_state.prefix_extract_result = None  # (rules_list, meta, df_display)

        if run_clicked:
            with st.spinner("DB 조회 및 추출 중..."):
                try:
                    ngram_conn = get_change_point_db_connection()
                    try:
                        step_path = get_default_results_db_path()
                        rules_list, meta, df_display = extract_prefix_rules_win9_10(ngram_conn, step_path)
                        st.session_state.prefix_extract_result = (rules_list, meta, df_display)
                        st.session_state.prefix_filtered_rules = None  # 새 추출 후에는 필터 적용 버튼으로 다시 적용
                        st.session_state.prefix_filter_params = None
                    finally:
                        ngram_conn.close()
                except Exception as e:
                    st.error(f"추출 중 오류: {e}")
                    import traceback
                    st.code(traceback.format_exc())
                    st.session_state.prefix_extract_result = None

        result = st.session_state.prefix_extract_result
        if result is not None:
            rules_list, meta, df_display = result
            st.session_state.prefix_rules_list = rules_list  # 필터 탐색 탭에서 사용
            st.success(f"추출 완료: **{meta['total_rules']}**개 규칙 (윈도우 9: {meta['by_window']['9']}, 윈도우 10: {meta['by_window']['10']})")

            # ---------- 5연패 확률 기준으로 리스트 좁히기 ----------
            st.markdown("---")
            st.markdown("### 5연패 확률 기준 필터")
            st.caption("승률 p일 때 5연패 확률 = (1-p)^5. 허용 최대 5연패 확률을 정하면 필요한 최소 승률이 정해집니다.")
            col_prob, col_min_obs, col_conf, col_btn, _ = st.columns([2, 2, 2, 1, 2])
            with col_prob:
                max_fail_pct = st.number_input(
                    "최대 허용 5연패 확률 (%)",
                    min_value=0.1,
                    max_value=50.0,
                    value=1.5,
                    step=0.1,
                    format="%.1f",
                    key="max_5fail_pct",
                    help="이 값 이하로 5연패가 나올 확률을 제한합니다. 0.1 단위로 조정 가능.",
                )
                min_wr = min_win_rate_pct_for_max_5consecutive_fail_prob(max_fail_pct)
                st.metric("→ 최소 승률 (win_rate_pct)", f"{min_wr}%")
            with col_min_obs:
                min_total_events_win9 = st.number_input(
                    "최소 관측 수 (윈도우 9)",
                    min_value=1,
                    value=100,
                    step=1,
                    key="min_total_events_win9",
                    help="윈도우 9 prefix: step_events에서 이 횟수 이상 나온 것만 유지.",
                )
                min_total_events_win10 = st.number_input(
                    "최소 관측 수 (윈도우 10)",
                    min_value=1,
                    value=10,
                    step=1,
                    key="min_total_events_win10",
                    help="윈도우 10 prefix: step_events에서 이 횟수 이상 나온 것만 유지.",
                )
                min_total_events_by_window = {9: min_total_events_win9, 10: min_total_events_win10}
            with col_conf:
                min_suffix_confidence_pct = st.number_input(
                    "최소 신뢰도 (suffix_confidence_pct, %)",
                    min_value=0.0,
                    max_value=100.0,
                    value=51.8,
                    step=0.1,
                    format="%.1f",
                    key="min_suffix_confidence_pct",
                    help="원본에서 최빈 suffix가 나온 비율. 이 값 이상인 prefix만 유지. 0.1 단위 조정.",
                )
            with col_btn:
                filter_apply_clicked = st.button("▶ 필터 적용", type="primary", key="prefix_filter_apply", use_container_width=True)

            if filter_apply_clicked:
                filtered_rules = filter_rules_by_threshold(
                    rules_list, min_wr,
                    min_suffix_confidence_pct=min_suffix_confidence_pct,
                    min_total_events_by_window=min_total_events_by_window,
                )
                st.session_state.prefix_filtered_rules = filtered_rules
                st.session_state.prefix_filter_params = {
                    "max_fail_pct": max_fail_pct,
                    "min_win_rate_pct": min_wr,
                    "min_total_events_win9": min_total_events_win9,
                    "min_total_events_win10": min_total_events_win10,
                    "min_suffix_confidence_pct": min_suffix_confidence_pct,
                }
                st.rerun()

            filtered_rules = st.session_state.get("prefix_filtered_rules")
            if filtered_rules is None:
                filtered_rules = []
                st.caption("필터 값을 설정한 뒤 **필터 적용** 버튼을 눌러 주세요.")
            filtered_by_win = {"9": sum(1 for r in filtered_rules if r["window_size"] == 9), "10": sum(1 for r in filtered_rules if r["window_size"] == 10)}
            filtered_meta = {"total_rules": len(filtered_rules), "by_window": filtered_by_win}

            st.markdown("**적용 후 규칙 수** (위 조건 적용)")
            st.info(f"**{filtered_meta['total_rules']}**개 (윈도우 9: {filtered_meta['by_window']['9']}, 윈도우 10: {filtered_meta['by_window']['10']}) — 이 리스트로 JSON 저장/다운로드합니다.")

            # 테이블: 필터 적용된 규칙만 표시 (suffix_freq 신뢰도 포함)
            st.caption("**suffix_confidence_pct**: 원본(ngram)에서 해당 prefix 다음에 최빈 suffix가 나온 비율(%). 높을수록 그 예측이 원본에서 더 확실함.")
            if filtered_rules:
                df_filtered = pd.DataFrame(filtered_rules)
                if "total_events" in df_filtered.columns:
                    df_filtered = df_filtered.sort_values(["window_size", "prefix"]).reset_index(drop=True)
                # 컬럼 순서: suffix_confidence_pct를 suffix_freq 옆에
                if "suffix_confidence_pct" not in df_filtered.columns and "freq_ngram" in df_filtered.columns and "suffix_freq" in df_filtered.columns:
                    df_filtered["suffix_confidence_pct"] = (100.0 * df_filtered["suffix_freq"] / df_filtered["freq_ngram"].replace(0, float("nan"))).round(2)
                cols = [c for c in ["window_size", "prefix", "prediction", "freq_ngram", "suffix_freq", "suffix_confidence_pct", "win_rate_pct", "total_events"] if c in df_filtered.columns]
                st.dataframe(df_filtered[cols] if cols else df_filtered, use_container_width=True, hide_index=True)
            else:
                st.warning("조건을 만족하는 규칙이 없습니다. 5연패 확률을 늘리거나, 최소 관측 수·최소 신뢰도를 낮춰 보세요.")
                st.dataframe(pd.DataFrame(), use_container_width=True, hide_index=True)

            st.markdown("---")
            st.markdown("### JSON 내보내기")
            st.caption("**필터 적용 후** 규칙 중, 선택한 윈도우만 포함. 저장 파일에는 **필터 조건**(5연패 확률, 최소 관측 수, 최소 신뒤도)과 **rules** 배열이 들어갑니다. rules는 **live_game_prefix_rules** 앱 불러오기와 호환됩니다.")
            include_win9 = st.checkbox("윈도우 9 포함", value=False, key="dl_include_win9", help="체크 시 윈도우 9 규칙을 JSON에 포함")
            include_win10 = st.checkbox("윈도우 10 포함", value=False, key="dl_include_win10", help="체크 시 윈도우 10 규칙을 JSON에 포함")
            download_windows = [w for w, inc in [(9, include_win9), (10, include_win10)] if inc]
            download_rules = [r for r in filtered_rules if r["window_size"] in download_windows]
            if not download_windows:
                st.caption("윈도우를 하나 이상 선택하면 해당 규칙만 다운로드됩니다. (선택 없음: 규칙 0개)")

            # live_game_prefix_rules 호환: rules 배열 [{"prefix", "prediction"}, ...] + 필터 조건
            compatible_list = build_live_game_prefix_rules_compatible_json(download_rules)
            payload = {
                "filter_conditions": st.session_state.get("prefix_filter_params"),
                "rules": compatible_list,
            }
            json_str = json.dumps(payload, ensure_ascii=False, indent=2)

            col_dl, col_save, _ = st.columns([1, 1, 4])
            with col_dl:
                st.download_button(
                    "💾 JSON 다운로드",
                    data=json_str,
                    file_name=f"prefix_list_win9_10_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json",
                    mime="application/json",
                    key="prefix_extract_download",
                    use_container_width=True,
                )
            with col_save:
                save_path = Path(__file__).resolve().parent / "prefix_list_win9_10.json"
                if st.button("📁 파일로 저장", key="prefix_extract_save_file", use_container_width=True):
                    try:
                        save_path.write_text(json_str, encoding="utf-8")
                        st.success(f"저장됨: `{save_path.name}` (윈도우 {download_windows or '없음'})")
                    except Exception as e:
                        st.error(str(e))
        else:
            if not run_clicked:
                st.caption("위 **추출 실행** 버튼을 눌러 주세요.")

    with tab_sim:
        st.markdown("### 시뮬레이션")
        st.caption("추출된 prefix 리스트로 테스트 데이터를 검증합니다. 앵커 미사용, 선택한 윈도우의 모든 위치가 후보. 불일치 시 리스트에서 해당 prefix 제거, 리스트가 비면 전체 복원 후 계속.")

        if load_preprocessed_grid_strings_cp is None:
            st.warning("`change_point_prediction_module.load_preprocessed_grid_strings_cp`를 불러올 수 없습니다. 시뮬레이션을 사용하려면 프로젝트 경로를 확인하세요.")
        else:
            df_mw = load_preprocessed_grid_strings_cp()
            if len(df_mw) == 0:
                st.warning("preprocessed_grid_strings에 데이터가 없습니다.")
            else:
                df_mw = df_mw.sort_values("id", ascending=False).reset_index(drop=True)
                cutoff_opts = [None] + df_mw["id"].tolist()
                cutoff_lbl = ["전체 (ID 이후 없음)"] + [f"ID {r['id']} 이후 ({_fmt_dt(r.get('created_at'))})" for _, r in df_mw.iterrows()]
                end_opts = [None] + df_mw["id"].tolist()
                end_lbl = ["전체 (끝까지)"] + [f"ID {r['id']} ({_fmt_dt(r.get('created_at'))})" for _, r in df_mw.iterrows()]

                st.markdown("""
                <style>
                /* 시뮬레이션: Grid String 선택 드롭다운 가로 길이 (시간 표시가 잘리지 않도록) */
                [data-testid="stSelectbox"] > div { min-width: 420px !important; }
                </style>
                """, unsafe_allow_html=True)
                col_cut, col_end, _ = st.columns([2, 2, 4])
                with col_cut:
                    idx_cutoff = st.selectbox("기준 Grid String ID (이 ID 이후 검증)", range(len(cutoff_opts)), format_func=lambda i: cutoff_lbl[i], key="sim_cutoff")
                    cutoff_id = cutoff_opts[idx_cutoff]
                with col_end:
                    idx_end = st.selectbox("마지막 검증 Grid String ID (해당 ID까지만)", range(len(end_opts)), format_func=lambda i: end_lbl[i], key="sim_end", help="선택 시 cutoff ~ end 구간만 검증")
                    end_id = end_opts[idx_end]

                sim_win9 = st.checkbox("윈도우 9 사용", value=True, key="sim_win9")
                sim_win10 = st.checkbox("윈도우 10 사용", value=False, key="sim_win10")
                sim_window_sizes = tuple(ws for ws in (9, 10) if (ws == 9 and sim_win9) or (ws == 10 and sim_win10))
                if not sim_window_sizes:
                    st.caption("윈도우를 하나 이상 선택하세요.")

                run_sim = st.button("▶ 시뮬레이션 실행", type="primary", key="run_sim")
                if run_sim:
                    filtered_rules = st.session_state.get("prefix_filtered_rules")
                    if filtered_rules is None:
                        st.error("먼저 **추출 / JSON 내보내기** 탭에서 **추출 실행**을 한 뒤, 필터(5연패 확률·최소 관측 수·최소 신뢰도)가 적용된 리스트가 만들어지면 시뮬레이션을 실행하세요. (필터된 리스트만 사용합니다.)")
                    elif not sim_window_sizes:
                        st.error("윈도우를 하나 이상 선택하세요.")
                    else:
                        rules_map = _rules_to_map(filtered_rules, sim_window_sizes)
                        if not rules_map:
                            st.warning("선택한 윈도우에 해당하는 규칙이 없습니다.")
                        else:
                            test_df = _load_test_grid_strings(cutoff_id, end_id)
                            if len(test_df) == 0:
                                st.warning("테스트 데이터가 없습니다. cutoff/end 구간을 확인하세요.")
                            else:
                                with st.spinner("시뮬레이션 실행 중..."):
                                    all_history = []
                                    for _, row in test_df.iterrows():
                                        gid = int(row["id"])
                                        gs = row.get("grid_string") or ""
                                        hist = _run_single_grid_simulation(gs, gid, rules_map, sim_window_sizes)
                                        all_history.extend(hist)
                                st.session_state.sim_history = all_history
                                max_cc, max_ci, runs_correct, runs_incorrect, runs_correct_details, runs_incorrect_details = _consecutive_stats_from_history(all_history)
                                st.session_state.sim_summary = {
                                    "test_grid_count": len(test_df),
                                    "total_steps": len(all_history),
                                    "predictions": sum(1 for h in all_history if not h.get("skipped")),
                                    "correct": sum(1 for h in all_history if h.get("is_correct") is True),
                                    "incorrect": sum(1 for h in all_history if h.get("is_correct") is False),
                                    "skipped": sum(1 for h in all_history if h.get("skipped")),
                                    "max_consec_correct": max_cc,
                                    "max_consec_incorrect": max_ci,
                                    "runs_correct": runs_correct,
                                    "runs_incorrect": runs_incorrect,
                                    "runs_correct_details": runs_correct_details,
                                    "runs_incorrect_details": runs_incorrect_details,
                                }
                                used_rules = []
                                for r in filtered_rules:
                                    if r.get("window_size") not in sim_window_sizes:
                                        continue
                                    p = _norm_prefix(r.get("prefix"))
                                    if not p or (r["window_size"], p) not in rules_map:
                                        continue
                                    used_rules.append({
                                        "prefix": p,
                                        "prediction": rules_map[(r["window_size"], p)],
                                        "window_size": r["window_size"],
                                        "freq_ngram": r.get("freq_ngram"),
                                        "suffix_freq": r.get("suffix_freq"),
                                    })
                                used_rules.sort(key=lambda x: (x["prefix"], x["window_size"]))
                                st.session_state.sim_used_rules = used_rules
                                st.rerun()

        if st.session_state.get("sim_summary"):
            st.markdown("---")
            st.markdown("#### 요약")
            sm = st.session_state.sim_summary
            c1, c2, c3, c4, c5 = st.columns(5)
            with c1:
                st.metric("테스트 grid_string 수", sm["test_grid_count"])
            with c2:
                st.metric("총 스텝", sm["total_steps"])
            with c3:
                st.metric("예측 수", sm["predictions"])
            with c4:
                st.metric("일치", sm["correct"])
            with c5:
                st.metric("불일치", sm["incorrect"])
            st.caption(f"스킵: {sm.get('skipped', 0)}")
            # 연속 일치/불일치 통계
            st.markdown("**연속 일치/불일치**")
            d1, d2, d3, d4 = st.columns(4)
            with d1:
                st.metric("최대 연속 일치", sm.get("max_consec_correct", 0))
            with d2:
                st.metric("최대 연속 불일치", sm.get("max_consec_incorrect", 0))
            with d3:
                st.metric("연속 일치 구간 수", sm.get("runs_correct", 0))
            with d4:
                st.metric("연속 불일치 구간 수", sm.get("runs_incorrect", 0))

            # 연속 일치/불일치 구간별 시작·끝 스텝·스트링 (히스토리 테이블에서 찾기용)
            runs_correct_details = sm.get("runs_correct_details") or []
            runs_incorrect_details = sm.get("runs_incorrect_details") or []
            if runs_correct_details or runs_incorrect_details:
                st.markdown("**연속 구간별 스텝·스트링 (히스토리 테이블에서 검색용)**")
                st.caption("아래 시작/끝 스텝·grid_string ID로 아래쪽 히스토리 테이블에서 해당 구간을 찾을 수 있습니다.")
                tab_correct, tab_incorrect = st.tabs(["연속 일치 구간", "연속 불일치 구간"])
                with tab_correct:
                    if runs_correct_details:
                        df_c = pd.DataFrame(runs_correct_details)
                        df_c = df_c.rename(columns={
                            "start_step": "시작 스텝",
                            "start_grid_string_id": "시작 스트링(ID)",
                            "end_step": "끝 스텝",
                            "end_grid_string_id": "끝 스트링(ID)",
                            "length": "길이",
                        })
                        cols = ["시작 스텝", "시작 스트링(ID)", "끝 스텝", "끝 스트링(ID)", "길이"]
                        st.dataframe(df_c[[c for c in cols if c in df_c.columns]], use_container_width=True, hide_index=True)
                    else:
                        st.caption("연속 일치 구간 없음.")
                with tab_incorrect:
                    if runs_incorrect_details:
                        df_i = pd.DataFrame(runs_incorrect_details)
                        df_i = df_i.rename(columns={
                            "start_step": "시작 스텝",
                            "start_grid_string_id": "시작 스트링(ID)",
                            "end_step": "끝 스텝",
                            "end_grid_string_id": "끝 스트링(ID)",
                            "length": "길이",
                        })
                        cols = ["시작 스텝", "시작 스트링(ID)", "끝 스텝", "끝 스트링(ID)", "길이"]
                        st.dataframe(df_i[[c for c in cols if c in df_i.columns]], use_container_width=True, hide_index=True)
                    else:
                        st.caption("연속 불일치 구간 없음.")

        if st.session_state.get("sim_history"):
            st.markdown("---")
            st.markdown("#### 히스토리 테이블")
            df_hist = pd.DataFrame(st.session_state.sim_history)
            df_hist = df_hist.iloc[::-1].reset_index(drop=True)
            st.dataframe(df_hist, use_container_width=True, hide_index=True)

            if st.session_state.get("sim_used_rules"):
                st.markdown("---")
                st.markdown("#### 사용한 리스트")
                st.markdown("""
                <style>
                div[data-testid="stVerticalBlock"] > div { margin-bottom: 0.12rem !important; }
                </style>
                """, unsafe_allow_html=True)
                used = sorted(st.session_state.sim_used_rules, key=lambda x: ((x.get("prefix") or "").lower(), x.get("window_size", 0)))
                mid = (len(used) + 1) // 2
                left_rules = used[:mid]
                right_rules = used[mid:]
                col_left, col_right = st.columns(2)
                with col_left:
                    for r in left_rules:
                        row_prefix = (r.get("prefix") or "").strip().lower()
                        row_pred = (r.get("prediction") or "B").strip().upper()
                        if row_pred not in ("B", "P"):
                            row_pred = "B"
                        ws = r.get("window_size", "")
                        freq = r.get("freq_ngram")
                        freq_txt = str(freq) if freq is not None else "—"
                        r1, r2, r3, r4 = st.columns([2, 2, 1, 1])
                        with r1:
                            st.text(row_prefix or "(빈 prefix)")
                        with r2:
                            if row_prefix and all(c in "bp" for c in row_prefix):
                                st.markdown(_prefix_pattern_svg(row_prefix), unsafe_allow_html=True)
                            else:
                                st.caption("—")
                        with r3:
                            st.text(f"{row_pred} (w{ws})")
                        with r4:
                            st.caption(f"빈도 {freq_txt}")
                with col_right:
                    for r in right_rules:
                        row_prefix = (r.get("prefix") or "").strip().lower()
                        row_pred = (r.get("prediction") or "B").strip().upper()
                        if row_pred not in ("B", "P"):
                            row_pred = "B"
                        ws = r.get("window_size", "")
                        freq = r.get("freq_ngram")
                        freq_txt = str(freq) if freq is not None else "—"
                        r1, r2, r3, r4 = st.columns([2, 2, 1, 1])
                        with r1:
                            st.text(row_prefix or "(빈 prefix)")
                        with r2:
                            if row_prefix and all(c in "bp" for c in row_prefix):
                                st.markdown(_prefix_pattern_svg(row_prefix), unsafe_allow_html=True)
                            else:
                                st.caption("—")
                        with r3:
                            st.text(f"{row_pred} (w{ws})")
                        with r4:
                            st.caption(f"빈도 {freq_txt}")

    with tab_search:
        st.markdown("### 필터 탐색")
        st.caption("5연패 확률·최소 신뒤도 그리드를 한정 범위로 돌려, 각 조합으로 시뮬 후 **최대 연속 불일치 ≤ 4** 인 조건과 확률별 목표 달성 최소 신뒤도를 찾습니다. 먼저 **추출 실행**을 한 뒤 사용하세요.")

        rules_list = st.session_state.get("prefix_rules_list")
        if rules_list is None:
            res = st.session_state.get("prefix_extract_result")
            if res is not None:
                rules_list = res[0]
        if rules_list is None or len(rules_list) == 0:
            st.warning("**추출 / JSON 내보내기** 탭에서 먼저 **추출 실행**을 해 주세요. 규칙 리스트가 있어야 탐색할 수 있습니다.")
        elif load_preprocessed_grid_strings_cp is None:
            st.warning("`change_point_prediction_module.load_preprocessed_grid_strings_cp`를 불러올 수 없습니다.")
        else:
            df_mw = load_preprocessed_grid_strings_cp()
            if len(df_mw) == 0:
                st.warning("preprocessed_grid_strings에 데이터가 없습니다.")
            else:
                df_mw = df_mw.sort_values("id", ascending=False).reset_index(drop=True)
                cutoff_opts = [None] + df_mw["id"].tolist()
                cutoff_lbl = ["전체 (ID 이후 없음)"] + [f"ID {r['id']} 이후 ({_fmt_dt(r.get('created_at'))})" for _, r in df_mw.iterrows()]
                end_opts = [None] + df_mw["id"].tolist()
                end_lbl = ["전체 (끝까지)"] + [f"ID {r['id']} ({_fmt_dt(r.get('created_at'))})" for _, r in df_mw.iterrows()]

                st.markdown("#### 탐색 범위")
                c_obs, c_prob, c_conf = st.columns(3)
                with c_obs:
                    min_obs_win9 = st.number_input("최소 관측 수 (윈도우 9)", min_value=1, value=100, step=1, key="search_min_obs_win9")
                    min_obs_win10 = st.number_input("최소 관측 수 (윈도우 10)", min_value=1, value=10, step=1, key="search_min_obs_win10")
                with c_prob:
                    prob_start = st.number_input("5연패 확률 시작 (%)", min_value=0.1, max_value=50.0, value=1.0, step=0.2, format="%.2f", key="search_prob_start")
                    prob_end = st.number_input("5연패 확률 끝 (%)", min_value=0.1, max_value=50.0, value=2.0, step=0.2, format="%.2f", key="search_prob_end")
                with c_conf:
                    conf_start = st.number_input("최소 신뒤도 시작 (%)", min_value=0.0, max_value=100.0, value=51.0, step=0.2, format="%.1f", key="search_conf_start")
                    conf_end = st.number_input("최소 신뒤도 끝 (%)", min_value=0.0, max_value=100.0, value=53.0, step=0.2, format="%.1f", key="search_conf_end")

                st.markdown("#### 시뮬 구간·윈도우")
                col_cut, col_end, _ = st.columns([2, 2, 4])
                with col_cut:
                    idx_cutoff = st.selectbox("기준 Grid String ID", range(len(cutoff_opts)), format_func=lambda i: cutoff_lbl[i], key="search_cutoff")
                    cutoff_id = cutoff_opts[idx_cutoff]
                with col_end:
                    idx_end = st.selectbox("마지막 Grid String ID", range(len(end_opts)), format_func=lambda i: end_lbl[i], key="search_end")
                    end_id = end_opts[idx_end]
                sim_win9 = st.checkbox("윈도우 9 사용", value=True, key="search_win9")
                sim_win10 = st.checkbox("윈도우 10 사용", value=False, key="search_win10")
                sim_window_sizes = tuple(ws for ws in (9, 10) if (ws == 9 and sim_win9) or (ws == 10 and sim_win10))

                run_search = st.button("▶ 탐색 실행", type="primary", key="run_search")
                if run_search:
                    if not sim_window_sizes:
                        st.error("윈도우를 하나 이상 선택하세요.")
                    else:
                        test_df = _load_test_grid_strings(cutoff_id, end_id)
                        if len(test_df) == 0:
                            st.warning("테스트 데이터가 없습니다. cutoff/end 구간을 확인하세요.")
                        else:
                            prob_lo, prob_hi = min(prob_start, prob_end), max(prob_start, prob_end)
                            conf_lo, conf_hi = min(conf_start, conf_end), max(conf_start, conf_end)
                            prob_candidates = []
                            p = prob_lo
                            while p <= prob_hi + 1e-9:
                                prob_candidates.append(round(p, 2))
                                p += 0.2
                            conf_candidates = []
                            c = conf_lo
                            while c <= conf_hi + 1e-9:
                                conf_candidates.append(round(c, 1))
                                c += 0.2
                            total_combos = len(prob_candidates) * len(conf_candidates)
                            min_total_events_by_window = {9: min_obs_win9, 10: min_obs_win10}
                            results = []
                            prog = st.progress(0.0, text="시뮬레이션 진행 중...")
                            for idx, (max_fail_pct, min_conf) in enumerate([(p, c) for p in prob_candidates for c in conf_candidates]):
                                min_wr = min_win_rate_pct_for_max_5consecutive_fail_prob(max_fail_pct)
                                filtered = filter_rules_by_threshold(
                                    rules_list, min_wr,
                                    min_suffix_confidence_pct=min_conf,
                                    min_total_events_by_window=min_total_events_by_window,
                                )
                                rules_map = _rules_to_map(filtered, sim_window_sizes)
                                if not rules_map:
                                    results.append({
                                        "max_fail_pct": max_fail_pct, "min_win_rate_pct": min_wr, "min_suffix_confidence_pct": min_conf,
                                        "규칙 수": 0, "correct": 0, "incorrect": 0, "max_consec_correct": 0, "max_consec_incorrect": 0,
                                    })
                                else:
                                    all_history = []
                                    for _, row in test_df.iterrows():
                                        gid = int(row["id"])
                                        gs = row.get("grid_string") or ""
                                        all_history.extend(_run_single_grid_simulation(gs, gid, rules_map, sim_window_sizes))
                                    n_correct = sum(1 for h in all_history if h.get("is_correct") is True)
                                    n_incorrect = sum(1 for h in all_history if h.get("is_correct") is False)
                                    max_cc, max_ci, _, _, _, _ = _consecutive_stats_from_history(all_history)
                                    results.append({
                                        "max_fail_pct": max_fail_pct, "min_win_rate_pct": min_wr, "min_suffix_confidence_pct": min_conf,
                                        "규칙 수": len(filtered), "correct": n_correct, "incorrect": n_incorrect,
                                        "max_consec_correct": max_cc, "max_consec_incorrect": max_ci,
                                    })
                                prog.progress((idx + 1) / total_combos, text=f"시뮬레이션 진행 중... {idx + 1}/{total_combos}")
                            prog.empty()
                            st.session_state.search_results = results
                            st.session_state.search_results_df = pd.DataFrame(results)
                            st.rerun()

        if st.session_state.get("search_results_df") is not None:
            st.markdown("---")
            st.markdown("#### 결과 테이블")
            df_res = st.session_state.search_results_df
            st.dataframe(df_res, use_container_width=True, hide_index=True)
            achieved = df_res[df_res["max_consec_incorrect"] <= 4]
            if len(achieved) > 0:
                st.markdown("#### 목표 달성 조건 (최대 연속 불일치 ≤ 4)")
                st.dataframe(achieved, use_container_width=True, hide_index=True)
                st.markdown("**확률별 목표 달성 최소 신뒤도**")
                for p in achieved["max_fail_pct"].unique():
                    sub = achieved[achieved["max_fail_pct"] == p]
                    min_conf = sub["min_suffix_confidence_pct"].min()
                    st.caption(f"5연패 확률 {p}% → 목표 달성 최소 신뒤도: **{min_conf}%**")
            else:
                st.info("목표(최대 연속 불일치 ≤ 4)를 만족하는 조합이 없습니다. 확률/신뒤도 범위를 넓히거나 최소 관측 수를 조정해 보세요.")

    return


if __name__ == "__main__":
    main()
