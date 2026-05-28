"""
Prefix 규칙 기반 라이브 게임 앱.

- 시작 시 Grid String을 첫 포지션부터 순차 검증(윈도우 9·10, (pos, ws) 순서), 히스토리 테이블 표시.
- 스트링 끝 9자(테일)에서만 라이브 일치/예측: 5자 이상 일치 후보만, 윈도우 9/10 구분 표시.
- B/P 버튼으로 한 글자 추가 시 테일만 갱신.
"""

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import streamlit as st

st.set_page_config(
    page_title="라이브 게임 (Prefix 규칙)",
    page_icon="🎯",
    layout="wide",
)

MIN_SEGMENT_LEN = 5
MAX_SEGMENT_LEN = 10
CELLS_PER_ROW = 35
WINDOW_SIZES = (9, 10)  # 지원 윈도우 (prefix 길이 8→ws9, 9→ws10)

# 기본 규칙 9개 (prefix → B/P)
DEFAULT_PREFIX_RULES = [
    ("bpbbbbpb", "B"),
    ("bpbbbpbp", "B"),
    ("pbpppbbb", "P"),
    ("pbpppbpp", "B"),
    ("bpbbbbbbpb", "B"),
    ("bpbbbbppbb", "B"),
    ("bpbpbbpbbb", "B"),
    ("pbbbbbbpbb", "B"),
    ("pbpppbbbpp", "P"),
]


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


def matching_rules(segment: str, rules: list) -> list:
    """리스트 아이템은 왼쪽부터 완성. segment로 시작하는 규칙들 (prefix 사전순). rule_prefix.startswith(segment)."""
    if len(segment) < MIN_SEGMENT_LEN:
        return []
    out = []
    for r in rules:
        prefix = (r.get("prefix") or "").strip().lower()
        if not prefix:
            continue
        if prefix.startswith(segment):
            out.append(r)
    return sorted(out, key=lambda x: (x.get("prefix") or ""))


def exact_match_prediction(segment: str, rules: list) -> Optional[str]:
    """segment == prefix인 규칙이 있으면 해당 예측값(B/P), 없으면 None."""
    for r in rules:
        prefix = (r.get("prefix") or "").strip().lower()
        if prefix and segment == prefix:
            return (r.get("prediction") or "B").upper()
    return None


def get_longest_matching_segment(grid_string: str, rules: list):
    """앵커 무시. 문자열 전체에서 길이 10→5인 부분 문자열을 검사해, rule prefix와 일치하는 가장 긴 segment 반환.
    segment = grid_string[i:i+L]. rule_prefix.startswith(segment)인 규칙만 일치.
    반환: (segment, matching_rules, start_index). 일치 없으면 ("", [], -1)."""
    if not grid_string or not rules:
        return "", [], -1
    n = len(grid_string)
    if n < MIN_SEGMENT_LEN:
        return "", [], -1
    max_l = min(MAX_SEGMENT_LEN, n)
    for L in range(max_l, MIN_SEGMENT_LEN - 1, -1):
        for i in range(0, n - L + 1):
            segment = grid_string[i : i + L]
            matching = matching_rules(segment, rules)
            if matching:
                return segment, matching, i
    return "", [], -1


def _rule_window_size(r: dict) -> Optional[int]:
    """규칙 prefix 길이로 윈도우 추론: 8→9, 9→10. 그 외 None."""
    p = (r.get("prefix") or "").strip()
    if len(p) == 8:
        return 9
    if len(p) == 9:
        return 10
    return None


def _tail_start(gs: str) -> int:
    """tail = gs[tail_start:] 가 끝 9자 되도록. 포지션 26부터(길이 35 기준)."""
    return max(0, len(gs) - 9)


