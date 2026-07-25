"""
ws9 (8자 b/p) 전체 조합 브라우저 · 선택 · 리스트 export.

실행:
  streamlit run change_point/ws9_pattern_picker_app.py
"""

from __future__ import annotations

import csv
import io
import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from pattern_list_profiles import PROJECT_ROOT
from prefix_pattern_svg import prefix_pattern_svg

WS9_LEN = 8

ALL_WS9: tuple[str, ...] = tuple(
    first + second + "".join(rest)
    for first in "bp"
    for second in "bp"
    if first != second
    for rest in itertools.product("bp", repeat=WS9_LEN - 2)
)

PATTERN_LIST2_CSV = PROJECT_ROOT / "pattern_list2.csv"
SESSION_SELECTED = "ws9_selected"


def load_list2_ws9() -> set[str]:
    if not PATTERN_LIST2_CSV.is_file():
        return set()
    df = pd.read_csv(PATTERN_LIST2_CSV)
    df["prefix"] = df["prefix"].astype(str).str.strip().str.lower()
    ws10 = df.loc[df["window_size"] == 10, "prefix"]
    return {p[1:] for p in ws10 if len(p) == 9}


def expand_pattern_list_rows(
    ws9: str,
    *,
    include_b: bool,
    include_p: bool,
) -> list[tuple[int, str]]:
    """ws9 → pattern_list 형식 (ws10 / ws12) 행."""
    rows: list[tuple[int, str]] = []
    if include_b:
        ws10 = "b" + ws9
        ws12 = "bpb" + ws9
        rows.append((10, ws10))
        rows.append((12, ws12))
    if include_p:
        ws10 = "p" + ws9
        ws12 = "pbp" + ws9
        rows.append((10, ws10))
        rows.append((12, ws12))
    return rows


def _init_selection() -> None:
    valid = set(ALL_WS9)
    if SESSION_SELECTED not in st.session_state:
        st.session_state[SESSION_SELECTED] = set()
    else:
        st.session_state[SESSION_SELECTED] &= valid


def _sync_checkbox_to_selection(prefix: str) -> None:
    selected: set[str] = st.session_state[SESSION_SELECTED]
    if st.session_state.get(f"chk_{prefix}"):
        selected.add(prefix)
    else:
        selected.discard(prefix)


def _sync_all_checkboxes(prefixes: tuple[str, ...] | list[str] | None = None) -> None:
    selected: set[str] = st.session_state[SESSION_SELECTED]
    targets = prefixes if prefixes is not None else ALL_WS9
    for p in targets:
        st.session_state[f"chk_{p}"] = p in selected


def _filter_prefixes(
    *,
    search: str,
    starts_with: str,
    only_selected: bool,
    only_list2: bool,
    list2_ws9: set[str],
) -> list[str]:
    selected: set[str] = st.session_state[SESSION_SELECTED]
    out: list[str] = []
    q = search.strip().lower()

    for p in ALL_WS9:
        if starts_with != "전체" and not p.startswith(starts_with.lower()):
            continue
        if q and q not in p:
            continue
        if only_selected and p not in selected:
            continue
        if only_list2 and p not in list2_ws9:
            continue
        out.append(p)
    return out


def _rows_to_csv(rows: list[tuple[int, str]]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["window_size", "prefix"])
    for ws, prefix in rows:
        writer.writerow([ws, prefix])
    return buf.getvalue()


def _ws9_rows_to_csv(prefixes: list[str]) -> str:
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["window_size", "prefix"])
    for p in sorted(prefixes):
        writer.writerow([9, p])
    return buf.getvalue()


