"""
Bead Road SVG/HTML 파서 확장 (P/B/T fallback).

원본이 git에 누락되어 샘플 HTML 기준으로 재구현한 stub.
Mac 원본이 있으면 이 파일을 교체하면 된다.

동작:
1) 기존 한글(플/뱅/무) 파서 시도 — 최상위 DOM → iframe srcdoc
2) 실패 시 P/B/T(영문) 파서 시도 — 동일 탐색 순서
"""

from __future__ import annotations

import html as html_module
from typing import Any, Optional

from bs4 import BeautifulSoup

from svg_parser_module import (
    TABLE_HEIGHT,
    TABLE_WIDTH,
    grid_to_string_column_wise,
    parse_bead_road_svg,
)

# Save Page / SingleFile 저장 시 Bead Road가 iframe srcdoc 안에 있는 경우
_IFRAME_SRC_ATTRS = ("srcdoc", "data-savepage-srcdoc")

# [유지보수] P/B/T Bead Road 그리드 클래스명 (샘플 HTML 2026-07-22 기준)
PBT_BEAD_ROAD_MAIN_CONTAINER_CLASS = "wn_ws"
PBT_BEAD_ROAD_ROW_CLASS = "wn_vL"
PBT_BEAD_ROAD_CELL_CLASS = "wn_wu"


def _empty_grid():
    return [["" for _ in range(TABLE_HEIGHT)] for _ in range(TABLE_WIDTH)]


def _cell_value_pbt(cell) -> str:
    """셀에서 P/B/T(또는 한글 플/뱅/무) 결과를 p/b/t로 반환."""
    # 1) span/p/text 등 직접 텍스트
    for tag in cell.find_all(["span", "p", "text"]):
        t = tag.get_text(strip=True)
        if not t:
            continue
        upper = t.upper()
        if upper == "P" or "플" in t:
            return "p"
        if upper == "B" or "뱅" in t:
            return "b"
        if upper == "T" or "무" in t:
            return "t"

    text_content = cell.get_text(strip=True)
    if text_content:
        upper = text_content.upper()
        if upper == "P" or "플" in text_content:
            return "p"
        if upper == "B" or "뱅" in text_content:
            return "b"
        if upper == "T" or "무" in text_content:
            return "t"

    # 2) 클래스 변형 (nM_nV=P, nM_nU=B, nM_nT=T / oP_oY=P, oP_oX=B, oP_oW=T)
    for div in cell.find_all("div", class_=True):
        classes = div.get("class") or []
        if "nM_nV" in classes or "oP_oY" in classes:
            return "p"
        if "nM_nU" in classes or "oP_oX" in classes:
            return "b"
        if "nM_nT" in classes or "oP_oW" in classes:
            return "t"

    # 3) SVG 색상 fallback (기존 한글 파서와 동일 계열)
    for svg in cell.find_all("svg"):
        for path in svg.find_all(["path", "circle"]):
            fill_color = path.get("fill", "") or ""
            if not fill_color:
                continue
            fl = fill_color.lower()
            if (
                "234, 66, 66" in fill_color
                or "rgba(234, 66, 66" in fill_color
                or "#e52638" in fl
                or "#de4e4e" in fl
            ):
                return "b"
            if (
                "45, 139, 232" in fill_color
                or "rgb(45, 139, 232)" in fill_color
                or "#3b79eb" in fl
            ):
                return "p"
            if "#2ba04d" in fl:
                return "t"

    return ""


def parse_bead_road_svg_pbt(svg_code: str):
    """
    P/B/T(영문) 라벨 기반 Bead Road 파서.
    prediction_test 호환 grid[x][y] (TABLE_WIDTH x TABLE_HEIGHT) 반환.
    """
    soup = BeautifulSoup(svg_code, "html.parser")
    grid = _empty_grid()

    main_container = soup.find("div", class_=PBT_BEAD_ROAD_MAIN_CONTAINER_CLASS)
    if not main_container:
        return grid

    rows = main_container.find_all("div", class_=PBT_BEAD_ROAD_ROW_CLASS)
    for row_idx, row in enumerate(rows):
        if row_idx >= TABLE_HEIGHT:
            break
        cells = row.find_all("div", class_=PBT_BEAD_ROAD_CELL_CLASS)
        for col_idx, cell in enumerate(cells):
            if col_idx >= TABLE_WIDTH:
                break
            result = _cell_value_pbt(cell)
            if result:
                grid[col_idx][row_idx] = result

    return grid


def _iter_html_fragments(html: str):
    """최상위 HTML 다음에 iframe srcdoc 조각들을 yield."""
    yield "top", html

    soup = BeautifulSoup(html, "html.parser")
    for iframe in soup.find_all("iframe"):
        for attr in _IFRAME_SRC_ATTRS:
            raw = iframe.get(attr)
            if not raw or not str(raw).strip():
                continue
            yield "iframe", html_module.unescape(raw)


def parse_bead_road_svg_with_pbt_fallback(
    html: str,
) -> tuple[list[list[str]], Optional[dict[str, Any]]]:
    """
    한글 파서 실패 시 P/B/T 파서로 fallback (iframe srcdoc 포함).

    Returns:
        (grid, meta)
        meta 예: {"mode": "korean"|"pbt", "source": "top"|"iframe"} 또는 실패 시 None
    """
    # 1) 한글(플/뱅/무) 파서
    for source, fragment in _iter_html_fragments(html):
        grid = parse_bead_road_svg(fragment)
        if grid_to_string_column_wise(grid):
            return grid, {"mode": "korean", "source": source}

    # 2) P/B/T 파서
    last_grid = _empty_grid()
    for source, fragment in _iter_html_fragments(html):
        grid = parse_bead_road_svg_pbt(fragment)
        last_grid = grid
        if grid_to_string_column_wise(grid):
            return grid, {"mode": "pbt", "source": source}

    return last_grid, None