def _run_sequential_validation(gs: str, rules: list) -> list:
    """extractor와 동일 (pos, ws) 순서로 순차 검증. 반환: history (step, position, window_size, segment, predicted, actual, is_correct)."""
    if not gs or not rules:
        return []
    n = len(gs)
    if n < max(WINDOW_SIZES):
        return []
    # (pos, ws) 오름차순. 윈도우9 첫 검증은 pos=8(시작 0), 윈도우10은 pos=9부터. pos는 min(ws)-1=8부터.
    candidates = []
    for pos in range(min(WINDOW_SIZES) - 1, n):
        for ws in sorted(WINDOW_SIZES):
            if pos >= ws - 1:
                candidates.append((pos, ws))
    candidates.sort(key=lambda x: (x[0], x[1]))
    history = []
    step = 0
    for pos, ws in candidates:
        prefix_len = ws - 1
        segment = gs[pos - prefix_len : pos]
        actual_char = gs[pos]
        actual_norm = (actual_char or "b").strip().upper()
        if actual_norm not in ("B", "P"):
            actual_norm = "B"
        step += 1
        # 규칙 중 len(prefix)==prefix_len 이고 prefix==segment 인 것
        predicted = None
        for r in rules:
            p = (r.get("prefix") or "").strip().lower()
            if len(p) == prefix_len and p == segment:
                predicted = (r.get("prediction") or "B").upper()
                break
        is_correct = (predicted == actual_norm) if predicted is not None else None
        history.append({
            "step": step,
            "position": pos,
            "window_size": ws,
            "segment": segment,
            "predicted": predicted,
            "actual": actual_char,
            "is_correct": is_correct,
        })
    return history


def matching_tail_rules(tail: str, rules: list) -> list:
    """tail에 대해 5자리 이상 일치하는 규칙만. prefix.startswith(tail) 또는 tail.startswith(prefix), min(len(tail),len(prefix))>=5."""
    if len(tail) < MIN_SEGMENT_LEN:
        return []
    out = []
    for r in rules:
        prefix = (r.get("prefix") or "").strip().lower()
        if not prefix:
            continue
        match_len = 0
        if prefix.startswith(tail):
            match_len = len(tail)
        elif tail.startswith(prefix):
            match_len = len(prefix)
        if match_len >= MIN_SEGMENT_LEN:
            out.append(r)
    return sorted(out, key=lambda x: (x.get("prefix") or ""))


def _apply_live_update(grid_string: str, session_state: dict) -> None:
    """B/P 추가 후 grid_string 기준으로 tail·tail_rules만 갱신. 히스토리는 유지."""
    rules_list = [r for r in session_state.get("prefix_rules", []) if (r.get("prefix") or "").strip()]
    tail_start = _tail_start(grid_string)
    tail = grid_string[tail_start:]
    session_state["prefix_rules_tail_start"] = tail_start
    session_state["prefix_rules_tail_segment"] = tail
    session_state["prefix_rules_tail_rules"] = matching_tail_rules(tail, rules_list)


