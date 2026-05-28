"""
Prefix 규칙 기반 라이브 게임 앱 (v2).

- 규칙: JSON/추가 시 원본 그대로 사용 (prefix, prediction 별도 필드, 정제 없음).
- 매칭: 입력 스트링을 끝에서부터 규칙의 prefix 길이(윈도우)로 쪼개어 가장 먼저 발견되는 prefix 선택.
- actual = 매칭 segment 바로 다음 글자. 예측 vs 실제로 승/패 자동 판정.
- 베팅 기록: SQLite `prefix_rules_bet_history.sqlite3` 테이블 cycle_records (사이클 단위 1행, stages_json에 스테이지별 상세).
"""

import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

st.set_page_config(
    page_title="라이브 게임 v2 (Prefix 규칙)",
    page_icon="🎯",
    layout="wide",
)

MIN_SEGMENT_LEN = 5
CELLS_PER_ROW = 35

# 배당 (고정 상수)
ODDS_P = 2.0   # P 일치
ODDS_B = 1.95  # B 일치
ODDS_DRAW = 8.0  # 무승부

OUTCOMES = ("B 승리", "P 승리", "무승부", "패배")


def outcome_from_grid_match(result: str, prediction: str) -> str:
    """Grid 매칭의 승/무/패 + 규칙 예측(B/P)으로 저장용 outcome 선택."""
    r = (result or "").strip()
    pred = (prediction or "B").upper()
    if pred not in ("B", "P"):
        pred = "B"
    if r == "승":
        return "B 승리" if pred == "B" else "P 승리"
    if r == "무":
        return "무승부"
    if r == "패":
        return "패배"
    return "B 승리"


def _bet_db_path() -> Path:
    return Path(__file__).resolve().parent / "prefix_rules_bet_history.sqlite3"


def _init_bet_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS bet_records (
            bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            stage INTEGER NOT NULL,
            pattern TEXT NOT NULL,
            main_bet REAL NOT NULL,
            draw_bet REAL NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('B 승리', 'P 승리', '무승부', '패배')),
            payout REAL NOT NULL,
            net_profit REAL NOT NULL,
            cum_profit REAL NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_records_cycle ON bet_records(cycle_id)")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cycle_records (
            record_id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            stages_json TEXT NOT NULL,
            stage_count INTEGER NOT NULL,
            total_stake REAL NOT NULL,
            total_payout REAL NOT NULL,
            cycle_net_profit REAL NOT NULL,
            cum_profit REAL NOT NULL,
            saved_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_cycle_records_cycle ON cycle_records(cycle_id)")
    _migrate_bet_outcomes_if_needed(conn)
    conn.commit()


def _migrate_bet_outcomes_if_needed(conn: sqlite3.Connection) -> None:
    """기존 DB(outcome에 '주승')를 B 승리/P 승리 스키마로 재구성. 과거 '주승' 행은 라벨만 'B 승리'로 옮기고 금액은 유지."""
    cur = conn.cursor()
    cur.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='bet_records'")
    row = cur.fetchone()
    if not row or not row[0] or "주승" not in row[0]:
        return
    cur.executescript(
        """
        CREATE TABLE bet_records__mig (
            bet_id INTEGER PRIMARY KEY AUTOINCREMENT,
            cycle_id INTEGER NOT NULL,
            stage INTEGER NOT NULL,
            pattern TEXT NOT NULL,
            main_bet REAL NOT NULL,
            draw_bet REAL NOT NULL,
            outcome TEXT NOT NULL CHECK (outcome IN ('B 승리', 'P 승리', '무승부', '패배')),
            payout REAL NOT NULL,
            net_profit REAL NOT NULL,
            cum_profit REAL NOT NULL
        );
        INSERT INTO bet_records__mig (
            bet_id, cycle_id, stage, pattern, main_bet, draw_bet, outcome, payout, net_profit, cum_profit
        )
        SELECT
            bet_id, cycle_id, stage, pattern, main_bet, draw_bet,
            CASE TRIM(outcome)
                WHEN '주승' THEN 'B 승리'
                WHEN '무승부' THEN '무승부'
                WHEN '패배' THEN '패배'
                ELSE 'B 승리'
            END,
            payout, net_profit, cum_profit
        FROM bet_records;
        DROP TABLE bet_records;
        ALTER TABLE bet_records__mig RENAME TO bet_records;
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_bet_records_cycle ON bet_records(cycle_id)")
    mx_row = conn.execute("SELECT MAX(bet_id) FROM bet_records").fetchone()
    mx = int(mx_row[0]) if mx_row and mx_row[0] is not None else 0
    conn.execute("DELETE FROM sqlite_sequence WHERE name='bet_records'")
    if mx > 0:
        conn.execute("INSERT INTO sqlite_sequence (name, seq) VALUES ('bet_records', ?)", (mx,))


def compute_bet_payout(outcome: str, main_bet: float, draw_bet: float) -> float:
    """B/P 승리: 해당 배당만 적용(무 베팅 미적중 가정). 무승부: 주 Push + 무 배당. 패배: 환수 0."""
    if outcome == "B 승리":
        return main_bet * ODDS_B
    if outcome == "P 승리":
        return main_bet * ODDS_P
    if outcome == "무승부":
        return main_bet + draw_bet * ODDS_DRAW
    return 0.0


def build_stage_entry(
    stage: int,
    pattern: str,
    main_bet: float,
    draw_bet: float,
    outcome: str,
) -> tuple[Optional[dict], Optional[str]]:
    """단일 스테이지 행 dict 또는 (None, 오류 메시지)."""
    pat = (pattern or "").strip()
    if not pat:
        return None, "pattern을 입력하세요."
    if main_bet < 0 or draw_bet < 0:
        return None, "베팅 금액은 0 이상이어야 합니다."
    if main_bet + draw_bet <= 0:
        return None, "주 베팅과 무 베팅 합이 0보다 커야 합니다."
    if outcome not in OUTCOMES:
        return None, "outcome이 올바르지 않습니다."
    if stage < 1 or stage > 6:
        return None, "stage는 1~6이어야 합니다."
    payout = compute_bet_payout(outcome, main_bet, draw_bet)
    net_profit = payout - (main_bet + draw_bet)
    return {
        "stage": int(stage),
        "pattern": pat,
        "main_bet": float(main_bet),
        "draw_bet": float(draw_bet),
        "outcome": outcome,
        "payout": float(payout),
        "net_profit": float(net_profit),
    }, None


def upsert_stage_in_draft(draft: list, entry: dict) -> list:
    """같은 stage가 있으면 교체, 없으면 추가 후 stage 순 정렬."""
    out = [x for x in draft if int(x.get("stage", 0)) != int(entry["stage"])]
    out.append(entry)
    return sorted(out, key=lambda x: int(x["stage"]))


def insert_cycle_record(
    conn: sqlite3.Connection,
    session_cycle_id: int,
    stages: list,
) -> tuple[int, float, float, float]:
    """사이클 1건 저장. 반환: (record_id, total_stake, cycle_net_profit, cum_profit)."""
    if not stages:
        raise ValueError("저장할 스테이지가 없습니다.")
    total_stake = sum(float(s["main_bet"]) + float(s["draw_bet"]) for s in stages)
    total_payout = sum(float(s["payout"]) for s in stages)
    cycle_net = sum(float(s["net_profit"]) for s in stages)
    cur = conn.cursor()
    cur.execute("SELECT COALESCE(SUM(cycle_net_profit), 0) FROM cycle_records")
    prior_cum = float(cur.fetchone()[0])
    cum_profit = prior_cum + cycle_net
    payload = json.dumps(stages, ensure_ascii=False)
    cur.execute(
        """
        INSERT INTO cycle_records
        (cycle_id, stages_json, stage_count, total_stake, total_payout, cycle_net_profit, cum_profit)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(session_cycle_id),
            payload,
            len(stages),
            total_stake,
            total_payout,
            cycle_net,
            cum_profit,
        ),
    )
    conn.commit()
    return cur.lastrowid, total_stake, cycle_net, cum_profit


