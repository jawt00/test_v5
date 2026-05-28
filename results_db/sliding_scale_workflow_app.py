"""
슬라이딩 스케일 조건 도출 및 6연패 검증 — 실행 순서 가이드 웹앱

실행 순서를 한 화면에서 보여 주고, 각 단계를 실행해 결과를 바로 확인할 수 있습니다.

실행 (프로젝트 루트):
  streamlit run results_db/sliding_scale_workflow_app.py
"""

import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_results_db = _root / "results_db"
_change_point = _root / "change_point"
for p in (_root, _results_db, _change_point):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

import streamlit as st
import pandas as pd
import json

st.set_page_config(
    page_title="슬라이딩 스케일 워크플로우",
    page_icon="📋",
    layout="wide",
)

# 기본 경로
DEFAULT_DB = _results_db / "sim_results_change_point.db"
DEFAULT_CSV = _results_db / "skipped_steps.csv"
DEFAULT_CONFIG = _results_db / "derived_sliding_config.json"
SOURCE_HYPOTHESIS = "first_anchor_window9_freq518_win50"
TARGET_HYPOTHESIS = "first_anchor_window9_sliding_scale"
RUN_LIMIT = 20


def show_execution_order():
    st.markdown("## 의도 (핵심)")
    st.markdown("""
    1. **스킵된 스텝을 분석**해서, **놓친 기회**(스킵했지만 맞았을 스텝)가 나오는 **빈도 신뢰도·시뮬 승률 조건**을 추출한다.  
    2. **추출한 조건과 기존 조건을 모두 적용**해서 점진적으로 검증한다.  
    3. **6연패가 발생하는지** 확인한다. **(핵심 검증)**  
    4. **6연패가 없으면** 그 조건을 **추가 조건**으로 제시한다. (추가 조건으로 진입 기회는 늘어나고, 6연패가 나오지 않는지가 핵심이다.)
    """)
    st.markdown("## 실행 순서")
    st.markdown("""
    | 순서 | 단계 | 설명 |
    |------|------|------|
    | **0** | (선택) 백필 | step_events null 채우기. |
    | **1** | 스킵 스텝 내보내기 | 조건 추출용이면 **sim_win_rate_pct 포함**으로 CSV 저장. |
    | **2** | 조건 추출 | 놓친 기회가 나오는 **빈도 신뢰도·시뮬 승률** 조건 도출 → JSON. |
    | **3** | 점진 검증 | **기존 조건 + 추출된 조건** 모두 적용해 점진 실행 → 저장. |
    | **4** | 6연패 확인 | 연속 6회 실패 건수 확인. **0이면 추가 조건으로 제시 가능.** |
    | **5** | 반복 | 6연패 발생 시 조건 조정 후 2→3→4 다시 실행. |
    """)
    st.caption("전제: 기존 가설 first_anchor_window9_freq518_win50 run 20개, step_events에 스킵/진입·predicted/actual/is_correct 있음.")


def run_backfill_step(db_path, hypothesis_key, limit, dry_run):
    from backfill_step_events_null import run_backfill
    return run_backfill(db_path=db_path, hypothesis_key=hypothesis_key, limit=limit, dry_run=dry_run)


def run_export_step(db_path, hypothesis_key, limit, out_path, with_sim_wr=False):
    from export_skipped_steps_with_sim_wr import run_export
    return run_export(db_path=db_path, hypothesis_key=hypothesis_key, limit=limit, out_path=out_path, with_sim_wr=with_sim_wr)


def run_derive_step(csv_path, conf_safe_low, conf_high, min_success_rate):
    from derive_sliding_scale import derive_config
    return derive_config(
        csv_path,
        conf_safe_low=conf_safe_low,
        conf_high=conf_high,
        min_success_rate_pct=min_success_rate,
    )


def run_progressive_step(db_path, source, target, limit, config_path, dry_run):
    from run_progressive_sliding_scale import run_progressive, load_sliding_config
    sliding_config = None
    if config_path and Path(config_path).exists():
        sliding_config = load_sliding_config(config_path)
    return run_progressive(
        db_path=db_path,
        source_hypothesis=source,
        target_hypothesis=target,
        limit=limit,
        sliding_config=sliding_config,
        dry_run=dry_run,
    )


def run_audit_step(db_path, hypothesis_key, threshold):
    from audit_consecutive_failures import audit_consecutive_failures
    return audit_consecutive_failures(db_path, hypothesis_key=hypothesis_key, failure_threshold=threshold)