def main():
    st.title("🎯 라이브 게임 (Prefix 규칙)")
    st.markdown("규칙 추가/제거 · Grid 입력 후 시작 → 순차 검증 히스토리 + 끝 9자(테일) 기준 일치/예측(윈도우 9·10 구분)")

    # 규칙용 id 카운터 (초기값: 빈 리스트, 불러오기/수동 추가로만 채움)
    if "prefix_rules_next_id" not in st.session_state:
        st.session_state.prefix_rules_next_id = 1
    if "prefix_rules" not in st.session_state:
        st.session_state.prefix_rules = []
    if "prefix_rules_grid_string" not in st.session_state:
        st.session_state.prefix_rules_grid_string = None
    # 순차 검증 히스토리 (시작 시 한 번 채움)
    if "prefix_rules_history" not in st.session_state:
        st.session_state.prefix_rules_history = []
    # 테일(끝 9자) 기준 라이브 일치/예측
    if "prefix_rules_tail_start" not in st.session_state:
        st.session_state.prefix_rules_tail_start = None
    if "prefix_rules_tail_segment" not in st.session_state:
        st.session_state.prefix_rules_tail_segment = None
    if "prefix_rules_tail_rules" not in st.session_state:
        st.session_state.prefix_rules_tail_rules = None
    if "prefix_rules_filter_conditions" not in st.session_state:
        st.session_state.prefix_rules_filter_conditions = None

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
                # 순차 검증 히스토리 (첫 포지션부터 (pos, ws) 순서)
                st.session_state.prefix_rules_history = _run_sequential_validation(gs, rules_list)
                # 테일(끝 9자) 기준 라이브 후보
                tail_start = _tail_start(gs)
                tail = gs[tail_start:] if tail_start < len(gs) else ""
                st.session_state.prefix_rules_tail_start = tail_start
                st.session_state.prefix_rules_tail_segment = tail
                st.session_state.prefix_rules_tail_rules = matching_tail_rules(tail, rules_list)
                st.rerun()
    with col_reset:
        if st.button("🔄 초기화", use_container_width=True, key="prefix_rules_btn_reset"):
            st.session_state.prefix_rules_grid_string = None
            st.session_state.prefix_rules_history = []
            st.session_state.prefix_rules_tail_start = None
            st.session_state.prefix_rules_tail_segment = None
            st.session_state.prefix_rules_tail_rules = None
            st.rerun()

    gs = st.session_state.prefix_rules_grid_string
    if gs is not None:
        st.markdown("---")
        st.markdown("## Grid String 및 앵커")
        anchors = _anchors_from_grid_string(gs)
        render_grid_string_and_anchors(gs, anchors)

        history = st.session_state.prefix_rules_history
        tail_start = st.session_state.prefix_rules_tail_start
        tail_segment = st.session_state.prefix_rules_tail_segment or ""
        tail_rules = st.session_state.prefix_rules_tail_rules or []

        st.markdown("---")
        st.markdown("## 히스토리 (순차 검증) — 윈도우 9 / 10 완전 분리")

        hist_ws9 = [h for h in history if h.get("window_size") == 9]
        hist_ws10 = [h for h in history if h.get("window_size") == 10]

        st.markdown("### 윈도우 9 히스토리")
        if hist_ws9:
            df9 = pd.DataFrame(hist_ws9)
            st.dataframe(
                df9[["step", "position", "window_size", "segment", "predicted", "actual", "is_correct"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("윈도우 9 검증 히스토리 없음.")

        st.markdown("### 윈도우 10 히스토리")
        if hist_ws10:
            df10 = pd.DataFrame(hist_ws10)
            st.dataframe(
                df10[["step", "position", "window_size", "segment", "predicted", "actual", "is_correct"]],
                use_container_width=True,
                hide_index=True,
            )
        else:
            st.caption("윈도우 10 검증 히스토리 없음.")

        st.markdown("---")
        st.markdown("## 일치 / 예측 (테일) — 윈도우 9 · 윈도우 10 각각")

        tail_ws9 = [r for r in tail_rules if _rule_window_size(r) == 9]
        tail_ws10 = [r for r in tail_rules if _rule_window_size(r) == 10]

        if len(gs) < 9:
            st.caption(f"Grid 길이 {len(gs)}자. 끝 9자(테일)가 있어야 라이브 일치/예측 표시.")
        elif len(tail_segment) < MIN_SEGMENT_LEN:
            st.caption(f"테일 `{tail_segment}` ({len(tail_segment)}자). {MIN_SEGMENT_LEN}자 이상부터 후보 표시.")
        else:
            st.caption(f"**테일** (위치 {tail_start}~): `{tail_segment}` ({len(tail_segment)}자) — 5자 이상 일치 후보만")

            st.markdown("### 윈도우 9 일치 / 예측")
            if tail_ws9:
                exact9 = exact_match_prediction(tail_segment, tail_ws9)
                if exact9 is not None:
                    st.success(f"예측값: **{exact9}** (prefix 완전 일치)")
                st.markdown("일치 중 (prefix 사전순):")
                for r in tail_ws9:
                    p = r.get("prefix", "")
                    pred = (r.get("prediction") or "B").upper()
                    st.markdown(f"- `{p}` → **{pred}**")
            else:
                st.info("일치하는 윈도우 9 규칙 없음.")

            st.markdown("### 윈도우 10 일치 / 예측")
            if tail_ws10:
                exact10 = exact_match_prediction(tail_segment, tail_ws10)
                if exact10 is not None:
                    st.success(f"예측값: **{exact10}** (prefix 완전 일치)")
                st.markdown("일치 중 (prefix 사전순):")
                for r in tail_ws10:
                    p = r.get("prefix", "")
                    pred = (r.get("prediction") or "B").upper()
                    st.markdown(f"- `{p}` → **{pred}**")
            else:
                st.info("일치하는 윈도우 10 규칙 없음.")

        st.markdown("---")
        st.caption("B / P 입력 (한 글자씩 추가, 테일만 갱신)")
        col_b, col_p, _ = st.columns([1, 1, 4])
        with col_b:
            if st.button("🔴 B", key="prefix_rules_append_b", use_container_width=True):
                new_gs = gs + "b"
                st.session_state.prefix_rules_grid_string = new_gs
                _apply_live_update(new_gs, st.session_state)
                st.rerun()
        with col_p:
            if st.button("🔵 P", key="prefix_rules_append_p", use_container_width=True):
                new_gs = gs + "p"
                st.session_state.prefix_rules_grid_string = new_gs
                _apply_live_update(new_gs, st.session_state)
                st.rerun()


if __name__ == "__main__":
    main()