def load_cycle_records_dataframe(conn: sqlite3.Connection, limit: Optional[int] = 200) -> pd.DataFrame:
    sql = (
        "SELECT record_id, cycle_id, stage_count, total_stake, total_payout, cycle_net_profit, cum_profit, saved_at "
        "FROM cycle_records ORDER BY record_id DESC"
    )
    if limit is not None:
        return pd.read_sql_query(sql + " LIMIT ?", conn, params=(int(limit),))
    return pd.read_sql_query(sql, conn)


BUNDLE_STRATEGY_JSON = Path(__file__).resolve().parent / "strategies" / "hybrid_martin_d_alembert_v1.json"


def parse_bet_strategy_json(raw: str) -> tuple[Optional[dict], Optional[str]]:
    """스테이지별 베팅 JSON 파싱. 성공 시 (data, None), 실패 시 (None, 메시지)."""
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as e:
        return None, str(e)
    if not isinstance(data, dict):
        return None, "루트는 JSON 객체여야 합니다."
    stages = data.get("stages")
    if not isinstance(stages, dict) or not stages:
        return None, "stages 객체가 비어 있지 않아야 합니다."
    return data, None


def stage_main_draw_from_strategy(strategy: dict, stage: int) -> Optional[tuple[float, float]]:
    """strategy['stages'][str(stage)]에서 main_bet, draw_bet."""
    stages = strategy.get("stages")
    if not isinstance(stages, dict):
        return None
    block = stages.get(str(int(stage)))
    if not isinstance(block, dict):
        return None
    try:
        main_b = float(block["main_bet"])
        draw_b = float(block["draw_bet"])
    except (KeyError, TypeError, ValueError):
        return None
    return main_b, draw_b


def sync_session_bet_amounts_from_strategy(ss) -> None:
    """전략이 있으면 현재 stage에 맞춰 bet_save_main / draw를 덮어씀 (수동 변경은 같은 stage 내에서만 유지)."""
    strat = ss.get("bet_strategy")
    if not strat or not isinstance(strat, dict):
        return
    stg = int(ss.get("bet_save_stage", 1))
    pair = stage_main_draw_from_strategy(strat, stg)
    if pair is None:
        return
    ver = int(ss.get("bet_strategy_version", 0))
    if ss.get("_bet_strategy_applied_version") != ver or ss.get("_bet_last_auto_stage") != stg:
        ss["bet_save_main"] = pair[0]
        ss["bet_save_draw"] = pair[1]
        ss["_bet_strategy_applied_version"] = ver
        ss["_bet_last_auto_stage"] = stg


def _prefix_pattern_svg(prefix: str, cell_px: int = 10, r_px: float = 4) -> str:
    """prefix 문자열을 꺽임(문자 바뀌는 지점)에서 줄바꿈·횡으로 적용(각 run을 한 열로 세로 배치). b=빨강, p=파랑. 마지막 문자는 우하단 1/4 원 채움."""
    if not prefix or not all(c in "bp" for c in prefix):
        return ""
    n = len(prefix)
    if n == 0:
        return ""
    # 꺽임에서 끊어서 각 run을 한 열(세로)로 배치 → 횡으로 나열
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
            # 우하단 1/4 원 채움 (같은 색)
            svg_parts.append(
                f'<path d="M{cx+r_px},{cy} A{r_px},{r_px} 0 0 1 {cx},{cy+r_px} L{cx},{cy} Z" fill="{color}"/>'
            )
    svg_parts.append("</svg>")
    return "".join(svg_parts)


