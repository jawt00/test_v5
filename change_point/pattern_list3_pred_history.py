"""simulation_predictions_change_point 갱신 이력 와이드 테이블 빌더.

예측값 + final_rule + rule_confidence/conf_source 이력을 오래된 순(왼쪽)으로 구성.
list3 앱 전용. pattern_list3_snapshot / refresh 와 import 순환을 피하기 위해 분리.

MODULE_API = 4  # (pred_wide, rule_wide, conf_wide, source_wide, meta) 반환
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from pattern_list_profiles import PatternListProfile
from pattern_list3_sim_predictions import METHOD, TABLE_SIM, THRESHOLD, WINDOW_SIZE

MODULE_API = 4

TABLE_RUNS = "prediction_build_runs"
TABLE_SNAPSHOTS = "simulation_predictions_change_point_snapshots"


def _fmt_history_pred(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "pass"
    s = str(value).strip().lower()
    if not s:
        return "pass"
    if s in ("b", "p"):
        return s.upper()
    return s.upper()[:1] if s else "pass"


def _fmt_history_rule(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    s = str(value).strip()
    return s if s else "-"


def _fmt_history_conf(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        return f"{float(value):.1f}"
    except (TypeError, ValueError):
        return "-"


def _fmt_history_source(value) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    s = str(value).strip()
    return s if s else "-"


def _rule_display(rule_id: str | None) -> str:
    """R1 → 'R1 · 3-way 일치' 등. list3 모듈을 lazy import."""
    rid = _fmt_history_rule(rule_id)
    if rid == "-":
        return "-"
    try:
        from pattern_list3_final_rules import FINAL_RULE_INFO

        info = FINAL_RULE_INFO.get(rid)
        if info and info.get("label"):
            return str(info["label"])
    except Exception:
        pass
    return rid


def _insert_applied_rule_column(
    df: pd.DataFrame, rule_map: dict[str, str] | None
) -> pd.DataFrame:
    """prefix 바로 오른쪽에 '적용 규칙' (현재 최종값에 쓰인 규칙) 삽입."""
    if df.empty or "prefix" not in df.columns:
        return df
    out = df.copy()
    rules = [
        _rule_display((rule_map or {}).get(str(p).strip().lower()))
        for p in out["prefix"]
    ]
    prefix_idx = list(out.columns).index("prefix")
    if "적용 규칙" in out.columns:
        out = out.drop(columns=["적용 규칙"])
        prefix_idx = list(out.columns).index("prefix")
    out.insert(prefix_idx + 1, "적용 규칙", rules)
    return out


def _column_label_for_run(created_at: str, run_id: str, used: set[str]) -> str:
    """시간으로 식별. 동일 시각이면 run_id 접미사를 붙인다."""
    base = str(created_at or "").strip() or run_id
    label = base
    if label in used:
        short = run_id[-8:] if len(run_id) >= 8 else run_id
        label = f"{base} ({short})"
    used.add(label)
    return label


def build_prediction_history_wide(
    profile: PatternListProfile,
    *,
    prefixes: list[str] | None = None,
    current_rules: dict[str, str] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame, list[str]]:
    """
    prefix × 스냅샷(오래된→최근) × 현재 테이블 와이드 이력.

    Returns:
        (pred_wide, rule_wide, conf_wide, source_wide, column_meta)
        각 wide: prefix, <created_at>..., 현재 테이블
    """
    empty = (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), [])
    if not profile.predictions_db.is_file():
        return empty

    conn = sqlite3.connect(profile.predictions_db, timeout=20.0)
    try:
        runs_df = pd.read_sql_query(
            f"""
            SELECT run_id, created_at, rule_version
            FROM {TABLE_RUNS}
            WHERE profile = ?
            ORDER BY created_at ASC
            """,
            conn,
            params=[profile.name],
        )
        current = pd.read_sql_query(
            f"""
            SELECT prefix, predicted_value, rule_confidence, conf_source
            FROM {TABLE_SIM}
            WHERE window_size = ? AND method = ? AND threshold = ?
            """,
            conn,
            params=[WINDOW_SIZE, METHOD, THRESHOLD],
        )
        if runs_df.empty:
            snaps = pd.DataFrame(
                columns=[
                    "run_id",
                    "prefix",
                    "predicted_value",
                    "final_rule",
                    "rule_confidence",
                    "conf_source",
                ]
            )
        else:
            run_ids = runs_df["run_id"].astype(str).tolist()
            placeholders = ",".join("?" * len(run_ids))
            snaps = pd.read_sql_query(
                f"""
                SELECT run_id, prefix, predicted_value, final_rule,
                       rule_confidence, conf_source
                FROM {TABLE_SNAPSHOTS}
                WHERE run_id IN ({placeholders})
                  AND window_size = ?
                  AND method = ?
                  AND threshold = ?
                """,
                conn,
                params=[*run_ids, WINDOW_SIZE, METHOD, THRESHOLD],
            )
    except Exception:
        return empty
    finally:
        conn.close()

    if not current.empty:
        current = current.copy()
        current["prefix"] = current["prefix"].astype(str).str.strip().str.lower()

    if runs_df.empty:
        if current.empty:
            return empty
        prefixes_out = current["prefix"].tolist()
        if prefixes is not None:
            want = {str(p).strip().lower() for p in prefixes if p}
            prefixes_out = [p for p in prefixes_out if p in want]
        cur_pred = dict(
            zip(current["prefix"], current["predicted_value"].map(_fmt_history_pred))
        )
        rule_map = current_rules or {}
        pred_wide = pd.DataFrame(
            {
                "prefix": prefixes_out,
                "현재 테이블": [cur_pred.get(p, "-") for p in prefixes_out],
            }
        )
        rule_wide = pd.DataFrame(
            {
                "prefix": prefixes_out,
                "현재 테이블": [
                    _fmt_history_rule(rule_map.get(p)) for p in prefixes_out
                ],
            }
        )
        cur_conf = {}
        cur_src = {}
        if not current.empty and "rule_confidence" in current.columns:
            cur_conf = dict(
                zip(
                    current["prefix"],
                    current["rule_confidence"].map(_fmt_history_conf),
                )
            )
        if not current.empty and "conf_source" in current.columns:
            cur_src = dict(
                zip(current["prefix"], current["conf_source"].map(_fmt_history_source))
            )
        conf_wide = pd.DataFrame(
            {
                "prefix": prefixes_out,
                "현재 테이블": [cur_conf.get(p, "-") for p in prefixes_out],
            }
        )
        source_wide = pd.DataFrame(
            {
                "prefix": prefixes_out,
                "현재 테이블": [cur_src.get(p, "-") for p in prefixes_out],
            }
        )
        meta = [
            "현재 테이블 = simulation_predictions_change_point",
            "규칙: 현재는 classify_final_rule (스냅샷 없음)",
            "적용 규칙 = prefix 오른쪽 · 현재 최종값에 쓰인 규칙",
        ]
        pred_wide = _insert_applied_rule_column(pred_wide, rule_map)
        rule_wide = _insert_applied_rule_column(rule_wide, rule_map)
        return (
            pred_wide.reset_index(drop=True),
            rule_wide.reset_index(drop=True),
            conf_wide.reset_index(drop=True),
            source_wide.reset_index(drop=True),
            meta,
        )

    used_labels: set[str] = set()
    run_labels: list[tuple[str, str, str]] = []
    for _, r in runs_df.iterrows():
        label = _column_label_for_run(str(r["created_at"]), str(r["run_id"]), used_labels)
        rv = str(r["rule_version"]) if pd.notna(r.get("rule_version")) else ""
        run_labels.append((str(r["run_id"]), label, rv))

    all_prefixes: set[str] = set()
    if not snaps.empty:
        snaps = snaps.copy()
        snaps["prefix"] = snaps["prefix"].astype(str).str.strip().str.lower()
        all_prefixes.update(snaps["prefix"].tolist())
    if not current.empty:
        all_prefixes.update(current["prefix"].tolist())

    if prefixes is not None:
        want = {str(p).strip().lower() for p in prefixes if p}
        all_prefixes &= want

    if not all_prefixes:
        return empty

    prefix_list = sorted(all_prefixes)
    pred_wide = pd.DataFrame({"prefix": prefix_list})
    rule_wide = pd.DataFrame({"prefix": prefix_list})
    conf_wide = pd.DataFrame({"prefix": prefix_list})
    source_wide = pd.DataFrame({"prefix": prefix_list})

    snap_pred_by_run: dict[str, dict] = {}
    snap_rule_by_run: dict[str, dict] = {}
    snap_conf_by_run: dict[str, dict] = {}
    snap_source_by_run: dict[str, dict] = {}
    if not snaps.empty:
        for rid, g in snaps.groupby("run_id"):
            snap_pred_by_run[str(rid)] = dict(
                zip(g["prefix"], g["predicted_value"].map(_fmt_history_pred))
            )
            snap_rule_by_run[str(rid)] = dict(
                zip(g["prefix"], g["final_rule"].map(_fmt_history_rule))
            )
            if "rule_confidence" in g.columns:
                snap_conf_by_run[str(rid)] = dict(
                    zip(g["prefix"], g["rule_confidence"].map(_fmt_history_conf))
                )
            if "conf_source" in g.columns:
                snap_source_by_run[str(rid)] = dict(
                    zip(g["prefix"], g["conf_source"].map(_fmt_history_source))
                )

    meta: list[str] = []
    for i, (rid, label, rv) in enumerate(run_labels, start=1):
        pred_wide[label] = [
            snap_pred_by_run.get(rid, {}).get(p, "-") for p in prefix_list
        ]
        rule_wide[label] = [
            snap_rule_by_run.get(rid, {}).get(p, "-") for p in prefix_list
        ]
        conf_wide[label] = [
            snap_conf_by_run.get(rid, {}).get(p, "-") for p in prefix_list
        ]
        source_wide[label] = [
            snap_source_by_run.get(rid, {}).get(p, "-") for p in prefix_list
        ]
        ver = f" · rule_version={rv}" if rv else ""
        meta.append(f"#{i} {label} ← run `{rid}`{ver} (오래된 순)")

    cur_pred = {}
    cur_conf = {}
    cur_src = {}
    if not current.empty:
        cur_pred = dict(
            zip(current["prefix"], current["predicted_value"].map(_fmt_history_pred))
        )
        if "rule_confidence" in current.columns:
            cur_conf = dict(
                zip(
                    current["prefix"],
                    current["rule_confidence"].map(_fmt_history_conf),
                )
            )
        if "conf_source" in current.columns:
            cur_src = dict(
                zip(current["prefix"], current["conf_source"].map(_fmt_history_source))
            )
    rule_map = current_rules or {}
    pred_wide["현재 테이블"] = [cur_pred.get(p, "-") for p in prefix_list]
    rule_wide["현재 테이블"] = [
        _fmt_history_rule(rule_map.get(p)) for p in prefix_list
    ]
    conf_wide["현재 테이블"] = [cur_conf.get(p, "-") for p in prefix_list]
    source_wide["현재 테이블"] = [cur_src.get(p, "-") for p in prefix_list]
    meta.append("현재 테이블 = simulation_predictions_change_point (가장 오른쪽)")
    meta.append("규칙 이력 = 스냅샷 final_rule · 현재 = classify_final_rule")
    meta.append("rule_confidence / conf_source = 스냅샷·현재 테이블 값")
    meta.append("적용 규칙 = prefix 오른쪽 · 현재 최종값에 쓰인 규칙")

    pred_wide = _insert_applied_rule_column(pred_wide, rule_map)
    rule_wide = _insert_applied_rule_column(rule_wide, rule_map)

    return (
        pred_wide.reset_index(drop=True),
        rule_wide.reset_index(drop=True),
        conf_wide.reset_index(drop=True),
        source_wide.reset_index(drop=True),
        meta,
    )
