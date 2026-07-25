"""pattern_list3 (ws11/ws13) 3-way compare — ws9_core 8자 기준."""

from __future__ import annotations

import sqlite3

import pandas as pd

from pattern_list3_ws9_core import norm_prefix, to_ws9_core
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
DEFAULT_PROFILE = "list3"


def _norm_pred(value) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().lower()
    return s if s in ("b", "p") else None


def load_pattern_pairs(profile: PatternListProfile) -> pd.DataFrame:
    df = pd.read_csv(profile.pattern_csv)
    df["window_size"] = df["window_size"].astype(int)
    df["prefix"] = df["prefix"].map(norm_prefix)

    ws11 = df[df["window_size"] == 11].reset_index(drop=True)
    ws13 = df[df["window_size"] == 13].reset_index(drop=True)
    if len(ws11) != len(ws13):
        raise ValueError(
            f"{profile.pattern_csv.name} ws11/ws13 행 수 불일치: "
            f"ws11={len(ws11)}, ws13={len(ws13)}"
        )

    merged = pd.DataFrame({"ws11_full": ws11["prefix"], "ws13_full": ws13["prefix"]})
    merged["ws9_core"] = merged["ws11_full"].map(lambda p: to_ws9_core(p, 11))
    merged["ws9_from13"] = merged["ws13_full"].map(lambda p: to_ws9_core(p, 13))
    merged["pad11"] = merged["ws11_full"].str[:2]
    merged["pad13"] = merged["ws13_full"].str[:3]
    merged["prefix_ext"] = merged["ws13_full"].str[:2]
    merged["core_match"] = merged["ws9_core"] == merged["ws9_from13"]
    merged["prefix_visual"] = merged.apply(
        lambda r: (
            f"w9:{r['ws9_core']} | "
            f"w11:[{r['pad11']}]{r['ws9_core']} | "
            f"w13:[{r['pad13']}]{r['ws9_core']}"
        ),
        axis=1,
    )
    return merged[
        [
            "ws9_core",
            "pad11",
            "pad13",
            "prefix_ext",
            "ws11_full",
            "ws13_full",
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
    df["prefix"] = df["prefix"].map(norm_prefix)
    df["predicted_value"] = df["predicted_value"].map(_norm_pred)
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


def _alias_for_rules(row: dict) -> dict:
    """list2 final_rules 컬럼명 alias."""
    return {
        **row,
        "grid10_pred": row.get("grid11_pred"),
        "grid10_conf": row.get("grid11_conf"),
        "grid10_freq": row.get("grid11_freq"),
        "grid12_pred": row.get("grid13_pred"),
        "grid12_conf": row.get("grid13_conf"),
        "grid12_freq": row.get("grid13_freq"),
        "ngram12_pred": row.get("ngram13_pred"),
        "ngram12_conf": row.get("ngram13_conf"),
        "ngram12_freq": row.get("ngram13_freq"),
        "agree_sim_grid10": row.get("agree_sim_grid11"),
    }


def build_comparison_df(profile_name: str = DEFAULT_PROFILE) -> tuple[pd.DataFrame, dict]:
    profile = get_profile(profile_name)
    if profile.name != "list3":
        raise ValueError(f"pattern_list3_compare supports list3 only, got {profile_name!r}")

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
            f"ws11[2:]/ws13[4:] → ws9 불일치 {len(bad_core)}건"
        )

    sim_all = load_predictions(SOURCE_DB, TABLE_SIM, window_size=9)
    grid_all = load_predictions(profile.predictions_db, TABLE_GRID)
    ngram_all = load_predictions(profile.predictions_db, TABLE_NGRAM, window_size=13)

    grid11 = grid_all[grid_all["window_size"] == 11].copy()
    grid13 = grid_all[grid_all["window_size"] == 13].copy()

    rows = []
    for _, pat in patterns.iterrows():
        ws9 = pat["ws9_core"]
        ws11 = pat["ws11_full"]
        ws13 = pat["ws13_full"]

        sim = _pick_row(sim_all, "prefix", ws9)
        g11 = _pick_row(grid11, "prefix", ws11)
        g13 = _pick_row(grid13, "prefix", ws13)
        ng = _pick_row(ngram_all, "prefix", ws13)

        new_preds = [g11.get("pred"), g13.get("pred"), ng.get("pred")]
        new_present = [v for v in new_preds if v is not None]
        agree_new_three = len(new_present) == 3 and len(set(new_present)) == 1

        preds = {
            "sim": sim.get("pred"),
            "grid11": g11.get("pred"),
            "grid13": g13.get("pred"),
            "ngram13": ng.get("pred"),
        }
        present = [v for v in preds.values() if v is not None]
        agree_all = len(set(present)) <= 1 and len(present) >= 2

        missing = []
        if not sim:
            missing.append("sim")
        if not g11:
            missing.append("grid11")
        if not g13:
            missing.append("grid13")
        if not ng:
            missing.append("ngram13")

        rows.append(
            {
                "ws9_core": ws9,
                "pad11": pat["pad11"],
                "pad13": pat["pad13"],
                "prefix_ext": pat["prefix_ext"],
                "ws11_full": ws11,
                "ws13_full": ws13,
                "prefix_visual": pat["prefix_visual"],
                "sim_pred": sim.get("pred"),
                "sim_conf": sim.get("conf"),
                "sim_freq": sim.get("freq"),
                "grid11_pred": g11.get("pred"),
                "grid11_conf": g11.get("conf"),
                "grid11_freq": g11.get("freq"),
                "grid13_pred": g13.get("pred"),
                "grid13_conf": g13.get("conf"),
                "grid13_freq": g13.get("freq"),
                "ngram13_pred": ng.get("pred"),
                "ngram13_conf": ng.get("conf"),
                "ngram13_freq": ng.get("freq"),
                "ngram_ws9_ok": to_ws9_core(ws13, 13) == ws9 if ng else True,
                "agree_all": agree_all if len(present) >= 2 else None,
                "agree_new_three": agree_new_three if len(new_present) == 3 else None,
                "agree_sim_grid11": sim.get("pred") == g11.get("pred")
                if sim.get("pred") and g11.get("pred")
                else None,
                "agree_sim_ngram": sim.get("pred") == ng.get("pred")
                if sim.get("pred") and ng.get("pred")
                else None,
                "agree_grid11_ngram": g11.get("pred") == ng.get("pred")
                if g11.get("pred") and ng.get("pred")
                else None,
                "missing_sources": ", ".join(missing) if missing else "",
            }
        )

    cmp_df = pd.DataFrame(rows)
    meta["hits"] = {
        "sim": int(cmp_df["sim_pred"].notna().sum()),
        "grid11": int(cmp_df["grid11_pred"].notna().sum()),
        "grid13": int(cmp_df["grid13_pred"].notna().sum()),
        "ngram13": int(cmp_df["ngram13_pred"].notna().sum()),
    }
    meta["agree_all"] = int(cmp_df["agree_all"].eq(True).sum())
    meta["agree_new_three"] = int(cmp_df["agree_new_three"].eq(True).sum())
    meta["table_counts"] = _table_row_counts(profile)
    return cmp_df, meta


def _table_row_counts(profile: PatternListProfile) -> dict[str, int]:
    counts = {}
    for _label, db_path, tables in (
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


def format_display_df(df: pd.DataFrame) -> pd.DataFrame:
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


def aggregate_by_ext(df: pd.DataFrame) -> pd.DataFrame:
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
                "grid11_hit": int(grp["grid11_pred"].notna().sum()),
                "grid13_hit": int(grp["grid13_pred"].notna().sum()),
                "ngram13_hit": int(grp["ngram13_pred"].notna().sum()),
                "agree_all_pct": _rate("agree_all"),
                "agree_sim_ngram_pct": _rate("agree_sim_ngram"),
            }
        )
    return pd.DataFrame(agg_rows)


def cmp_row_for_rules(row: pd.Series) -> pd.Series:
    """list2 final_rules 엔진용 alias row."""
    d = row.to_dict()
    aliased = _alias_for_rules(d)
    return pd.Series(aliased)
