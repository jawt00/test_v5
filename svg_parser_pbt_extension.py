"""
[OPTIONAL] P/B/T 영문 Bead Road 파서 확장.

제거 방법:
  1. 이 파일 삭제
  2. 앱의 [OPTIONAL P/B/T] import 및 호출 블록 삭제
"""

import html as html_module
from bs4 import BeautifulSoup

from svg_parser_module import (
    TABLE_WIDTH,
    TABLE_HEIGHT,
    BEAD_ROAD_MAIN_CONTAINER_CLASS,
    BEAD_ROAD_ROW_CLASS,
    BEAD_ROAD_CELL_CLASS,
    parse_bead_road_svg,
    grid_to_string_column_wise,
)

_IFRAME_SRC_ATTRS = ("srcdoc", "data-savepage-srcdoc", "src")


def _iter_html_sources(html):
    """최상위 HTML과 iframe srcdoc 등 파싱 후보 HTML 목록."""
    sources = [html]
    soup = BeautifulSoup(html, "html.parser")
    for iframe in soup.find_all("iframe"):
        for attr in _IFRAME_SRC_ATTRS:
            raw = iframe.get(attr)
            if raw and str(raw).strip():
                sources.append(html_module.unescape(str(raw)))
    return sources


def _has_pbt_cells_in_html(html):
    soup = BeautifulSoup(html, "html.parser")
    main = soup.find("div", class_=BEAD_ROAD_MAIN_CONTAINER_CLASS)
    if not main:
        return False

    pbt_count = 0
    for row in main.find_all("div", class_=BEAD_ROAD_ROW_CLASS):
        for cell in row.find_all("div", class_=BEAD_ROAD_CELL_CLASS):
            if cell.get_text(strip=True).upper() in ("P", "B", "T"):
                pbt_count += 1
                if pbt_count >= 3:
                    return True
    return False


def _collect_svg_colors(cell):
    colors = []
    for svg in cell.find_all("svg"):
        for path in svg.find_all("path"):
            fill = path.get("fill", "")
            if fill:
                colors.append(fill)
    return colors


def resolve_bead_cell_value_pbt(text_content, svg_colors=None):
    """
    영문 P/B/T 셀 값 해석.
    텍스트가 P/B/T이면 SVG 색상보다 우선한다.
    """
    text = (text_content or "").strip().upper()

    if text == "P":
        return "p"
    if text == "B":
        return "b"
    if text == "T":
        return "t"

    for color in svg_colors or []:
        if "234, 66, 66" in color or "rgba(234, 66, 66" in color:
            return "b"
        if "45, 139, 232" in color or "rgb(45, 139, 232)" in color:
            return "p"

    return ""


def _walk_bead_road_grid(soup, resolve_fn):
    grid = [["" for _ in range(TABLE_HEIGHT)] for _ in range(TABLE_WIDTH)]
    main = soup.find("div", class_=BEAD_ROAD_MAIN_CONTAINER_CLASS)
    if not main:
        return grid

    for row_idx, row in enumerate(main.find_all("div", class_=BEAD_ROAD_ROW_CLASS)):
        if row_idx >= TABLE_HEIGHT:
            break
        for col_idx, cell in enumerate(row.find_all("div", class_=BEAD_ROAD_CELL_CLASS)):
            if col_idx >= TABLE_WIDTH:
                break
            text = cell.get_text(strip=True)
            colors = _collect_svg_colors(cell)
            result = resolve_fn(text, colors)
            if result:
                grid[col_idx][row_idx] = result
    return grid


def parse_bead_road_svg_pbt(svg_code):
    """P/B/T 전용 파서. 기존 parse_bead_road_svg와 동일한 grid 형식을 반환한다."""
    soup = BeautifulSoup(svg_code, "html.parser")
    return _walk_bead_road_grid(soup, resolve_bead_cell_value_pbt)


def is_pbt_bead_road_markup(svg_code):
    """셀에 P/B/T 텍스트가 있는지 감지한다 (iframe srcdoc 포함)."""
    for html in _iter_html_sources(svg_code):
        if _has_pbt_cells_in_html(html):
            return True
    return False


def _parse_with_iframe_fallback(parse_fn, html):
    """savepage/iframe srcdoc 내부 Bead Road 재시도."""
    best_grid = parse_fn(html)
    best_len = len(grid_to_string_column_wise(best_grid))

    for source in _iter_html_sources(html)[1:]:
        candidate = parse_fn(source)
        candidate_len = len(grid_to_string_column_wise(candidate))
        if candidate_len > best_len:
            best_grid = candidate
            best_len = candidate_len

    return best_grid


def parse_bead_road_svg_with_pbt_fallback(svg_code):
    """
    한글 파서 우선, 필요 시 P/B/T 파서로 fallback.

    Returns:
        tuple: (grid, parser_used)  # parser_used: 'korean' | 'pbt'
    """
    grid_ko = _parse_with_iframe_fallback(parse_bead_road_svg, svg_code)
    gs_ko = grid_to_string_column_wise(grid_ko)

    need_pbt = (not gs_ko) or is_pbt_bead_road_markup(svg_code)
    if not need_pbt:
        return grid_ko, "korean"

    grid_pbt = _parse_with_iframe_fallback(parse_bead_road_svg_pbt, svg_code)
    gs_pbt = grid_to_string_column_wise(grid_pbt)

    if gs_pbt and (not gs_ko or len(gs_pbt) >= len(gs_ko)):
        return grid_pbt, "pbt"
    return grid_ko, "korean"
