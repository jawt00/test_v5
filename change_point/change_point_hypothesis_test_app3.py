"""
Change-point 가설 테스트 앱3 — 윈도우 9 빈도+승률+빈도수550 전용

- first_anchor_window9_freq518_win50_freq550 가설만 실행 (모듈2/테스트앱2 복제 + prefix 빈도수 > 550)
- 원본: change_point_hypothesis_test_app2.py
"""

import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st
import pandas as pd

from change_point_prediction_module import load_preprocessed_grid_strings_cp
from svg_parser_module import get_change_point_db_connection
from change_point_hypothesis_module import (
    generate_simulation_predictions_table,
    get_simulation_predictions_change_point_count,
)
from change_point_hypothesis_module3 import (
    FirstAnchorWindow9Freq518Win50Freq550Hypothesis,
    batch_validate_first_anchor_window9_freq518_win50_freq550_cp,
)
from results_storage import save_run_results

HYPOTHESIS_KEY = "first_anchor_window9_freq518_win50_freq550"

st.set_page_config(
    page_title="윈도우 9 빈도+승률+빈도수550 테스트",
    page_icon="🧪",
    layout="wide",
)


def _fmt_dt(s):
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


def main():
    st.title("윈도우 9 빈도+승률+빈도수550 가설 테스트")
    hyp = FirstAnchorWindow9Freq518Win50Freq550Hypothesis()
    st.markdown(f"**가설**: {hyp.get_name()}")
    st.info(hyp.get_description())
    st.markdown("---")

    n_sim_predictions = get_simulation_predictions_change_point_count()

    # 데이터 새로고침
    if st.button("🔄 데이터 새로고침", key="refresh_data"):
        st.success("데이터가 새로고침되었습니다.")
        st.rerun()

    df_mw = load_preprocessed_grid_strings_cp()
    if len(df_mw) == 0:
        st.warning("preprocessed_grid_strings에 데이터가 없습니다.")
        return

    cutoff_opts = [None] + df_mw["id"].tolist()
    cutoff_lbl = ["전체 (ID 이후 없음)"] + [
        f"ID {r['id']} 이후 ({_fmt_dt(r['created_at'])})" for _, r in df_mw.iterrows()
    ]

    st.markdown("## 시뮬레이션 설정")
    col1, col2 = st.columns(2)
    with col1:
        idx_cutoff = st.selectbox(
            "기준 Grid String ID (이 ID 이후 검증)",
            range(len(cutoff_opts)),
            format_func=lambda i: cutoff_lbl[i],
            key="cutoff_select",
        )
        cutoff_sim = cutoff_opts[idx_cutoff]
    with col2:
        st.caption("예측 방법: 빈도 기반 (고정)")

    max_sim = None
    if cutoff_sim is not None:
        end_ids = sorted([i for i in df_mw["id"].tolist() if i > cutoff_sim])
        end_opts = [None] + end_ids
        end_lbl = ["전체 (끝까지)"] + [f"ID {eid} 까지" for eid in end_ids]
        idx_end = st.selectbox(
            "검증데이터 마지막 기준 (이 ID 까지만 검증)",
            range(len(end_opts)),
            format_func=lambda i: end_lbl[i],
            key="max_grid_select",
            help="설정 시 학습은 cutoff 이전, 검증은 cutoff 초과 ~ 선택 ID 까지만 수행 (예: cutoff 100, 마지막 150 → 101~150만 검증)",
        )
        max_sim = end_opts[idx_end]

    if cutoff_sim is not None:
        if max_sim is not None:
            st.info(f"📊 **데이터 분리**: 학습 ID ≤ {cutoff_sim} / 검증 ID {cutoff_sim + 1} ~ {max_sim}")
        else:
            st.info(f"📊 **데이터 분리**: ID {cutoff_sim} 이전 = 학습 데이터, ID {cutoff_sim} 이후 = 검증 데이터 (끝까지)")

    st.markdown("### 규칙에 적용되는 조건")
    col_t1, col_t2, col_t3 = st.columns(3)
    with col_t1:
        thresh_freq_fw = st.number_input(
            "빈도 신뢰도 최소 (%)",
            0.0,
            100.0,
            51.3,
            0.1,
            key="thresh_freq518_freq",
            help="빈도 기반 confidence가 이 값 이상일 때만 조건 충족",
        )
    with col_t2:
        min_win_rate_fw = st.number_input(
            "시뮬레이션 승률 최소 (%)",
            0.0,
            100.0,
            49.9,
            0.1,
            key="min_win_rate_freq518",
            help="sim_win_rate_pct가 이 값 이상일 때만 조건 충족",
        )
    with col_t3:
        min_prefix_freq_fw = st.number_input(
            "prefix 전체 빈도수 최소 (초과일 때만 사용)",
            0,
            100000,
            550,
            1,
            key="min_prefix_freq_550",
            help="해당 prefix의 pred_frequency가 이 값보다 클 때만 예측 사용 (기본 550 초과)",
        )

    st.markdown("---")
    st.markdown("### 🔧 시뮬레이션 예측값 테이블 생성 (필수)")
    st.warning(
        "⚠️ **예측값·시뮬레이션 승률·pred_frequency**는 테이블에서만 조회합니다. "
        "테이블 생성 시 **빈도 기반** 예측과 **시뮬레이션 승률**, **전체 빈도수**가 함께 저장됩니다. "
        "승률이 채워지려면 이전에 시뮬레이션 결과가 저장된 적이 있거나, 동일 윈도우로 한 번 실행해 둔 뒤 테이블을 다시 생성하세요."
    )
    if st.button("예측값 테이블 생성", key="generate_freq518_win50_freq550_predictions", type="secondary"):
        if cutoff_sim is None:
            st.warning("기준 Grid String ID를 선택하세요.")
        else:
            with st.spinner("예측값 테이블 생성 중... (빈도 기반 9~14, threshold=0, 시뮬레이션 승률·빈도수 포함)"):
                try:
                    result = generate_simulation_predictions_table(
                        cutoff_grid_string_id=cutoff_sim,
                        window_sizes=(9, 10, 11, 12, 13, 14),
                        method="빈도 기반",
                        threshold=0,
                        methods=("빈도 기반",),
                    )
                    st.success(f"✅ 예측값 테이블 생성 완료! (저장된 레코드: {result.get('total_saved', 0):,}개)")
                    st.session_state["freq550_predictions_generated"] = True
                except Exception as e:
                    st.error(f"❌ 예측값 테이블 생성 실패: {str(e)}")
                    st.session_state["freq550_predictions_generated"] = False

    st.markdown("---")
    st.markdown("### 시뮬레이션 실행")
    if st.button("시뮬레이션 실행", type="primary", key="run_sim"):
        if cutoff_sim is None:
            st.warning("기준 Grid String ID를 선택하세요.")
        elif not (st.session_state.get("freq550_predictions_generated", False) or n_sim_predictions > 0):
            st.warning(
                "⚠️ 예측값 테이블이 비어 있거나 시뮬레이션 승률이 없을 수 있습니다. "
                "'예측값 테이블 생성'을 실행하거나, 이미 채워진 테이블이 있어야 시뮬레이션을 실행할 수 있습니다."
            )
        else:
            st.session_state["test_cutoff"] = cutoff_sim if cutoff_sim is not None else 0
            st.session_state["test_max_grid_string_id"] = max_sim
            st.session_state["test_thresh_freq"] = thresh_freq_fw
            st.session_state["test_min_win_rate_pct"] = min_win_rate_fw
            st.session_state["test_min_prefix_freq"] = min_prefix_freq_fw
            st.session_state["test_results"] = None
            st.rerun()

    # 실행 트리거: test_cutoff가 있으면 배치 검증 실행 후 결과 저장
    if "test_cutoff" in st.session_state:
        cutoff_sim_run = st.session_state["test_cutoff"]
        min_conf_freq = st.session_state.get("test_thresh_freq", 51.3)
        min_wr = st.session_state.get("test_min_win_rate_pct", 49.9)
        min_pf = st.session_state.get("test_min_prefix_freq", 550)
        max_gid = st.session_state.get("test_max_grid_string_id")
        with st.status("시뮬레이션 실행 중...") as status:
            bar = st.progress(0.0)
            status.text("윈도우 9 빈도+승률+빈도수550 검증 중...")
            bar.progress(0.5)
            res = batch_validate_first_anchor_window9_freq518_win50_freq550_cp(
                cutoff_sim_run,
                threshold=0,
                min_confidence_freq=min_conf_freq,
                min_win_rate_pct=min_wr,
                min_prefix_freq=min_pf,
                max_grid_string_id=max_gid,
            )
            st.session_state["test_results"] = res
            st.session_state["test_run_params"] = {
                "hypothesis_key": HYPOTHESIS_KEY,
                "cutoff_grid_string_id": cutoff_sim_run,
                "window_sizes": [9],
                "method": "빈도 기반",
                "threshold": 0,
            }
            bar.progress(1.0)
            status.text("완료")
        if "test_cutoff" in st.session_state:
            del st.session_state["test_cutoff"]
        st.rerun()

    # 결과 표시
    if "test_results" in st.session_state and st.session_state["test_results"] is not None:
        st.markdown("---")
        st.markdown("## 시뮬레이션 결과")
        res = st.session_state["test_results"]
        rr = res.get("results", [])
        sm = res.get("summary", {})

        if not rr:
            st.info("검증 결과가 없습니다.")
        else:
            if "last_save_success" in st.session_state:
                info = st.session_state.pop("last_save_success")
                run_id = info.get("run_id", "")
                st.success(f"✅ **결과 저장 완료** — run_id: `{run_id}` (results_db/sim_results_change_point.db)")

            col1, col2, col3, col4, col5 = st.columns(5)
            with col1:
                st.metric("최대 연속 불일치", f"{sm.get('max_consecutive_failures', 0)}회")
            with col2:
                st.metric("평균 최대 연속 불일치", f"{sm.get('avg_max_consecutive_failures', 0):.2f}회")
            with col3:
                st.metric("평균 정확도", f"{sm.get('avg_accuracy', 0):.2f}%")
            with col4:
                st.metric("총 예측 횟수", f"{sm.get('total_predictions', 0):,}")
            with col5:
                st.metric("스킵 횟수", f"{sm.get('total_skipped', 0):,}")

            run_params = st.session_state.get("test_run_params")
            if run_params and run_params.get("hypothesis_key") == HYPOTHESIS_KEY:
                if st.button("결과 저장", key="save_results_btn", type="secondary", use_container_width=True):
                    try:
                        run_id = save_run_results(
                            run_meta=run_params,
                            results=rr,
                            summary=sm,
                        )
                        st.session_state["last_save_success"] = {"run_id": run_id}
                        st.rerun()
                    except Exception as e:
                        st.error(f"저장 실패: {e}")

            # 최대 연속 불일치별 케이스 개수
            st.markdown("#### 최대 연속 불일치별 케이스 개수")
            failure_counts = {}
            for r in rr:
                failures = r["max_consecutive_failures"]
                failure_counts[failures] = failure_counts.get(failures, 0) + 1
            failure_stats = [
                {"최대 연속 불일치": f"{f}회", "케이스 개수": failure_counts[f], "비율": f"{failure_counts[f] / len(rr) * 100:.1f}%"}
                for f in sorted(failure_counts.keys(), reverse=True)
            ]
            if failure_stats:
                st.dataframe(pd.DataFrame(failure_stats), use_container_width=True, hide_index=True)

            # 상세 결과 테이블
            st.markdown("#### 상세 결과")
            grid_string_dict = {}
            if rr:
                grid_string_ids = [r["grid_string_id"] for r in rr]
                conn = get_change_point_db_connection()
                try:
                    df_grid = pd.read_sql_query(
                        "SELECT id, grid_string FROM preprocessed_grid_strings WHERE id IN ({})".format(
                            ",".join("?" * len(grid_string_ids))
                        ),
                        conn,
                        params=grid_string_ids,
                    )
                    for _, row in df_grid.iterrows():
                        grid_string_dict[row["id"]] = row["grid_string"]
                finally:
                    conn.close()
            rows = [
                {
                    "grid_string_id": r["grid_string_id"],
                    "전체 스트링": grid_string_dict.get(r["grid_string_id"], "N/A"),
                    "최대 연속 불일치": r["max_consecutive_failures"],
                    "정확도": f"{r['accuracy']:.2f}%",
                    "예측 횟수": r["total_predictions"],
                    "스킵": r.get("total_skipped", 0),
                }
                for r in rr
            ]
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

            # grid_string_id 선택 후 상세 히스토리
            grid_string_ids = [r["grid_string_id"] for r in rr]
            selected_gid_idx = st.selectbox(
                "상세 히스토리를 조회할 grid_string_id 선택",
                range(len(grid_string_ids)),
                format_func=lambda i: f"ID {grid_string_ids[i]}",
                key="single_history_gid",
            )
            selected_gid = grid_string_ids[selected_gid_idx]
            selected_result = next((r for r in rr if r["grid_string_id"] == selected_gid), None)

            with st.expander(f"📊 상세 히스토리 (grid_string_id: {selected_gid})", expanded=True):
                if selected_result and selected_result.get("history"):
                    h = selected_result["history"]
                    history_data = []
                    for entry in h:
                        is_correct = entry.get("is_correct")
                        match_status = "✅" if is_correct else ("❌" if is_correct is False else "-")
                        predicted = entry.get("predicted")
                        if predicted is None and entry.get("all_predictions"):
                            first_pred = entry["all_predictions"][0]
                            predicted = first_pred.get("predicted")
                        skipped = entry.get("skipped", False)
                        skip_reason = entry.get("skip_reason", "")
                        sp_val = entry.get("skipped_prediction")
                        skipped_mark = "⏭️" if skipped else ""
                        if skipped and skip_reason:
                            skipped_mark = f"⏭️ ({skip_reason})"
                        predicted_display = (f"{sp_val}{skipped_mark}" if sp_val is not None else f"-{skipped_mark}") if skipped else (f"{predicted}{skipped_mark}" if predicted else "-")
                        entry_reason = entry.get("entry_reason", "") if not skipped else ""
                        history_data.append({
                            "Step": entry.get("step", 0),
                            "Position": entry.get("position", ""),
                            "Anchor": entry.get("anchor", ""),
                            "Window Size": entry.get("window_size", ""),
                            "Prefix": entry.get("prefix", ""),
                            "예측": predicted_display,
                            "실제값": entry.get("actual", "-"),
                            "일치": match_status,
                            "신뢰도": f"{entry.get('confidence', 0):.1f}%" if (predicted is not None or entry.get("confidence") is not None) else "-",
                            "예측 사유": entry_reason,
                            "스킵 사유": skip_reason if skipped else "",
                        })
                    st.dataframe(pd.DataFrame(history_data), use_container_width=True, hide_index=True)
                    st.caption(f"💡 전체 {len(h)}개 히스토리")
                else:
                    st.info("히스토리 데이터가 없습니다.")


if __name__ == "__main__":
    main()
