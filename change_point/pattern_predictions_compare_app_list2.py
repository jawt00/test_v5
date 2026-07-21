"""pattern_list2 전용 3-way 비교 앱 (최종 예측 규칙 포함)."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import CHANGE_POINT_DIR, SOURCE_DB, get_profile

try:
    from pattern_list_profiles import LIST2_LIVE_PREDICTIONS_DB
except ImportError:
    LIST2_LIVE_PREDICTIONS_DB = CHANGE_POINT_DIR / "pattern_list2.db"
from pattern_list2_final_rules import (
    FINAL_RULE_INFO,
    RULE_ORDER,
    build_rule_engine,
    classify_final_rule,
    compute_final_prediction,
    normalize_enabled_rules,
)
from pattern_list2_refresh import run_refresh
from extract_pattern_list_data import get_max_source_grid_string_id
from pattern_list2_sim_predictions import (
    TABLE_SIM,
    build_simulation_predictions_df,
    count_simulation_predictions,
)
import pattern_list2_pred_history as _pred_history
from pattern_list2_snapshot import (
    diff_snapshots,
    evaluate_all_runs,
    get_active_run_id,
    get_active_rule_version,
    list_runs,
    load_latest_scores,
    load_snapshot,
    restore_snapshot_to_current,
)
from pattern_predictions_compare_app import (
    _aggregate_by_ext,
    _format_display_df,
    build_comparison_df,
)


def _enabled_rules_from_session() -> frozenset[str]:
    selected = st.session_state.get("refresh_enabled_rules")
    if selected is None:
        return normalize_enabled_rules(RULE_ORDER)
    return normalize_enabled_rules(selected)


def _build_summary_table_list2(
    df: pd.DataFrame,
    *,
    compute_fn=compute_final_prediction,
    classify_fn=classify_final_rule,
) -> pd.DataFrame:
    def _pred(v):
        return v if pd.notna(v) and v else "-"

    def _conf(v):
        return round(v, 2) if pd.notna(v) else "-"

    def _freq(v):
        return int(v) if pd.notna(v) else "-"

    def _yn(v):
        if v is True:
            return "Y"
        if v is False:
            return "N"
        return "-"

    grid12_match = df.apply(
        lambda r: r["sim_pred"] == r["grid12_pred"]
        if pd.notna(r["sim_pred"]) and pd.notna(r["grid12_pred"])
        else None,
        axis=1,
    )

    final_preds = df.apply(compute_fn, axis=1)
    final_rules = df.apply(classify_fn, axis=1)

    return pd.DataFrame(
        {
            "ws9 prefix": df["ws9_core"],
            "sim 예측": df["sim_pred"].map(_pred),
            "sim 신뢰도": df["sim_conf"].map(_conf),
            "sim 빈도": df["sim_freq"].map(_freq),
            "grid10 예측": df["grid10_pred"].map(_pred),
            "grid10 일치": df["agree_sim_grid10"].map(_yn),
            "grid12 예측": df["grid12_pred"].map(_pred),
            "grid12 일치": grid12_match.map(_yn),
            "ngram12 예측": df["ngram12_pred"].map(_pred),
            "ngram12 일치": df["agree_sim_ngram"].map(_yn),
            "전체 일치": df["agree_all"].map(_yn),
            "신규 3-way 일치": df["agree_new_three"].map(_yn),
            "적용 규칙": final_rules,
            "최종 예측": final_preds,
            "미조회": df["missing_sources"].apply(lambda x: x if x else "-"),
        }
    )


def main() -> None:
    profile = get_profile("list2")

    st.set_page_config(
        page_title="예측 테이블 3-way 비교 (LIST2 · TEST)",
        page_icon="📊",
        layout="wide",
    )
    st.title("예측 테이블 3-way 비교 (LIST2 · TEST DB)")
    st.caption(
        f"profile={profile.name} · sim={SOURCE_DB.name} · "
        f"preds={profile.predictions_db.name} · ws9 비교 · 최종 예측 규칙 적용"
    )

    if getattr(profile, "is_test_db", False):
        st.warning(
            f"**테스트 DB 모드** — 갱신/스냅샷/복원은 `{profile.predictions_db}` 에만 기록됩니다. "
            f"라이브 앱용 운영 DB `{LIST2_LIVE_PREDICTIONS_DB.name}` 는 건드리지 않습니다."
        )

    if not profile.pattern_csv.is_file():
        st.error(f"pattern CSV 없음: {profile.pattern_csv}")
        return

    if not profile.predictions_db.is_file():
        st.error(
            f"테스트 predictions DB 없음: `{profile.predictions_db}` · "
            f"`db_backup/pattern_list2_TEST.db` 를 확인하세요."
        )
        return

    max_src_id = get_max_source_grid_string_id()
    st.markdown("### 예측 테이블 갱신 (수동 · TEST DB)")
    st.caption(
        f"원본 `{SOURCE_DB.name}` grid_string 최대 id={max_src_id} · "
        f"갱신 대상 **TEST** `{profile.predictions_db}` · "
        f"라이브 `{LIST2_LIVE_PREDICTIONS_DB}` 와 분리 · 자동 실행 없음"
    )

    if "refresh_enabled_rules" not in st.session_state:
        st.session_state.refresh_enabled_rules = list(RULE_ORDER)

    active_rv = get_active_rule_version(profile)
    st.markdown("#### 적용 규칙 선택")
    rule_cols = st.columns(len(RULE_ORDER))
    selected_rules: list[str] = []
    for col, rule_id in zip(rule_cols, RULE_ORDER):
        info = FINAL_RULE_INFO[rule_id]
        with col:
            if st.checkbox(
                info["label"],
                value=rule_id in st.session_state.refresh_enabled_rules,
                key=f"rule_sel_{rule_id}",
                help=info["description"],
            ):
                selected_rules.append(rule_id)
    st.session_state.refresh_enabled_rules = selected_rules
    preview_compute, preview_classify, preview_rule_version = build_rule_engine(selected_rules)
    st.caption(
        f"미리보기 규칙 버전: `{preview_rule_version}` · "
        f"선택 {len(selected_rules)}/{len(RULE_ORDER)} · "
        f"미매칭 prefix → pass"
    )
    if active_rv:
        st.caption(f"DB active 규칙 버전: `{active_rv}`")
    if not selected_rules:
        st.error("최소 1개 규칙을 선택하세요. (선택 없으면 갱신 불가)")

    def _do_refresh(**kwargs):
        return run_refresh(profile, enabled_rules=selected_rules, **kwargs)

    if st.session_state.get("refresh_confirm_full"):
        st.warning("전량 갱신(--full)을 실행합니다. staging을 비우고 전체 재적재합니다.")
        bc1, bc2 = st.columns(2)
        with bc1:
            if st.button("전량 갱신 확인", type="primary", disabled=not selected_rules):
                with st.spinner("전량 갱신 중..."):
                    res = _do_refresh(full=True)
                st.session_state.refresh_confirm_full = False
                st.session_state.last_refresh_result = res
                st.rerun()
        with bc2:
            if st.button("취소"):
                st.session_state.refresh_confirm_full = False
                st.rerun()

    rc1, rc2, rc3 = st.columns([1, 1, 2])
    with rc1:
        if st.button(
            "예측 테이블 갱신",
            type="primary",
            use_container_width=True,
            disabled=not selected_rules,
        ):
            with st.spinner("증분 갱신 중..."):
                res = _do_refresh(full=False)
            st.session_state.last_refresh_result = res
            st.rerun()
    with rc2:
        if st.button(
            "전량 갱신 (--full)",
            use_container_width=True,
            disabled=not selected_rules,
        ):
            st.session_state.refresh_confirm_full = True
            st.rerun()

    last_res = st.session_state.get("last_refresh_result")
    if last_res:
        if last_res.status == "unchanged":
            st.info(last_res.message)
        elif last_res.status == "error":
            st.error(f"갱신 실패: {last_res.message}")
        else:
            st.success(
                f"갱신 완료 ({last_res.refreshed_at}) · {last_res.message} · "
                f"grid_id {last_res.previous_grid_string_id}→{last_res.max_grid_string_id}"
            )

    cmp_df, meta = build_comparison_df("list2")
    if cmp_df.empty:
        st.warning("비교 데이터를 만들 수 없습니다.")
        return

    for w in meta.get("warnings", []):
        st.warning(w)

    hits = meta["hits"]
    tc = meta["table_counts"]
    final_series = cmp_df.apply(preview_compute, axis=1)
    n_final = int((final_series != "pass").sum())

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
    c1.metric("pattern_list", meta["pattern_rows"])
    c2.metric("sim (ws9)", f"{hits['sim']}/{meta['pattern_rows']}")
    c3.metric("grid10", f"{hits['grid10']}/{meta['pattern_rows']}")
    c4.metric("grid12", f"{hits['grid12']}/{meta['pattern_rows']}")
    c5.metric("ngram12", f"{hits['ngram12']}/{meta['pattern_rows']}")
    c6.metric("전체 일치", meta["agree_all"])
    c7.metric("신규 3-way 일치", meta["agree_new_three"])
    c8.metric("최종 예측", n_final)

    with st.expander("테이블 전체 row count"):
        st.json(tc)

    with st.expander("최종 예측 규칙"):
        for rule_id in RULE_ORDER:
            info = FINAL_RULE_INFO[rule_id]
            on = rule_id in selected_rules
            mark = "✓" if on else "○"
            st.markdown(f"{mark} **{info['label']}** — {info['description']}")
        st.markdown(
            f"""