def main():
    show_execution_order()
    st.markdown("---")

    db_path = str(DEFAULT_DB)
    if not Path(db_path).exists():
        st.warning(f"결과 DB가 없습니다: {db_path}")
        return

    # ----- 단계 0: 백필 (선택) -----
    st.markdown("### 0. (선택) step_events null 백필")
    if st.button("0. 백필 실행 (스킵 스텝도 predicted/actual/is_correct 채움)", key="btn_backfill"):
        with st.spinner("백필 실행 중..."):
            try:
                res = run_backfill_step(db_path, SOURCE_HYPOTHESIS, RUN_LIMIT, dry_run=False)
                if res.get("error"):
                    st.error(res["error"])
                else:
                    st.success(f"처리 완료: {res['runs_processed']} runs")
            except Exception as e:
                st.error(str(e))
    st.caption("step_events에 이미 값이 채워져 있으면 생략해도 됩니다.")

    # ----- 단계 1: 스킵 스텝 내보내기 -----
    st.markdown("### 1. 스킵 스텝 내보내기")
    st.caption("조건 추출을 하려면 **sim_win_rate_pct 포함**을 켜야 합니다. (run별 예측 테이블 복원 후 시뮬 승률 붙임)")
    with_sim_wr = st.checkbox("조건 추출용 (sim_win_rate_pct 포함)", value=True, key="with_sim_wr")
    out_csv = st.text_input("저장할 CSV 경로", value=str(DEFAULT_CSV), key="out_csv")
    if st.button("1. 스킵 스텝 내보내기 실행", key="btn_export"):
        with st.spinner("스킵 스텝 추출 중..." + (" (sim_win_rate_pct 붙이는 중…)" if with_sim_wr else "")):
            try:
                rows = run_export_step(db_path, SOURCE_HYPOTHESIS, RUN_LIMIT, out_csv, with_sim_wr=with_sim_wr)
                st.success(f"내보냄: {len(rows)} 행 → {out_csv}" + (" (sim_win_rate_pct 포함)" if with_sim_wr else ""))
                if rows:
                    df = pd.DataFrame(rows[:100])
                    st.dataframe(df, use_container_width=True)
                    if len(rows) > 100:
                        st.caption(f"상위 100행만 표시 (전체 {len(rows)}행)")
            except Exception as e:
                st.error(str(e))

    # ----- 단계 2: 조건 추출 -----
    st.markdown("### 2. 놓친 기회가 나오는 빈도 신뢰도·시뮬 승률 조건 추출")
    with st.expander("**설정 설명** (conf_safe_low, conf_high, 목표 성공률)"):
        st.markdown("""
        - **conf_safe_low (기본 51.3)**  
          스킵 스텝을 **신뢰도(confidence) 구간**으로 나눌 때 쓰는 경계입니다.  
          - **낮은 구간**: confidence **≤ 이 값** → 원래 진입 조건(예: 51.3 초과)을 못 채워 스킵된 구간.  
          - 이 구간은 나중에 슬라이딩 스케일에서 **승률 기준을 더 높게**(wr_strict) 쓸 수 있는 구간입니다.

        - **conf_high (기본 54.0)**  
          **높은 신뢰도** 구간의 시작입니다.  
          - **중간 구간**: conf_safe_low **< confidence < conf_high** → 기존 “안전” 구간.  
          - **높은 구간**: confidence **≥ 이 값** → 승률 기준을 조금 완화(wr_relaxed)해도 되는 구간.

        - **목표 성공률(%) (기본 50)**  
          CSV에 시뮬 승률(sim_win_rate_pct)이 있을 때만 사용됩니다.  
          “이 구간에서 **진입했을 때** 성공한 비율이 이 % 이상이 되도록” 최소 승률(wr_*)을 제안할 때 쓰는 기준입니다.  
          예: 50이면 “진입한 스텝 중 절반 이상은 맞았으면 한다”는 의미.  
          현재는 스킵 스텝 CSV에 sim_win_rate_pct가 없으므로, 도출 결과의 **wr_strict / wr_safe / wr_relaxed**는 모듈 기본값(52 / 49.9 / 48)으로 나옵니다. 구간별 **스킵 건수·놓친 기회 수**만 데이터 기준으로 집계됩니다.
        """)
    input_csv = st.text_input("스킵 스텝 CSV 경로", value=str(DEFAULT_CSV), key="input_csv")
    out_config = st.text_input("저장할 config JSON 경로", value=str(DEFAULT_CONFIG), key="out_config")
    col2a, col2b, col2c = st.columns(3)
    with col2a:
        conf_safe_low = st.number_input("conf_safe_low", 0.0, 100.0, 51.3, 0.1, key="conf_safe_low", help="이 값 이하 = 낮은 신뢰도 구간")
    with col2b:
        conf_high = st.number_input("conf_high", 0.0, 100.0, 54.0, 0.1, key="conf_high", help="이 값 이상 = 높은 신뢰도 구간")
    with col2c:
        min_success_rate = st.number_input("목표 성공률(%)", 0.0, 100.0, 50.0, 0.1, key="min_success_rate", help="sim_wr 있을 때 진입 시 목표 성공률")
    if st.button("2. 조건 도출 실행", key="btn_derive"):
        if not Path(input_csv).exists():
            st.warning(f"CSV가 없습니다. 1단계를 먼저 실행하세요: {input_csv}")
        else:
            with st.spinner("도출 중..."):
                try:
                    config = run_derive_step(input_csv, conf_safe_low, conf_high, min_success_rate)
                    meta = config.get("_meta", {})
                    st.success("도출 완료")
                    st.markdown("**구간별 스킵 건수 / 놓친 기회(is_correct=1)**")
                    st.write(pd.DataFrame([
                        {"구간": "low (conf≤51.3)", "스킵 건수": meta.get("n_low", 0), "놓친 기회": meta.get("missed_opportunities_low", 0)},
                        {"구간": "mid (51.3<conf<54)", "스킵 건수": meta.get("n_mid", 0), "놓친 기회": meta.get("missed_opportunities_mid", 0)},
                        {"구간": "high (conf≥54)", "스킵 건수": meta.get("n_high", 0), "놓친 기회": meta.get("missed_opportunities_high", 0)},
                    ]))
                    st.markdown("**도출된 설정**")
                    cfg_display = {k: v for k, v in config.items() if not k.startswith("_")}
                    st.json(cfg_display)
                    if out_config:
                        with open(out_config, "w", encoding="utf-8") as f:
                            json.dump(config, f, indent=2, ensure_ascii=False)
                        st.caption(f"저장: {out_config}")
                except Exception as e:
                    st.error(str(e))

    # ----- 단계 3: 점진 검증 (기존 + 추출 조건) -----
    st.markdown("### 3. 기존 조건 + 추출된 조건 모두 적용 — 점진 검증")
    config_path = st.text_input("사용할 config JSON", value=str(DEFAULT_CONFIG), key="config_path")
    dry_run = st.checkbox("저장 없이 검증만 (dry-run)", value=False, key="dry_run")
    if st.button("3. 점진 실행", key="btn_progressive"):
        with st.spinner("run 순서대로 예측 테이블 생성 → 검증 → 저장..."):
            try:
                res = run_progressive_step(db_path, SOURCE_HYPOTHESIS, TARGET_HYPOTHESIS, RUN_LIMIT, config_path, dry_run)
                if res.get("error"):
                    st.error(res["error"])
                else:
                    st.success(f"처리: {res['runs_processed']} runs, 저장: {len(res['run_ids_saved'])} runs")
                    if res.get("run_ids_saved"):
                        st.caption("run_id: " + ", ".join(r[:12] + "..." for r in res["run_ids_saved"][:5]))
            except Exception as e:
                st.error(str(e))

    # ----- 단계 4: 6연패 확인 (핵심) -----
    st.markdown("### 4. 6연패 발생 여부 확인 (핵심)")
    st.caption("건수 0이면 추출한 조건을 **추가 조건**으로 제시 가능. 추가 조건 적용 시 진입 기회는 늘고, 6연패가 나오지 않는지가 핵심이다.")
    audit_hypothesis = st.text_input("감사할 가설 키", value=TARGET_HYPOTHESIS, key="audit_hypothesis")
    if st.button("4. 6연패 전수 조사 실행", key="btn_audit"):
        with st.spinner("감사 중..."):
            try:
                result = run_audit_step(db_path, audit_hypothesis, 6)
                count = result["count"]
                if count == 0:
                    st.success("**연속 6회 실패 발생 건수: 0** — 추출한 조건을 **추가 조건**으로 제시해도 됨.")
                else:
                    st.warning(f"**연속 6회 실패 발생 (run_id, grid_string_id) 건수: {count}** — 조건 조정 후 2→3→4 다시 실행.")
                    df_v = pd.DataFrame(result["violators"])
                    st.dataframe(df_v, use_container_width=True)
            except Exception as e:
                st.error(str(e))

    st.markdown("---")
    st.markdown("**요약**: 1(조건 추출용이면 sim_win_rate_pct 포함) → 2(조건 추출) → 3(기존+추출 조건 점진 검증) → 4(6연패 확인). 4에서 건수 0이면 추가 조건으로 제시 가능.")


if __name__ == "__main__":
    main()