def _export_rules_to_json(rules: list) -> str:
    """규칙 리스트를 저장용 JSON 문자열로 변환. id는 제외, prefix·prediction만."""
    data = [
        {"prefix": (r.get("prefix") or "").strip().lower(), "prediction": (r.get("prediction") or "B").upper()}
        for r in rules
        if (r.get("prefix") or "").strip()
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


def _import_rules_from_json(json_str: str) -> tuple[list, Optional[dict]]:
    """JSON 문자열에서 규칙 리스트 복원. id는 새로 부여.
    - 루트가 리스트면 그대로 규칙 배열로 사용 (기존 형식).
    - 루트가 dict이고 'rules' 키가 있으면 rules 배열 사용 (필터 조건 포함 export 호환).
    반환: (규칙 리스트, filter_conditions 또는 None)
    """
    try:
        data = json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return [], None
    filter_conditions = None
    if isinstance(data, dict) and "rules" in data:
        filter_conditions = data.get("filter_conditions")
        data = data["rules"]
    if not isinstance(data, list):
        return [], None
    out = []
    next_id = 1
    for item in data:
        if not isinstance(item, dict):
            continue
        prefix = (item.get("prefix") or "").strip().lower()
        if not prefix:
            continue
        pred = (item.get("prediction") or "B").upper()
        if pred not in ("B", "P"):
            pred = "B"
        out.append({"id": next_id, "prefix": prefix, "prediction": pred})
        next_id += 1
    return out, filter_conditions


def _anchors_from_grid_string(grid_string: str):
    """grid_string에서 change-point(앵커) 위치 리스트 반환."""
    if not grid_string or len(grid_string) < 2:
        return []
    return sorted(set(i for i in range(len(grid_string) - 1) if grid_string[i] != grid_string[i + 1]))


def render_grid_string_and_anchors(grid_string: str, anchors: list = None):
    """Grid String 및 앵커 시각화."""
    if not grid_string:
        st.caption("(Grid String 없음)")
        return
    if anchors is None:
        anchors = _anchors_from_grid_string(grid_string)
    pos_to_anchor_idx = {p: i for i, p in enumerate(anchors)}
    cell_w = "1.85em"
    cell_h = "1.85em"
    base_cell = (
        f"display:inline-block;width:{cell_w};min-width:{cell_w};max-width:{cell_w};"
        f"min-height:{cell_h};height:{cell_h};line-height:{cell_h};"
        "text-align:center;font-family:monospace;vertical-align:middle;"
        "border:1px solid #ccc;box-sizing:border-box;padding:0;font-size:12px;"
    )
    row_style = "margin-bottom:2px;line-height:0;font-size:0;"

    def make_rows(cells: list, n: int = CELLS_PER_ROW):
        return ["".join(cells[start : start + n]) for start in range(0, len(cells), n)]

    char_cell = base_cell + "white-space:nowrap;overflow:hidden;"
    chars = []
    for i, c in enumerate(grid_string):
        bg = "background:#ADD8E6;" if i in anchors else "background:#fff;"
        chars.append(f"<span style='{char_cell}{bg}'>{c}</span>")
    for row_html in make_rows(chars):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    idx_cells = [f"<span style='{base_cell}color:#555;'>{i}</span>" for i in range(len(grid_string))]
    for row_html in make_rows(idx_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    anchor_cells = []
    for i in range(len(grid_string)):
        if i in pos_to_anchor_idx:
            a_idx = pos_to_anchor_idx[i]
            anchor_cells.append(f"<span style='{base_cell}color:#0066cc;font-weight:bold;'>{a_idx}</span>")
        else:
            anchor_cells.append(f"<span style='{base_cell}color:#ccc;'>·</span>")
    for row_html in make_rows(anchor_cells):
        st.markdown(f"<div style='{row_style}'>{row_html}</div>", unsafe_allow_html=True)
    st.caption("위: 문자 | 가운데: 포지션 | 아래: 앵커 인덱스. 연한 파란색=앵커(변화점)")


def find_matching_prefix_at_tail(grid_string: str, rules: list) -> Optional[dict]:
    """입력 스트링을 끝에서부터 윈도우(규칙의 prefix 길이들)로 쪼개어, 가장 먼저 발견되는 prefix 매칭을 반환.

    - 규칙은 원본 그대로 사용: rule["prefix"], rule["prediction"] (정제 없음).
    - end를 끝 인덱스부터 0까지 감소시키며, 각 end에서 규칙에 있는 prefix 길이(긴 것 우선)로
      segment = grid_string[start:end+1] 를 잡아 규칙 prefix와 일치하면 선택.
    - actual = segment 바로 다음 글자 (끝이면 grid_string[-1] 사용).
    """
    if not grid_string or not rules:
        return None
    s = (grid_string or "").strip().lower()
    if len(s) < 2:
        return None

    # 빠른 조회를 위해 prefix -> prediction 매핑(동일 prefix가 여러 개면 사전순 prediction 우선)
    prefix_to_pred: dict[str, str] = {}
    for r in rules:
        p = (r.get("prefix") or "").strip().lower()
        if not p:
            continue
        pred = (r.get("prediction") or "B").upper()
        if pred not in ("B", "P"):
            pred = "B"
        if p not in prefix_to_pred or pred < prefix_to_pred[p]:
            prefix_to_pred[p] = pred

    # 규칙에 등장하는 prefix 길이들 (긴 것 먼저)
    window_sizes = sorted(
        set(len((r.get("prefix") or "").strip().lower()) for r in rules if (r.get("prefix") or "").strip()),
        reverse=True,
    )
    if not window_sizes:
        return None

    # end: 끝 인덱스부터 0까지 감소 (매칭이 끝까지 갈 수 있도록 len(s)-1 포함)
    for end in range(len(s) - 1, -1, -1):
        for window in window_sizes:
            start = end - window + 1
            if start < 0:
                continue
            segment = s[start : end + 1]
            if segment not in prefix_to_pred:
                continue
            pred = prefix_to_pred[segment]
            if end + 1 < len(s):
                actual_raw = s[end + 1]
            else:
                actual_raw = s[-1]
            actual = actual_raw.upper() if actual_raw in "bp" else actual_raw
            result = "승" if pred == actual else "패"
            return {
                "prefix": segment,
                "prediction": pred,
                "actual": actual,
                "result": result,
                "start": start,
                "end": end,
            }
    return None


def main():
    st.title("🎯 라이브 게임 (Prefix 규칙)")
    st.markdown("규칙 추가/제거 · Grid 입력 후 시작 → Grid 및 앵커 시각화 + prefix 매칭 기반 승무패 기록")

    # 규칙용 id 카운터 (초기값: 빈 리스트, 불러오기/수동 추가로만 채움)
    if "prefix_rules_next_id" not in st.session_state:
        st.session_state.prefix_rules_next_id = 1
    if "prefix_rules" not in st.session_state:
        st.session_state.prefix_rules = []
    if "prefix_rules_grid_string" not in st.session_state:
        st.session_state.prefix_rules_grid_string = None
    # Grid 및 매칭/라운드 관련 상태
    if "prefix_rules_grid_string" not in st.session_state:
        st.session_state.prefix_rules_grid_string = None
    if "prefix_rules_current_match" not in st.session_state:
        st.session_state.prefix_rules_current_match = None  # {"prefix","prediction"}
    if "prefix_rules_rounds" not in st.session_state:
        st.session_state.prefix_rules_rounds = []  # [{round_index,prefix,prediction,actual,result}]
    if "prefix_rules_filter_conditions" not in st.session_state:
        st.session_state.prefix_rules_filter_conditions = None
    if "prefix_rules_bet_cycle_id" not in st.session_state:
        st.session_state.prefix_rules_bet_cycle_id = 1
    if "bet_save_pattern" not in st.session_state:
        st.session_state.bet_save_pattern = ""
    if "bet_save_main" not in st.session_state:
        st.session_state.bet_save_main = 1.0
    if "bet_save_draw" not in st.session_state:
        st.session_state.bet_save_draw = 0.0
    if "bet_save_outcome" not in st.session_state:
        st.session_state.bet_save_outcome = OUTCOMES[0]
    if "bet_save_stage" not in st.session_state:
        st.session_state.bet_save_stage = 1
    if "bet_strategy" not in st.session_state:
        st.session_state.bet_strategy = None
    if "bet_strategy_version" not in st.session_state:
        st.session_state.bet_strategy_version = 0
    if "bet_cycle_draft_stages" not in st.session_state:
        st.session_state.bet_cycle_draft_stages = []

    rules = st.session_state.prefix_rules

    # -------------------------------------------------------------------------
    # 상단: Prefix 규칙 (추가·삭제만 가능, 수정 불가, prefix 순 정렬) — 2단·간격 축소
    # -------------------------------------------------------------------------
    st.markdown("""
    <style>
    /* 규칙 영역 세로 간격 더 축소 (폰트 크기는 유지) */
    div[data-testid="stVerticalBlock"] > div { margin-bottom: 0.12rem !important; }
    </style>
    """, unsafe_allow_html=True)
    st.markdown("### Prefix 규칙 (추가·삭제만 가능, prefix 순 정렬)")
    fc = st.session_state.get("prefix_rules_filter_conditions")
    if fc and isinstance(fc, dict):
        parts = []
        if fc.get("max_fail_pct") is not None:
            parts.append(f"5연패 확률 ≤{fc['max_fail_pct']}%")
        if fc.get("min_win_rate_pct") is not None:
            parts.append(f"최소 승률 {fc['min_win_rate_pct']}%")
        if fc.get("min_total_events_win9") is not None or fc.get("min_total_events_win10") is not None:
            w9 = fc.get("min_total_events_win9")
            w10 = fc.get("min_total_events_win10")
            parts.append(f"최소 관측(w9/w10) {w9 or '—'}/{w10 or '—'}")
        if fc.get("min_suffix_confidence_pct") is not None:
            parts.append(f"최소 신뒤도 {fc['min_suffix_confidence_pct']}%")
        if parts:
            st.markdown(
                '<p style="font-size:0.85rem;color:#666;margin-top:0.15rem;margin-bottom:0.5rem;">불러온 필터 조건: '
                + " · ".join(parts)
                + "</p>",
                unsafe_allow_html=True,
            )
    # 정렬: 게임 중일 때는 테일과 일치하는 규칙을 위로 (완전 일치 → 그 외 일치 → 나머지 prefix 순)
    tail_segment = (st.session_state.get("prefix_rules_tail_segment") or "").strip().lower()
    tail_rules = st.session_state.get("prefix_rules_tail_rules") or []
    if tail_segment and len(tail_segment) >= MIN_SEGMENT_LEN and tail_rules:
        matching_ids = {r.get("id") for r in tail_rules}
        exact_prefix = next(
            ((r.get("prefix") or "").strip().lower() for r in tail_rules if ((r.get("prefix") or "").strip().lower() == tail_segment)),
            None,
        )

        def _rule_sort_key(r):
            p = (r.get("prefix") or "").strip().lower()
            is_matching = r.get("id") in matching_ids
            is_exact = exact_prefix is not None and p == exact_prefix
            # False < True → 매칭 먼저, 그중 완전 일치 먼저, 동일 그룹 내 prefix 순
            return (not is_matching, not is_exact, p)

        sorted_rules = sorted(rules, key=_rule_sort_key)
        st.caption("🎯 **테일과 일치하는 규칙**이 위에 표시됩니다 (완전 일치 → 일치 → 나머지, 각각 prefix 순)")
    else:
        sorted_rules = sorted(rules, key=lambda x: (x.get("prefix") or "").strip().lower())
    mid = (len(sorted_rules) + 1) // 2
    left_rules = sorted_rules[:mid]
    right_rules = sorted_rules[mid:]

    to_remove_id = None
    col_left, col_right = st.columns(2)
    with col_left:
        for r in left_rules:
            rid = r.get("id")
            if rid is None:
                continue
            row_prefix = (r.get("prefix") or "").strip().lower()
            row_pred = (r.get("prediction") or "B").upper()
            if row_pred not in ("B", "P"):
                row_pred = "B"
            r1, r2, r3, r4 = st.columns([2, 2, 1, 1])
            with r1:
                st.text(row_prefix or "(빈 prefix)")
            with r2:
                if row_prefix and all(c in "bp" for c in row_prefix):
                    st.markdown(_prefix_pattern_svg(row_prefix), unsafe_allow_html=True)
                else:
                    st.caption("—")
            with r3:
                st.text(row_pred)
            with r4:
                if st.button("삭제", key=f"del_rule_{rid}", type="secondary"):
                    to_remove_id = rid
    with col_right:
        for r in right_rules:
            rid = r.get("id")
            if rid is None:
                continue
            row_prefix = (r.get("prefix") or "").strip().lower()
            row_pred = (r.get("prediction") or "B").upper()
            if row_pred not in ("B", "P"):
                row_pred = "B"
            r1, r2, r3, r4 = st.columns([2, 2, 1, 1])
            with r1:
                st.text(row_prefix or "(빈 prefix)")
            with r2:
                if row_prefix and all(c in "bp" for c in row_prefix):
                    st.markdown(_prefix_pattern_svg(row_prefix), unsafe_allow_html=True)
                else:
                    st.caption("—")
            with r3:
                st.text(row_pred)
            with r4:
                if st.button("삭제", key=f"del_rule_{rid}", type="secondary"):
                    to_remove_id = rid

    if to_remove_id is not None:
        st.session_state.prefix_rules = [r for r in st.session_state.prefix_rules if r.get("id") != to_remove_id]
        st.rerun()

    st.markdown("**새 규칙 추가**")
    new_prefix_key = "new_rule_prefix"
    new_pred_key = "new_rule_pred"
    _clear_flag = "_clear_new_rule_inputs"
    # 위젯 생성 전에만 session_state 수정 (추가 직후 다음 run에서 입력 비우기)
    if st.session_state.get(_clear_flag):
        st.session_state[new_prefix_key] = ""
        st.session_state[new_pred_key] = "B"
        del st.session_state[_clear_flag]
    if new_prefix_key not in st.session_state:
        st.session_state[new_prefix_key] = ""
    if new_pred_key not in st.session_state:
        st.session_state[new_pred_key] = "B"
    c1, c2, c3 = st.columns([3, 1, 1])
    with c1:
        new_prefix = st.text_input("prefix", key=new_prefix_key, label_visibility="collapsed", placeholder="b/p 문자열")
    with c2:
        new_pred = st.selectbox("예측", options=["B", "P"], key=new_pred_key, label_visibility="collapsed")
    with c3:
        add_clicked = st.button("➕ 추가", key="add_prefix_rule")
    if add_clicked and (new_prefix or "").strip():
        new_id = st.session_state.prefix_rules_next_id
        st.session_state.prefix_rules_next_id += 1
        st.session_state.prefix_rules.append({
            "id": new_id,
            "prefix": new_prefix.strip().lower(),
            "prediction": (new_pred or "B").upper(),
        })
        st.session_state[_clear_flag] = True
        st.rerun()

    # 규칙 초기화 / 저장 / 불러오기
    st.markdown("**규칙 초기화 / 저장 / 불러오기**")
    col_reset_rules, col_save, col_load, _ = st.columns([1, 1, 1, 2])
    with col_reset_rules:
        if st.button("🔄 규칙 초기화", key="prefix_rules_reset_default", use_container_width=True, help="규칙을 모두 비우고 초기 상태로 되돌립니다."):
            st.session_state.prefix_rules = []
            st.session_state.prefix_rules_next_id = 1
            st.session_state.prefix_rules_filter_conditions = None
            st.success("규칙을 모두 비웠습니다.")
            st.rerun()
    with col_save:
        save_filename = f"prefix_rules_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
        save_data = _export_rules_to_json(st.session_state.prefix_rules)
        st.download_button(
            "💾 저장",
            data=save_data,
            file_name=save_filename,
            mime="application/json",
            key="prefix_rules_save",
            use_container_width=True,
        )
    with col_load:
        loaded_file = st.file_uploader(
            "불러오기",
            type=["json"],
            key="prefix_rules_upload",
            label_visibility="collapsed",
        )
    apply_load_clicked = st.button("📂 불러오기 적용", key="prefix_rules_apply_load", help="선택한 JSON 파일로 규칙을 교체합니다.")
    if apply_load_clicked and loaded_file is not None:
        try:
            raw = loaded_file.read().decode("utf-8")
        except Exception:
            raw = loaded_file.getvalue().decode("utf-8", errors="replace")
        imported, filter_conditions = _import_rules_from_json(raw)
        if imported:
            next_id = max(r["id"] for r in imported) + 1
            st.session_state.prefix_rules = imported
            st.session_state.prefix_rules_next_id = next_id
            st.session_state.prefix_rules_filter_conditions = filter_conditions
            if "prefix_rules_upload" in st.session_state:
                del st.session_state["prefix_rules_upload"]
            st.success(f"규칙 {len(imported)}개 불러옴.")
            st.rerun()
        else:
            st.warning("유효한 규칙이 없거나 형식이 올바르지 않습니다.")
    elif loaded_file is not None:
        st.caption("파일 선택됨. 위 **불러오기 적용**을 눌러 적용하세요.")

    st.markdown("---")
    st.markdown("## Grid String 입력")

    grid_input = st.text_area(
        "Grid String",
        key="prefix_rules_grid_input",
        height=80,
        placeholder="예: bbppbppbbp...",
        help="시작 시 이 문자열로 Grid와 앵커를 표시하고, B/P로 라이브 업데이트합니다.",
    )

    col_start, col_reset, _ = st.columns([1, 1, 4])
    with col_start:
        if st.button("🎮 시작", type="primary", use_container_width=True, key="prefix_rules_btn_start"):
            s = (grid_input or "").strip().lower()
            if not s:
                st.warning("Grid String을 입력하세요.")
            else:
                allowed = "".join(c for c in s if c in "bp")
                if allowed != s:
                    st.warning("b와 p만 사용됩니다. 다른 문자는 제거됩니다.")
                st.session_state.prefix_rules_grid_string = allowed or s
                gs = st.session_state.prefix_rules_grid_string
                rules_list = [r for r in st.session_state.prefix_rules if (r.get("prefix") or "").strip()]
                st.session_state.prefix_rules_current_match = find_matching_prefix_at_tail(gs, rules_list)
                m = st.session_state.prefix_rules_current_match
                if m:
                    st.session_state.bet_save_pattern = m["prefix"]
                    st.session_state.bet_save_outcome = outcome_from_grid_match(m["result"], m["prediction"])
                st.rerun()
    with col_reset:
        if st.button("🔄 Grid만 초기화", use_container_width=True, key="prefix_rules_btn_reset_grid"):
            st.session_state.prefix_rules_grid_string = None
            st.session_state.prefix_rules_current_match = None
            st.rerun()

    gs = st.session_state.prefix_rules_grid_string
    current_match = st.session_state.prefix_rules_current_match
    rounds = st.session_state.prefix_rules_rounds

    if gs is not None:
        st.markdown("---")
        st.markdown("## Grid String 및 앵커")
        anchors = _anchors_from_grid_string(gs)
        render_grid_string_and_anchors(gs, anchors)

        st.markdown("---")
        st.markdown("## 현재 Grid 기준 prefix 매칭")
        if current_match is None:
            st.info("끝부분에 prefix가 완전 일치하는 규칙이 없습니다. (Grid는 최소 2글자, 끝 1글자는 실제값으로 사용)")
        else:
            col_m1, col_m2, col_m3 = st.columns([2, 1, 2])
            with col_m1:
                st.markdown(f"- 일치 prefix: `{current_match['prefix']}`")
            with col_m2:
                st.markdown(f"- 예측값: **{current_match['prediction']}**")
            with col_m3:
                st.markdown(f"- 실제값(Grid 끝 1글자): **{current_match['actual']}** → 결과: **{current_match['result']}**")

            st.markdown("### 이 Grid를 라운드로 기록")
            default_result = current_match["result"]  # "승" or "패" (예측==실제 → 승, 아니면 패)
            result_idx = {"승": 0, "무": 1, "패": 2}.get(default_result, 0)
            result = st.selectbox(
                "결과 (예측 vs 실제로 자동 판정, 필요 시 무로 변경)",
                options=["승", "무", "패"],
                key="v2_result_select",
                index=result_idx,
            )

            if st.button("이 Grid를 라운드로 추가", key="v2_add_round", use_container_width=True):
                if len(rounds) >= 8:
                    st.warning("라운드는 최대 8개까지 기록할 수 있습니다.")
                else:
                    round_index = len(rounds) + 1
                    rounds.append(
                        {
                            "round_index": round_index,
                            "prefix": current_match["prefix"],
                            "prediction": current_match["prediction"],
                            "actual": current_match["actual"],
                            "result": result,
                        }
                    )
                    st.session_state.prefix_rules_rounds = rounds
                    st.success(f"{round_index}회차 라운드를 추가했습니다.")

    st.markdown("---")
    st.markdown("## 사이클 베팅 기록 (SQLite)")
    st.caption(
        f"DB 파일: `{_bet_db_path().name}` · 스테이지별로 **사이클에 추가**한 뒤, 한 번에 **사이클 기록 저장**합니다. "
        "B 승리: 주×{b:.2f} · P 승리: 주×{p:.2f} · 무승부: 주 Push + 무×{d:.2f} · 패배: 환수 0".format(
            b=ODDS_B, p=ODDS_P, d=ODDS_DRAW
        )
    )
    st.markdown("#### 전략 JSON (스테이지별 주·무 베팅)")
    strat_loaded = st.session_state.get("bet_strategy")
    if strat_loaded and isinstance(strat_loaded, dict):
        si = strat_loaded.get("strategy_info") or {}
        sn = si.get("name", "(이름 없음)")
        st.caption(f"적용 중 전략: **{sn}** — `stage`를 바꾸면 해당 단의 `main_bet` / `draw_bet`이 자동 입력됩니다.")
    else:
        st.caption(
            f"JSON을 불러오거나 **번들 전략**을 쓰면 스테이지와 연동됩니다. "
            f"샘플: `{BUNDLE_STRATEGY_JSON.relative_to(Path(__file__).resolve().parent)}`"
        )
    strat_file = st.file_uploader(
        "전략 JSON 파일",
        type=["json"],
        key="bet_strategy_upload",
        label_visibility="collapsed",
    )
    col_sa, col_sb, col_sc, col_sd = st.columns([1, 1, 1, 1])
    with col_sa:
        apply_strat = st.button("전략 적용", key="bet_strategy_apply_upload", help="위에서 선택한 파일을 적용합니다.")
    with col_sb:
        bundle_strat = st.button("번들 전략", key="bet_strategy_apply_bundle", help="Hybrid_Martin_D_Alembert v1 샘플 JSON")
    with col_sc:
        clear_strat = st.button("전략 해제", key="bet_strategy_clear")
    with col_sd:
        if strat_file is not None:
            st.caption("파일 선택됨 → **전략 적용**")
    if apply_strat and strat_file is not None:
        try:
            raw_s = strat_file.read().decode("utf-8")
        except Exception:
            raw_s = strat_file.getvalue().decode("utf-8", errors="replace")
        parsed, err = parse_bet_strategy_json(raw_s)
        if err:
            st.warning(f"전략 JSON 오류: {err}")
        else:
            st.session_state.bet_strategy = parsed
            st.session_state.bet_strategy_version = int(st.session_state.bet_strategy_version) + 1
            if "bet_strategy_upload" in st.session_state:
                del st.session_state.bet_strategy_upload
            st.success("전략을 적용했습니다.")
            st.rerun()
    elif apply_strat and strat_file is None:
        st.warning("JSON 파일을 먼저 선택하세요.")
    if bundle_strat:
        if not BUNDLE_STRATEGY_JSON.is_file():
            st.error(f"번들 파일이 없습니다: {BUNDLE_STRATEGY_JSON}")
        else:
            parsed, err = parse_bet_strategy_json(BUNDLE_STRATEGY_JSON.read_text(encoding="utf-8"))
            if err:
                st.warning(f"번들 JSON 오류: {err}")
            else:
                st.session_state.bet_strategy = parsed
                st.session_state.bet_strategy_version = int(st.session_state.bet_strategy_version) + 1
                st.success("번들 전략을 적용했습니다.")
                st.rerun()
    if clear_strat:
        st.session_state.bet_strategy = None
        st.session_state.bet_strategy_version = int(st.session_state.bet_strategy_version) + 1
        st.info("전략을 해제했습니다. 금액은 그대로 두었습니다.")
        st.rerun()

    cyc = st.session_state.prefix_rules_bet_cycle_id
    col_c1, col_c2, col_c3 = st.columns([1, 1, 3])
    with col_c1:
        if st.button("다음 사이클 시작", key="bet_next_cycle", use_container_width=True, help="cycle_id를 1 올리고 stage·담긴 스테이지 목록을 초기화합니다."):
            st.session_state.prefix_rules_bet_cycle_id = int(cyc) + 1
            st.session_state.bet_save_stage = 1
            st.session_state.bet_cycle_draft_stages = []
            st.rerun()
    with col_c2:
        if st.button("1단 회귀", key="bet_stage_reset", use_container_width=True, help="cycle_id는 유지하고 stage만 1로 둡니다."):
            st.session_state.bet_save_stage = 1
            st.rerun()
    with col_c3:
        st.markdown(f"현재 **cycle_id** = `{cyc}` (리셋 전까지 동일 번호 유지)")

    col_b1, col_b2 = st.columns([1, 2])
    with col_b1:
        st.number_input("stage (1~6)", min_value=1, max_value=6, step=1, key="bet_save_stage")
    sync_session_bet_amounts_from_strategy(st.session_state)
    with col_b2:
        st.text_input("pattern (선택한 규칙 prefix)", key="bet_save_pattern", placeholder="예: bbpbbp…")
    col_b3, col_b4, col_b5 = st.columns([1, 1, 2])
    with col_b3:
        st.number_input("main_bet (주 베팅 유닛)", min_value=0.0, step=1.0, format="%.4f", key="bet_save_main")
    with col_b4:
        st.number_input("draw_bet (무 베팅 유닛)", min_value=0.0, step=1.0, format="%.4f", key="bet_save_draw")
    with col_b5:
        st.selectbox("outcome (최종 결과)", options=list(OUTCOMES), key="bet_save_outcome")

    sync_match = current_match is not None
    if sync_match:
        if st.button("현재 Grid 매칭으로 pattern·outcome 채우기", key="bet_fill_from_match"):
            st.session_state.bet_save_pattern = current_match["prefix"]
            st.session_state.bet_save_outcome = outcome_from_grid_match(
                current_match["result"], current_match["prediction"]
            )
            st.rerun()

    col_add, col_pop, col_clr = st.columns([2, 1, 1])
    with col_add:
        add_stage = st.button("이 스테이지를 사이클에 추가", key="bet_draft_add_stage", use_container_width=True)
    with col_pop:
        pop_stage = st.button("마지막 스테이지 제거", key="bet_draft_pop_stage", use_container_width=True)
    with col_clr:
        clear_draft = st.button("사이클 초기화", key="bet_draft_clear", use_container_width=True, help="DB에 안 쓰고, 담아 둔 스테이지만 비웁니다.")

    draft = st.session_state.bet_cycle_draft_stages
    if add_stage:
        pat = (st.session_state.get("bet_save_pattern") or "").strip()
        stage = int(st.session_state.get("bet_save_stage") or 1)
        main_b = float(st.session_state.get("bet_save_main") or 0.0)
        draw_b = float(st.session_state.get("bet_save_draw") or 0.0)
        outcome = st.session_state.get("bet_save_outcome") or "B 승리"
        entry, err = build_stage_entry(stage, pat, main_b, draw_b, outcome)
        if err:
            st.warning(err)
        else:
            st.session_state.bet_cycle_draft_stages = upsert_stage_in_draft(draft, entry)
            st.success(f"스테이지 {stage}를 사이클에 반영했습니다 (같은 단은 덮어씁니다).")
            st.rerun()
    if pop_stage:
        if draft:
            st.session_state.bet_cycle_draft_stages = draft[:-1]
            st.rerun()
        else:
            st.warning("제거할 스테이지가 없습니다.")
    if clear_draft:
        st.session_state.bet_cycle_draft_stages = []
        st.rerun()

    st.markdown("### 현재 사이클에 담긴 스테이지 (저장 전)")
    if draft:
        st.dataframe(pd.DataFrame(draft), use_container_width=True, hide_index=True)
        total_stake_draft = sum(float(s["main_bet"]) + float(s["draw_bet"]) for s in draft)
        total_payout_draft = sum(float(s["payout"]) for s in draft)
        cycle_total_net = sum(float(s["net_profit"]) for s in draft)
        m1, m2, m3 = st.columns(3)
        with m1:
            st.metric("사이클 토탈 stake (주+무)", f"{total_stake_draft:.4f}")
        with m2:
            st.metric("사이클 토탈 payout", f"{total_payout_draft:.4f}")
        with m3:
            st.metric(
                "사이클 토탈 net profit",
                f"{cycle_total_net:.4f}",
                help="담긴 모든 스테이지의 net_profit 합계 (= Σ payout − Σ stake)",
            )
        st.caption(
            "스테이지별 `net_profit`을 모두 더한 값이 위 **사이클 토탈 net profit**과 동일합니다."
        )
    else:
        st.caption("아직 추가된 스테이지가 없습니다. 위에서 입력 후 **이 스테이지를 사이클에 추가**를 누르세요.")

    if st.button("사이클 기록 저장", type="primary", key="bet_cycle_save_submit", use_container_width=True):
        cid = int(st.session_state.prefix_rules_bet_cycle_id)
        if not draft:
            st.error("저장할 스테이지를 먼저 추가하세요.")
        else:
            try:
                conn = sqlite3.connect(_bet_db_path())
                try:
                    _init_bet_db(conn)
                    rid, total_stake, cycle_net, cum_p = insert_cycle_record(conn, cid, list(draft))
                    st.session_state.bet_cycle_draft_stages = []
                    st.success(
                        f"사이클 저장됨 · record_id={rid} · cycle_id={cid} · "
                        f"스테이지 {len(draft)}개 · total_stake={total_stake:.4f} · "
                        f"cycle_net_profit={cycle_net:.4f} · 누적 cum_profit={cum_p:.4f}"
                    )
                    st.rerun()
                finally:
                    conn.close()
            except Exception as e:
                st.error(f"저장 실패: {e}")

    try:
        conn = sqlite3.connect(_bet_db_path())
        try:
            _init_bet_db(conn)
            df_cycles = load_cycle_records_dataframe(conn, limit=200)
            if not df_cycles.empty:
                st.markdown("### 저장된 사이클 기록 (record_id 내림차순)")
                st.dataframe(df_cycles, use_container_width=True, hide_index=True)
                df_csv = pd.read_sql_query(
                    "SELECT record_id, cycle_id, stage_count, total_stake, total_payout, cycle_net_profit, cum_profit, saved_at, stages_json "
                    "FROM cycle_records ORDER BY record_id",
                    conn,
                )
                st.download_button(
                    "사이클 기록 전체 CSV",
                    data=df_csv.to_csv(index=False).encode("utf-8-sig"),
                    file_name=f"cycle_records_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.csv",
                    mime="text/csv",
                    key="cycle_records_csv_dl",
                    use_container_width=True,
                )
            else:
                st.caption("아직 저장된 사이클 기록이 없습니다.")
        finally:
            conn.close()
    except Exception:
        st.caption("베팅 DB를 불러올 수 없습니다.")

    st.markdown("---")
    st.markdown("## 라운드 요약 (최대 8회차)")
    col_r1, col_r2 = st.columns([1, 1])
    with col_r1:
        if st.button("전체 라운드 초기화", key="v2_reset_rounds", use_container_width=True):
            st.session_state.prefix_rules_rounds = []
            st.success("라운드 기록을 모두 초기화했습니다.")
    with col_r2:
        st.caption("Grid만 초기화 버튼은 위 Grid 입력 섹션에 있습니다.")

    if rounds:
        df_rounds = pd.DataFrame(rounds)
        st.dataframe(
            df_rounds[["round_index", "prefix", "prediction", "actual", "result"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("저장된 라운드가 없습니다.")


if __name__ == "__main__":
    main()