**R4 · pass** — 선택된 규칙에 해당 없음 → 예측 없음 (pass)

미리보기 규칙 버전: `{preview_rule_version}`
            """
        )

    f1, f2, f3, f4 = st.columns([1, 1, 1, 2])
    with f1:
        mismatch_only = st.checkbox("불일치만", value=False)
    with f2:
        final_only = st.checkbox("최종 예측만 (pass 제외)", value=False)
    with f3:
        ext_filter = st.selectbox("ws12 앞2글자", ["전체", "bp", "pb"], index=0)
    with f4:
        search = st.text_input("prefix 검색 (ws9 / ws10 / ws12)", "")

    filtered = cmp_df.copy()
    filtered["최종 예측"] = filtered.apply(preview_compute, axis=1)
    if ext_filter != "전체":
        filtered = filtered[filtered["prefix_ext"] == ext_filter]
    if mismatch_only:
        filtered = filtered[filtered["agree_all"] == False]  # noqa: E712
    if final_only:
        filtered = filtered[filtered["최종 예측"] != "pass"]
    if search.strip():
        q = search.strip().lower()
        filtered = filtered[
            filtered["ws9_core"].str.contains(q, na=False)
            | filtered["ws10_full"].str.contains(q, na=False)
            | filtered["ws12_full"].str.contains(q, na=False)
        ]

    tab1, tab2, tab3, tab4 = st.tabs(
        ["취합 비교", "윈도우별 확장", "prefix_ext 집계", "스냅샷 이력"]
    )

    with tab1:
        st.markdown("### 취합 비교 — simulation ws9 prefix 기준")
        show = _build_summary_table_list2(
            filtered.drop(columns=["최종 예측"], errors="ignore"),
            compute_fn=preview_compute,
            classify_fn=preview_classify,
        )
        show.insert(0, "No", range(1, len(show) + 1))
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption(f"표시 {len(show)} / {len(cmp_df)}행")

        st.markdown("---")
        st.markdown("### simulation_predictions_change_point 미리보기")
        st.caption(
            f"`{TABLE_SIM}` · window_size=9 · ws9 prefix · "
            f"DB **TEST** `{profile.predictions_db}` · pass → NULL · "
            "저장은 상단 「예측 테이블 갱신」버튼 사용 · 라이브 DB 미사용"
        )

        pred_preview = build_simulation_predictions_df(
            cmp_df, profile, preview_compute
        )
        existing_n = count_simulation_predictions(profile)
        n_pass = int(pred_preview["predicted_value"].isna().sum()) if not pred_preview.empty else 0
        n_pred = len(pred_preview) - n_pass

        pc1, pc2, pc3, pc4 = st.columns(4)
        pc1.metric("저장 대상 (전체)", len(pred_preview))
        pc2.metric("예측 b/p", n_pred)
        pc3.metric("pass (NULL)", n_pass)
        pc4.metric("DB 기존 건수", existing_n)

        if pred_preview.empty:
            st.warning("저장할 prefix가 없습니다.")
        else:
            preview_cols = [
                "window_size",
                "prefix",
                "predicted_value",
                "confidence",
                "b_ratio",
                "p_ratio",
                "pred_frequency",
                "sim_win_rate_pct",
            ]
            st.dataframe(
                pred_preview[preview_cols],
                use_container_width=True,
                hide_index=True,
            )

        if st.button("sim만 재적용 (--sim-only)", type="secondary", disabled=not selected_rules):
            with st.spinner("sim UPSERT 중..."):
                res = _do_refresh(sim_only=True)
            st.session_state.last_refresh_result = res
            st.rerun()

    with tab2:
        st.markdown("### 윈도우별 native prefix")
        ext_cols = [
            "ws9_core",
            "pad10",
            "ws10_full",
            "pad12",
            "ws12_full",
            "prefix_visual",
            "sim_pred",
            "sim_conf",
            "grid10_pred",
            "grid10_conf",
            "grid12_pred",
            "grid12_conf",
            "ngram12_pred",
            "ngram12_conf",
            "ngram_ws9_ok",
            "agree_all",
            "최종 예측",
            "missing_sources",
        ]
        show2 = _format_display_df(filtered[ext_cols])
        show2.insert(0, "No", range(1, len(show2) + 1))
        st.dataframe(show2, use_container_width=True, hide_index=True)

    with tab3:
        st.markdown("### ws12 prefix_ext별 집계")
        st.dataframe(_aggregate_by_ext(cmp_df), use_container_width=True, hide_index=True)

    with tab4:
        st.markdown("### 스냅샷 이력 · 라이브 적중률 · 복원")
        active_run = get_active_run_id(profile)
        if active_run:
            st.caption(f"현재 active run: `{active_run}`")

        ec1, ec2, ec3 = st.columns([1, 1, 2])
        with ec1:
            eval_from = st.text_input("평가 from (created_at)", value="", key="snap_eval_from")
        with ec2:
            eval_to = st.text_input("평가 to (created_at)", value="", key="snap_eval_to")
        with ec3:
            if st.button("적중률 재계산", type="primary", key="snap_eval_btn"):
                with st.spinner("전 run 평가 중..."):
                    evaluate_all_runs(
                        profile,
                        eval_from.strip() or None,
                        eval_to.strip() or None,
                    )
                st.success("평가 완료")
                st.rerun()

        ef = eval_from.strip() or None
        et = eval_to.strip() or None
        runs_df = list_runs(profile)
        scores_df = load_latest_scores(profile, ef, et)

        if runs_df.empty:
            st.info("스냅샷 run 없음 — 「예측 테이블 갱신」 실행 후 생성됩니다.")
        else:
            show_runs = runs_df.copy()
            if not scores_df.empty:
                score_cols = scores_df[
                    [
                        "run_id",
                        "eval_n",
                        "accuracy_pct",
                        "verdict",
                        "hits",
                        "live_n",
                        "skipped_null_pred",
                    ]
                ].rename(
                    columns={
                        "eval_n": "평가 n",
                        "accuracy_pct": "적중률(%)",
                        "verdict": "판정",
                        "hits": "적중",
                        "live_n": "live n",
                        "skipped_null_pred": "pass 제외",
                    }
                )
                show_runs = show_runs.merge(score_cols, on="run_id", how="left")

            show_runs["active"] = show_runs["is_active"].map({1: "Y", 0: ""})
            display_cols = [
                c
                for c in [
                    "run_id",
                    "created_at",
                    "mode",
                    "source_max_grid_id",
                    "snapshot_rows",
                    "적중률(%)",
                    "평가 n",
                    "판정",
                    "active",
                ]
                if c in show_runs.columns
            ]
            st.dataframe(
                show_runs[display_cols],
                use_container_width=True,
                hide_index=True,
            )

            run_ids = runs_df["run_id"].tolist()
            sc1, sc2, sc3 = st.columns([2, 2, 1])
            with sc1:
                sel_run = st.selectbox("Run 상세", run_ids, key="snap_sel_run")
            with sc2:
                diff_b = st.selectbox("Diff 비교 run", run_ids, index=min(1, len(run_ids) - 1), key="snap_diff_b")
            with sc3:
                min_eval = st.number_input("복원 최소 eval_n", min_value=0, value=20, key="snap_min_eval")

            snap_detail = load_snapshot(profile, sel_run)
            if not snap_detail.empty:
                st.markdown("#### Run 스냅샷 미리보기")
                rule_dist = snap_detail["final_rule"].value_counts().reset_index()
                rule_dist.columns = ["final_rule", "count"]
                st.dataframe(rule_dist, hide_index=True, use_container_width=True)
                st.dataframe(
                    snap_detail[
                        [
                            "prefix",
                            "predicted_value",
                            "final_rule",
                            "confidence",
                            "sim_pred",
                            "ngram12_pred",
                            "grid10_pred",
                        ]
                    ],
                    use_container_width=True,
                    hide_index=True,
                )

            if sel_run != diff_b:
                st.markdown("#### Diff (predicted_value 변경)")
                diff_df = diff_snapshots(profile, sel_run, diff_b)
                if diff_df.empty:
                    st.caption("변경된 prefix 없음")
                else:
                    st.dataframe(diff_df, use_container_width=True, hide_index=True)

            score_row = None
            if not scores_df.empty:
                sub = scores_df[scores_df["run_id"] == sel_run]
                if not sub.empty:
                    score_row = sub.iloc[0]

            if score_row is not None and min_eval > 0:
                if int(score_row.get("eval_n") or 0) < min_eval:
                    st.warning(
                        f"평가 n={score_row.get('eval_n')} < 최소 {min_eval} — 복원 시 주의"
                    )

            if st.button("이 스냅샷을 현재 예측 테이블로 복원", key="snap_restore_btn"):
                if score_row is not None and min_eval > 0 and int(score_row.get("eval_n") or 0) < min_eval:
                    st.error(f"eval_n < {min_eval} — 복원 중단")
                else:
                    with st.spinner("복원 중..."):
                        n_restored = restore_snapshot_to_current(profile, sel_run)
                    st.success(f"복원 완료 · {n_restored}행 UPSERT · run `{sel_run}`")
                    st.rerun()

    st.markdown("---")
    st.markdown("### simulation_predictions_change_point 예측값 · 규칙 이력")
    st.caption(
        "왼쪽이 오래된 갱신 · **prefix 옆 = 현재 최종값 적용 규칙** · "
        "이후 칼럼은 갱신 시점 · 맨 오른쪽은 **현재 테이블** · "
        "규칙 버전은 `prediction_build_runs.rule_version` 에 기록"
    )
    current_rules = {
        str(r["ws9_core"]).strip().lower(): preview_classify(r)
        for _, r in filtered.iterrows()
        if pd.notna(r.get("ws9_core")) and str(r["ws9_core"]).strip()
    }
    # Streamlit이 옛 모듈을 캐시해도 최신 pred_history를 쓰도록 reload
    pred_hist = importlib.reload(_pred_history)
    hist_out = pred_hist.build_prediction_history_wide(
        profile,
        prefixes=filtered["ws9_core"].tolist() if not filtered.empty else [],
        current_rules=current_rules,
    )
    if len(hist_out) == 2:
        hist_df, hist_meta = hist_out
        rule_hist_df = pd.DataFrame()
    else:
        hist_df, rule_hist_df, hist_meta = hist_out
    if hist_df.empty:
        st.info("표시할 예측 이력이 없습니다. 갱신 후 스냅샷이 쌓이면 칼럼이 늘어납니다.")
    else:
        st.markdown("#### 예측값")
        show_hist = hist_df.copy()
        show_hist.insert(0, "No", range(1, len(show_hist) + 1))
        st.dataframe(show_hist, use_container_width=True, hide_index=True)

        if not rule_hist_df.empty:
            st.markdown("#### 적용 규칙 (R1–R5, pass=R4)")
            show_rules = rule_hist_df.copy()
            show_rules.insert(0, "No", range(1, len(show_rules) + 1))
            st.dataframe(show_rules, use_container_width=True, hide_index=True)

        st.caption(
            f"표시 {len(hist_df)} prefix · 이력 칼럼 {len(hist_df.columns) - 1}개"
        )
        with st.expander("칼럼 ↔ run / rule_version 매핑"):
            for line in hist_meta:
                st.text(line)


if __name__ == "__main__":
    main()