def main() -> None:
    st.set_page_config(
        page_title="ws9 패턴 선택",
        page_icon="🧩",
        layout="wide",
    )
    _init_selection()
    list2_ws9 = load_list2_ws9()
    selected: set[str] = st.session_state[SESSION_SELECTED]

    st.title("ws9 패턴 선택")
    st.caption(
        f"`b`/`p` **{WS9_LEN}자** · 1·2번째 문자 상이 (`bp`/`pb` 시작) **{len(ALL_WS9)}**개 · "
        f"선택 **{len(selected)}**개 · "
        f"`pattern_list2.csv` ws9 **{len(list2_ws9)}**개"
    )

    with st.sidebar:
        st.subheader("필터")
        search = st.text_input("prefix 검색 (부분 일치)", "")
        starts_with = st.selectbox("시작 문자", ["전체", "bp", "pb"])
        only_selected = st.checkbox("선택된 것만", False)
        only_list2 = st.checkbox("list2에 있는 것만", False)

        st.divider()
        st.subheader("선택")
        if st.button("표시 중 전체 선택", use_container_width=True):
            visible = _filter_prefixes(
                search=search,
                starts_with=starts_with,
                only_selected=False,
                only_list2=only_list2,
                list2_ws9=list2_ws9,
            )
            selected.update(visible)
            _sync_all_checkboxes(visible)
            st.rerun()
        if st.button("표시 중 전체 해제", use_container_width=True):
            visible = _filter_prefixes(
                search=search,
                starts_with=starts_with,
                only_selected=False,
                only_list2=only_list2,
                list2_ws9=list2_ws9,
            )
            selected -= set(visible)
            _sync_all_checkboxes(visible)
            st.rerun()
        if st.button(f"전체 선택 ({len(ALL_WS9)})", use_container_width=True):
            selected.update(ALL_WS9)
            _sync_all_checkboxes()
            st.rerun()
        if st.button("전체 해제", use_container_width=True):
            selected.clear()
            _sync_all_checkboxes()
            st.rerun()
        if list2_ws9 and st.button("list2 불러오기", use_container_width=True):
            selected.clear()
            selected.update(list2_ws9)
            _sync_all_checkboxes()
            st.rerun()

        st.divider()
        st.subheader("Export")
        sorted_sel = sorted(selected & set(ALL_WS9))
        st.download_button(
            "ws9 CSV",
            data=_ws9_rows_to_csv(sorted_sel),
            file_name=f"ws9_list_{datetime.now():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=not sorted_sel,
        )
        st.download_button(
            "ws9 JSON",
            data=json.dumps(
                [{"window_size": 9, "prefix": p} for p in sorted_sel],
                ensure_ascii=False,
                indent=2,
            ),
            file_name=f"ws9_list_{datetime.now():%Y%m%d_%H%M%S}.json",
            mime="application/json",
            use_container_width=True,
            disabled=not sorted_sel,
        )

        expand_b = st.checkbox("pad10=b (ws10/ws12)", value=True)
        expand_p = st.checkbox("pad10=p (ws10/ws12)", value=True)
        pattern_rows: list[tuple[int, str]] = []
        for p in sorted_sel:
            pattern_rows.extend(
                expand_pattern_list_rows(p, include_b=expand_b, include_p=expand_p)
            )
        st.download_button(
            "pattern_list CSV (ws10+ws12)",
            data=_rows_to_csv(pattern_rows),
            file_name=f"pattern_list_ws9_expanded_{datetime.now():%Y%m%d_%H%M%S}.csv",
            mime="text/csv",
            use_container_width=True,
            disabled=not pattern_rows,
        )

    visible = _filter_prefixes(
        search=search,
        starts_with=starts_with,
        only_selected=only_selected,
        only_list2=only_list2,
        list2_ws9=list2_ws9,
    )

    st.markdown(f"**표시 {len(visible)}** / {len(ALL_WS9)}")
    if not visible:
        st.info("조건에 맞는 prefix가 없습니다.")
        return

    cols_per_row = st.slider("열 개수", min_value=2, max_value=8, value=4)
    cell_px = st.slider("도형 크기", min_value=8, max_value=16, value=10)

    for row_start in range(0, len(visible), cols_per_row):
        cols = st.columns(cols_per_row)
        for col, prefix in zip(cols, visible[row_start : row_start + cols_per_row]):
            with col:
                is_sel = prefix in selected
                in_list2 = prefix in list2_ws9
                label = f"{'✓ ' if is_sel else ''}{prefix}"
                if in_list2:
                    label += " · list2"
                st.checkbox(
                    label,
                    value=is_sel,
                    key=f"chk_{prefix}",
                    on_change=_sync_checkbox_to_selection,
                    args=(prefix,),
                )
                svg = prefix_pattern_svg(prefix, cell_px=cell_px)
                if svg:
                    st.markdown(svg, unsafe_allow_html=True)

    if selected:
        with st.expander(f"선택 목록 ({len(selected)})", expanded=False):
            st.code("\n".join(sorted(selected)), language=None)


if __name__ == "__main__":
    main()
