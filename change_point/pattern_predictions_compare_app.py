"""
pattern_list CSV 기준 3개 예측 테이블 비교 Streamlit 앱.

비교 키: ws9 prefix
- sim: SOURCE_DB · simulation_predictions_change_point · window_size=9
- grid/ngram: profile predictions_db

실행:
  streamlit run change_point/pattern_predictions_compare_app.py
  streamlit run change_point/pattern_predictions_compare_app_list2.py
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import (
    SOURCE_DB,
    TABLE_GRID,
    TABLE_NGRAM,
    TABLE_SIM,
    PatternListProfile,
    get_profile,
)

METHOD = "빈도 기반"
THRESHOLD = 0.0

DEFAULT_PROFILE = "list1"


def to_ws9_prefix(prefix: str, window_size: int) -> str:
    p = _norm_prefix(prefix)
    if not p:
        return ""
    if window_size == 9:
        return p
    if window_size == 10:
        return p[1:] if len(p) > 1 else ""
    if window_size == 12:
        return p[3:] if len(p) > 3 else ""
    raise ValueError(f"unsupported window_size for ws9 normalize: {window_size}")


def _norm_prefix(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    return str(value).strip().lower()


def _norm_pred(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    return s if s in ("b", "p") else None


def load_pattern_pairs(profile: PatternListProfile) -> pd.DataFrame:
    df = pd.read_csv(profile.pattern_csv)
    df["window_size"] = df["window_size"].astype(int)
    df["prefix"] = df["prefix"].map(_norm_prefix)

    ws10 = df[df["window_size"] == 10].reset_index(drop=True)
    ws12 = df[df["window_size"] == 12].reset_index(drop=True)
    if len(ws10) != len(ws12):
        raise ValueError(
            f"{profile.pattern_csv.name} ws10/ws12 행 수 불일치: "
            f"ws10={len(ws10)}, ws12={len(ws12)}"
        )

    merged = pd.DataFrame({"ws10_full": ws10["prefix"], "ws12_full": ws12["prefix"]})
    merged["ws9_core"] = merged["ws10_full"].map(lambda p: to_ws9_prefix(p, 10))
    merged["ws9_from12"] = merged["ws12_full"].map(lambda p: to_ws9_prefix(p, 12))
    merged["pad10"] = merged["ws10_full"].str[:1]
    merged["pad12"] = merged["ws12_full"].str[:3]
    merged["prefix_ext"] = merged["ws12_full"].str[:2]
    merged["core_match"] = merged["ws9_core"] == merged["ws9_from12"]
    merged["prefix_visual"] = merged.apply(
        lambda r: (
            f"w9:{r['ws9_core']} | "
            f"w10:[{r['pad10']}]{r['ws9_core']} | "
            f"w12:[{r['pad12']}]{r['ws9_core']}"
        ),
        axis=1,
    )
    return merged[
        [
            "ws9_core",
            "pad10",
            "pad12",
            "prefix_ext",
            "ws10_full",
            "ws12_full",
            "prefix_visual",
            "core_match",
        ]
    ].reset_index(drop=True)


def load_predictions(
    db_path,
    table: str,
    window_size: int | None = None,
) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    try:
        q = f"""
            SELECT window_size, prefix, predicted_value, confidence, pred_frequency
            FROM {table}
            WHERE method = ? AND threshold = ?
        """
        params: list = [METHOD, THRESHOLD]
        if window_size is not None:
            q += " AND window_size = ?"
            params.append(window_size)
        df = pd.read_sql_query(q, conn, params=params)
    finally:
        conn.close()

    if df.empty:
        return df
    df["prefix"] = df["prefix"].map(_norm_prefix)
    df["predicted_value"] = df["predicted_value"].map(_norm_pred)
    df["ws9_core"] = df.apply(
        lambda r: to_ws9_prefix(r["prefix"], int(r["window_size"])), axis=1
    )
    return df


def _pick_row(df: pd.DataFrame, key_col: str, key: str) -> dict:
    if df.empty or not key:
        return {}
    sub = df[df[key_col] == key]
    if sub.empty:
        return {}
    r = sub.iloc[0]
    return {
        "pred": r["predicted_value"],
        "conf": r["confidence"],
        "freq": r["pred_frequency"],
    }


def build_comparison_df(profile_name: str = DEFAULT_PROFILE) -> tuple[pd.DataFrame, dict]:
    profile = get_profile(profile_name)
    patterns = load_pattern_pairs(profile)
    meta: dict = {
        "pattern_rows": len(patterns),
        "warnings": [],
        "profile": profile.name,
    }

    if patterns.empty:
        return pd.DataFrame(), meta

    bad_core = patterns[~patterns["core_match"]]
    if not bad_core.empty:
        meta["warnings"].append(
            f"ws10[1:]/ws12[3:] → ws9 불일치 {len(bad_core)}건"
        )

    sim_all = load_predictions(SOURCE_DB, TABLE_SIM, window_size=9)
    grid_all = load_predictions(profile.predictions_db, TABLE_GRID)
    ngram_all = load_predictions(profile.predictions_db, TABLE_NGRAM, window_size=12)

    grid10 = grid_all[grid_all["window_size"] == 10].copy()
    grid12 = grid_all[grid_all["window_size"] == 12].copy()

    rows = []
    for _, pat in patterns.iterrows():
        ws9 = pat["ws9_core"]
        ws10 = pat["ws10_full"]
        ws12 = pat["ws12_full"]

        sim = _pick_row(sim_all, "prefix", ws9)
        g10 = _pick_row(grid10, "prefix", ws10)
        g12 = _pick_row(grid12, "prefix", ws12)
        ng = _pick_row(ngram_all, "prefix", ws12)

        preds = {
            "sim": sim.get("pred"),
            "grid10": g10.get("pred"),
            "grid12": g12.get("pred"),
            "ngram12": ng.get("pred"),
        }
        present = [v for v in preds.values() if v is not None]
        agree_all = len(set(present)) <= 1 and len(present) >= 2

        new_preds = [g10.get("pred"), g12.get("pred"), ng.get("pred")]
        new_present = [v for v in new_preds if v is not None]
        agree_new_three = len(new_present) == 3 and len(set(new_present)) == 1

        missing = []
        if not sim:
            missing.append("sim")
        if not g10:
            missing.append("grid10")
        if not g12:
            missing.append("grid12")
        if not ng:
            missing.append("ngram12")

        rows.append(
            {
                "ws9_core": ws9,
                "pad10": pat["pad10"],
                "pad12": pat["pad12"],
                "prefix_ext": pat["prefix_ext"],
                "ws10_full": ws10,
                "ws12_full": ws12,
                "prefix_visual": pat["prefix_visual"],
                "sim_pred": sim.get("pred"),
                "sim_conf": sim.get("conf"),
                "sim_freq": sim.get("freq"),
                "grid10_pred": g10.get("pred"),
                "grid10_conf": g10.get("conf"),
                "grid10_freq": g10.get("freq"),
                "grid12_pred": g12.get("pred"),
                "grid12_conf": g12.get("conf"),
                "grid12_freq": g12.get("freq"),
                "ngram12_pred": ng.get("pred"),
                "ngram12_conf": ng.get("conf"),
                "ngram12_freq": ng.get("freq"),
                "ngram_ws9_ok": to_ws9_prefix(ws12, 12) == ws9 if ng else True,
                "agree_all": agree_all if len(present) >= 2 else None,
                "agree_new_three": agree_new_three if len(new_present) == 3 else None,
                "agree_sim_grid10": sim.get("pred") == g10.get("pred")
                if sim.get("pred") and g10.get("pred")
                else None,
                "agree_sim_ngram": sim.get("pred") == ng.get("pred")
                if sim.get("pred") and ng.get("pred")
                else None,
                "agree_grid10_ngram": g10.get("pred") == ng.get("pred")
                if g10.get("pred") and ng.get("pred")
                else None,
                "missing_sources": ", ".join(missing) if missing else "",
            }
        )

    cmp_df = pd.DataFrame(rows)
    meta["hits"] = {
        "sim": int(cmp_df["sim_pred"].notna().sum()),
        "grid10": int(cmp_df["grid10_pred"].notna().sum()),
        "grid12": int(cmp_df["grid12_pred"].notna().sum()),
        "ngram12": int(cmp_df["ngram12_pred"].notna().sum()),
    }
    meta["agree_all"] = int(cmp_df["agree_all"].eq(True).sum())
    meta["agree_new_three"] = int(cmp_df["agree_new_three"].eq(True).sum())
    meta["table_counts"] = _table_row_counts(profile)
    return cmp_df, meta


def _table_row_counts(profile: PatternListProfile) -> dict[str, int]:
    counts = {}
    for label, db_path, tables in (
        ("sim", SOURCE_DB, [TABLE_SIM]),
        ("preds", profile.predictions_db, [TABLE_GRID, TABLE_NGRAM]),
    ):
        conn = sqlite3.connect(db_path)
        try:
            for name in tables:
                try:
                    cur = conn.execute(f"SELECT COUNT(*) FROM {name}")
                    counts[name] = cur.fetchone()[0]
                except sqlite3.OperationalError:
                    counts[name] = -1
        finally:
            conn.close()
    return counts


def _format_display_df(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if col.endswith("_conf") and out[col].dtype in ("float64", "float32"):
            out[col] = out[col].apply(lambda x: round(x, 2) if pd.notna(x) else "-")
        elif col.endswith("_freq"):
            out[col] = out[col].apply(lambda x: int(x) if pd.notna(x) else "-")
        elif col.endswith("_pred"):
            out[col] = out[col].apply(lambda x: x if pd.notna(x) and x else "-")
    bool_cols = [c for c in out.columns if c.startswith("agree_") or c.endswith("_ws9_ok")]
    for col in bool_cols:
        out[col] = out[col].map({True: "Y", False: "N", None: "-", pd.NA: "-"})
    return out


def _aggregate_by_ext(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    agg_rows = []
    for ext, grp in df.groupby("prefix_ext", sort=True):
        def _rate(col):
            sub = grp[col].dropna()
            return round(sub.eq(True).sum() / len(sub) * 100, 1) if len(sub) else None

        agg_rows.append(
            {
                "prefix_ext": ext,
                "count": len(grp),
                "sim_hit": int(grp["sim_pred"].notna().sum()),
                "grid10_hit": int(grp["grid10_pred"].notna().sum()),
                "grid12_hit": int(grp["grid12_pred"].notna().sum()),
                "ngram12_hit": int(grp["ngram12_pred"].notna().sum()),
                "agree_all_pct": _rate("agree_all"),
                "agree_sim_ngram_pct": _rate("agree_sim_ngram"),
            }
        )
    return pd.DataFrame(agg_rows)


def _build_summary_table(df: pd.DataFrame) -> pd.DataFrame:
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
            "미조회": df["missing_sources"].apply(lambda x: x if x else "-"),
        }
    )


def main(profile_name: str = DEFAULT_PROFILE) -> None:
    profile = get_profile(profile_name)
    title_suffix = profile.name.upper()

    st.set_page_config(
        page_title=f"예측 테이블 3-way 비교 ({title_suffix})",
        page_icon="📊",
        layout="wide",
    )
    st.title(f"예측 테이블 3-way 비교 ({title_suffix})")
    st.caption(
        f"profile={profile.name} · sim={SOURCE_DB.name} · "
        f"preds={profile.predictions_db.name} · ws9 비교"
    )

    if not profile.pattern_csv.is_file():
        st.error(f"pattern CSV 없음: {profile.pattern_csv}")
        return
    if not profile.predictions_db.is_file():
        st.error(f"predictions DB 없음: {profile.predictions_db}")
        return

    cmp_df, meta = build_comparison_df(profile_name)
    if cmp_df.empty:
        st.warning("비교 데이터를 만들 수 없습니다.")
        return

    for w in meta.get("warnings", []):
        st.warning(w)

    hits = meta["hits"]
    tc = meta["table_counts"]

    c1, c2, c3, c4, c5, c6, c7 = st.columns(7)
    c1.metric("pattern_list", meta["pattern_rows"])
    c2.metric("sim (ws9)", f"{hits['sim']}/{meta['pattern_rows']}")
    c3.metric("grid10", f"{hits['grid10']}/{meta['pattern_rows']}")
    c4.metric("grid12", f"{hits['grid12']}/{meta['pattern_rows']}")
    c5.metric("ngram12", f"{hits['ngram12']}/{meta['pattern_rows']}")
    c6.metric("전체 일치", meta["agree_all"])
    c7.metric("신규 3-way 일치", meta["agree_new_three"])

    with st.expander("테이블 전체 row count"):
        st.json(tc)

    f1, f2, f3 = st.columns([1, 1, 3])
    with f1:
        mismatch_only = st.checkbox("불일치만", value=False)
    with f2:
        ext_filter = st.selectbox("ws12 앞2글자", ["전체", "bp", "pb"], index=0)
    with f3:
        search = st.text_input("prefix 검색 (ws9 / ws10 / ws12)", "")

    filtered = cmp_df.copy()
    if ext_filter != "전체":
        filtered = filtered[filtered["prefix_ext"] == ext_filter]
    if mismatch_only:
        filtered = filtered[filtered["agree_all"] == False]  # noqa: E712
    if search.strip():
        q = search.strip().lower()
        filtered = filtered[
            filtered["ws9_core"].str.contains(q, na=False)
            | filtered["ws10_full"].str.contains(q, na=False)
            | filtered["ws12_full"].str.contains(q, na=False)
        ]

    tab1, tab2, tab3 = st.tabs(["취합 비교", "윈도우별 확장", "prefix_ext 집계"])

    with tab1:
        st.markdown("### 취합 비교 — simulation ws9 prefix 기준")
        show = _build_summary_table(filtered)
        show.insert(0, "No", range(1, len(show) + 1))
        st.dataframe(show, use_container_width=True, hide_index=True)
        st.caption(f"표시 {len(show)} / {len(cmp_df)}행")

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
            "missing_sources",
        ]
        show2 = _format_display_df(filtered[ext_cols])
        show2.insert(0, "No", range(1, len(show2) + 1))
        st.dataframe(show2, use_container_width=True, hide_index=True)

    with tab3:
        st.markdown("### ws12 prefix_ext별 집계")
        st.dataframe(_aggregate_by_ext(cmp_df), use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main(DEFAULT_PROFILE)
