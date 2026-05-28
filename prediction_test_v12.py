import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import os
from bs4 import BeautifulSoup
import json
import sqlite3
import uuid
import copy  # Add copy module for deep copy

# 페이지 설정을 가장 먼저 실행
st.set_page_config(
    page_title="Pattern Analysis System V12",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS to minimize top margin
st.markdown("""
    <style>
        .block-container {
            padding-top: 1rem;
        }
    </style>
""", unsafe_allow_html=True)

# Table dimensions
TABLE_WIDTH = 15
TABLE_HEIGHT = 6

# Cell types
CELL_BANKER = 'b'
CELL_PLAYER = 'p'
CELL_TIE = 't'
CELL_EMPTY = ''

# Pattern definitions
PATTERN_WIDTH = 2
PATTERN_TOP_ROWS = [0,1,2]
PATTERN_BOTTOM_ROWS = [3,4,5]

def parse_bead_road_svg(svg_code):
    """
    HTML div 기반 테이블(qV_qY, qV_qp, qV_q0 구조)을
    prediction_test_v4.py와 호환되는 grid[x][y] (15x6) 형태로 파싱
    
    [유지보수 노트 - 2025-01-16]
    클래스명 변경 이력:
    - 2025-01-16: rg_rj → qz_qC, rg_qu → qz_pO, rg_rl → qz_qF
    - 2025-01-17: qz_qC → qV_qY, qz_pO → qV_qp, qz_qF → qV_q0 (새로운 구조)
    - 2025-01-XX: qV_qY → qF_qI, qV_qp → qF_p1, qV_q0 → qF_qK (최신 구조)
    - 2025-01-XX: qF_qI → rk_rn, qF_p1 → rk_ov, qF_qK → rk_rp (최신 구조)
    - 2025-01-XX: rk_rn → sb_sf, rk_ov → sb_ry, rk_rp → sb_sh (최신 구조)
    - 2025-01-XX: sb_sf → rI_rL, sb_ry → rI_pq, sb_sh → rI_rN (최신 구조)
    - 2025-01-XX: rI_rL → ti_tl, rI_pq → ti_rR, rI_rN → ti_tn (최신 구조)
    - 2025-01-XX: ti_tl → to_tr, ti_rR → to_st, ti_tn → to_tt (최신 구조)
    - 2025-01-XX: to_tr → sp_ss, to_st → sp_rM, to_tt → sp_su (최신 구조)
    - 2025-01-XX: sp_ss → sT_sW, sp_rM → sT_qG, sp_su → sT_sY (최신 구조)
    - 2025-01-XX: sT_sW → sH_sK, sT_qG → sH_so, sT_sY → sH_sM (최신 구조)
    - 2025-01-XX: sH_sK → tr_tu, sH_so → tr_rU, sH_sM → tr_tw (최신 구조)
    - 2025-01-XX: tr_tu → sx_sA, tr_rU → sx_rU, tr_tw → sx_sC (최신 구조)
    - 2025-01-XX: sx_sA → sB_sF, sx_rU → sB_rL, sx_sC → sB_sH (최신 구조)
    - 2025-01-XX: sB_sF → rs_rv, sB_rL → rs_ri, sB_sH → rs_rx (최신 구조)
    - 2025-01-XX: rs_rv → sf_si, rs_ri → sf_sm, rs_rx → sf_sn (최신 구조)
    - 2025-01-XX: sf_si → o0_pb, sf_sm → o0_pg, sf_sn → o0_ph (최신 구조)
    - 2025-01-XX: o0_pb → rK_rN, o0_pg → rK_qU, o0_ph → rK_rP (최신 구조)
    - 2025-01-XX: rK_rN → pO_pR, rK_qU → pO_pm, rK_rP → pO_pT (최신 구조)
    - 2025-01-XX: pO_pR → pl_po, pO_pm → pl_ps, pO_pT → pl_pt (최신 구조)
    - 2025-01-XX: pl_po → qR_qU, pl_ps → qR_qY, pl_pt → qR_qZ (최신 구조)
    - 이전: rg_rj → qz_qC (메인 컨테이너)
    - 이전: rg_qu → qz_pO (행)
    - 이전: rg_rl → qz_qF (셀)
    
    다음 변경 시 이 부분만 수정하면 됩니다:
    - main_container = soup.find('div', class_='qR_qU')
    - rows = main_container.find_all('div', class_='qR_qY')
    - cells = row.find_all('div', class_='qR_qZ')
    """
    soup = BeautifulSoup(svg_code, 'html.parser')
    grid = [['' for _ in range(TABLE_HEIGHT)] for _ in range(TABLE_WIDTH)]
    
    # [유지보수] 클래스명 변경 시 이 부분만 수정
    main_container = soup.find('div', class_='qR_qU')
    if not main_container:
        st.warning("qR_qU 클래스를 찾을 수 없습니다.")
        return grid
    
    # [유지보수] 클래스명 변경 시 이 부분만 수정
    rows = main_container.find_all('div', class_='qR_qY')
    for row_idx, row in enumerate(rows):
        if row_idx >= TABLE_HEIGHT:
            break
        
        # [유지보수] 클래스명 변경 시 이 부분만 수정
        cells = row.find_all('div', class_='qR_qZ')
        for col_idx, cell in enumerate(cells):
            if col_idx >= TABLE_WIDTH:
                break
            text_content = cell.get_text(strip=True)
            svg_elements = cell.find_all('svg')
            svg_colors = []
            for svg in svg_elements:
                paths = svg.find_all('path')
                for path in paths:
                    fill_color = path.get('fill', '')
                    if fill_color:
                        svg_colors.append(fill_color)
            result = ''
            if '플' in text_content:
                result = 'p'
            elif '뱅' in text_content:
                result = 'b'
            elif '무' in text_content:
                result = 't'
            elif svg_colors:
                for color in svg_colors:
                    if '234, 66, 66' in color or 'rgba(234, 66, 66' in color:
                        result = 'b'
                        break
                    elif '45, 139, 232' in color or 'rgb(45, 139, 232)' in color:
                        result = 'p'
                        break
            grid[col_idx][row_idx] = result
    return grid

def display_grid_with_title(grid, title):
    html = '''
    <style>
    .grid-container { display: table; border-collapse: collapse; margin: 0 auto 20px auto; width: 80%; margin-top: 0 !important; }
    .grid-row { display: table-row; }
    .bead-road-cell { width: 22px; height: 22px; border: 1px solid black; display: table-cell; 
                     text-align: center; vertical-align: middle; font-family: monospace; font-size: 0.95rem; padding: 0; }
    .banker { color: red; font-weight: bold; }
    .player { color: blue; font-weight: bold; }
    .tie { color: green; font-weight: bold; }
    .grid-title { font-size:1.05rem; font-weight:600; margin-bottom:0 !important; padding-bottom:0 !important; display:block; }
    </style>
    '''
    html += f'<span class="grid-title">{title}</span>'
    html += '<div class="grid-container">'
    for y in range(TABLE_HEIGHT):
        html += '<div class="grid-row">'
        for x in range(TABLE_WIDTH):
            cell = grid[x][y]
            css_class = 'banker' if cell == 'b' else 'player' if cell == 'p' else 'tie' if cell == 't' else ''
            html += f'<div class="bead-road-cell {css_class}">{cell.upper() if cell else "&nbsp;"}</div>'
        html += '</div>'
    html += '</div>'
    st.markdown(html, unsafe_allow_html=True)

def convert_tie_values(grid):
    """
    Convert T values according to rules.
    Updated on 2024-03-21 to match parser_v2.py logic:
    - First column: T is converted to the value from the second row for first row, or previous row for other rows
    - Other columns: T is converted based on conditional logic comparing left, left-up, and up values
    """
    converted_grid = [row[:] for row in grid]  # Copy grid
    
    # Apply 1st column rule
    for y in range(6):
        if converted_grid[0][y] == 't':
            if y == 0:  # 1st row 1st column
                converted_grid[0][y] = converted_grid[0][1]  # Convert to 2nd row 1st column value
            else:  # 1st column other rows
                converted_grid[0][y] = converted_grid[0][y-1]  # Convert to previous row value
    
    # Apply other columns rule
    for x in range(1, 15):
        for y in range(6):
            if converted_grid[x][y] == 't':
                if y == 0:  # 1st row of each column
                    converted_grid[x][y] = converted_grid[x-1][y]  # Convert to previous column 1st value
                else:  # Other rows
                    # Get values from left, left up, and up
                    left = converted_grid[x-1][y]
                    left_up = converted_grid[x-1][y-1]
                    up = converted_grid[x][y-1]
                    
                    # If left-up and up are the same, use that value
                    if left_up == up:
                        converted_grid[x][y] = up
                    # If left-up and left are the same, use that value
                    elif left_up == left:
                        converted_grid[x][y] = left
                    # Otherwise use the up value
                    else:
                        converted_grid[x][y] = up
    
    return converted_grid

def apply_column_range_to_grid(grid, start_col=0, end_col=None):
    """
    지정한 열 범위 밖의 데이터를 제거한 새로운 그리드를 반환합니다.
    grid의 전체 폭은 유지하고, 범위 밖의 셀은 빈 문자열로 채웁니다.
    """
    if not grid:
        return grid
    
    grid_width = len(grid)
    grid_height = len(grid[0]) if grid[0] else 0
    
    if end_col is None:
        end_col = grid_width - 1
    
    start_col = max(0, int(start_col))
    end_col = min(int(end_col), grid_width - 1)
    
    if start_col > end_col:
        return [['' for _ in range(grid_height)] for _ in range(grid_width)]
    
    new_grid = [['' for _ in range(grid_height)] for _ in range(grid_width)]
    
    for x in range(start_col, end_col + 1):
        for y in range(grid_height):
            new_grid[x][y] = grid[x][y]
    
    return new_grid

def realign_grid_by_columns(grid, start_col=0, end_col=None):
    """
    선택한 열 범위를 0번 열부터 차례대로 '왼쪽 정렬'하고,
    나머지 열은 빈 값으로 남기는 그리드를 반환합니다.
    이렇게 하면 선택한 시작 열이 새로운 1열(0번 인덱스)처럼 동작합니다.
    """
    if not grid:
        return grid
    
    grid_width = len(grid)
    grid_height = len(grid[0]) if grid[0] else 0
    
    if end_col is None:
        end_col = grid_width - 1
    
    start_col = max(0, int(start_col))
    end_col = min(int(end_col), grid_width - 1)
    
    if start_col > end_col:
        return [['' for _ in range(grid_height)] for _ in range(grid_width)]
    
    new_grid = [['' for _ in range(grid_height)] for _ in range(grid_width)]
    dest_x = 0
    
    # 선택한 열 범위를 0번 열부터 차례대로 배치
    for x in range(start_col, end_col + 1):
        for y in range(grid_height):
            new_grid[dest_x][y] = grid[x][y]
        dest_x += 1
    
    return new_grid

# ============================================================================
# 독립적인 인덱스 계산 함수들 (기존 코드에 영향 없음)
# ============================================================================

def get_cell_index(x, y):
    """
    그리드 셀의 인덱스를 계산 (열 우선 순서)
    
    Args:
        x: 열 인덱스 (0-based)
        y: 행 인덱스 (0-based)
    
    Returns:
        int: 셀 인덱스 (0-based, 열 우선 순서)
    """
    return y + (x * TABLE_HEIGHT)

def get_flag_triggered_cell_index(converted_group_range):
    """
    Flag 조건 충족 시점 (Converted Grid)의 셀 인덱스를 계산
    그룹의 시작 열의 첫 번째 셀 인덱스를 반환
    
    Args:
        converted_group_range: Converted Grid의 그룹 범위 문자열 (예: "4-6")
    
    Returns:
        int: 그룹 시작 열의 첫 번째 셀 인덱스 (0-based)
    """
    try:
        parts = converted_group_range.split('-')
        if len(parts) != 2:
            return None
        
        start_col = int(parts[0]) - 1  # 1-based to 0-based
        # 그룹 시작 열의 첫 번째 셀 인덱스 (행 0)
        return get_cell_index(start_col, 0)
    except Exception as e:
        return None

def count_t_before_cell_index(original_grid, cell_index):
    """
    원본 그리드에서 특정 셀 인덱스 이전의 T 개수를 계산 (열 우선 순서)
    해당 셀 인덱스 위치의 셀을 포함하지 않고, 그 이전까지의 T 개수를 계산
    
    Args:
        original_grid: 원본 그리드 (T가 변환되기 전)
        cell_index: 셀 인덱스 (0-based, 이 인덱스 위치의 셀은 포함하지 않음)
    
    Returns:
        int: 특정 셀 인덱스 이전까지의 T 개수
    """
    if not original_grid or cell_index < 0:
        return 0
    
    t_count = 0
    current_index = 0
    
    # 열 우선 순서로 그리드를 순회하며 T 개수 세기
    for x in range(TABLE_WIDTH):
        for y in range(TABLE_HEIGHT):
            # 현재 인덱스가 목표 셀 인덱스에 도달하면 중단 (해당 셀은 포함하지 않음)
            if current_index >= cell_index:
                return t_count
            if original_grid[x][y] == 't':
                t_count += 1
            current_index += 1
    
    return t_count

def adjust_index_by_t_count(cell_index, original_grid):
    """
    T 개수만큼 인덱스를 조정
    
    Args:
        cell_index: 원본 인덱스 (0-based)
        original_grid: 원본 그리드 (T가 변환되기 전)
    
    Returns:
        int: 조정된 인덱스 (0-based)
    """
    if not original_grid or cell_index < 0:
        return cell_index
    
    t_count = count_t_before_cell_index(original_grid, cell_index)
    adjusted_index = max(0, cell_index - t_count)
    return adjusted_index

def get_reconstructed_group_from_index(adjusted_index):
    """
    조정된 인덱스로부터 T-Removed Reconstructed Grid의 그룹 범위를 계산
    그룹은 3열씩이므로, 인덱스를 열로 변환하여 그룹 범위를 계산
    
    Args:
        adjusted_index: 조정된 셀 인덱스 (0-based)
    
    Returns:
        str: T-Removed Reconstructed Grid의 그룹 범위 (예: "2-4")
    """
    if adjusted_index is None or adjusted_index < 0:
        return None
    
    # 인덱스를 열과 행으로 변환
    col = adjusted_index // TABLE_HEIGHT
    row = adjusted_index % TABLE_HEIGHT
    
    # 그룹은 3열씩이므로, 시작 열과 끝 열 계산
    group_start_col = col
    group_end_col = group_start_col + 2  # 3열 그룹
    
    # 1-based로 변환하여 반환
    return f"{group_start_col + 1}-{group_end_col + 1}"

def convert_flag_group_to_reconstructed_group(converted_group_range, original_grid):
    """
    Flag 조건 충족 시점의 Converted Grid 그룹을 T-Removed Reconstructed Grid 그룹으로 변환
    독립적으로 구현된 함수들을 사용
    
    Args:
        converted_group_range: Converted Grid의 그룹 범위 문자열 (예: "4-6")
        original_grid: 원본 그리드 (T가 변환되기 전)
    
    Returns:
        str: T-Removed Reconstructed Grid의 그룹 범위 (예: "2-4")
    """
    # 1. Flag 시점의 셀 인덱스 계산
    flag_index = get_flag_triggered_cell_index(converted_group_range)
    if flag_index is None:
        return converted_group_range
    
    # 2. T 개수만큼 인덱스 조정
    adjusted_index = adjust_index_by_t_count(flag_index, original_grid)
    
    # 3. 조정된 인덱스로 그룹 계산
    reconstructed_group = get_reconstructed_group_from_index(adjusted_index)
    
    return reconstructed_group if reconstructed_group else converted_group_range

def get_cell_position_from_index(cell_index):
    """
    셀 인덱스로부터 열과 행 위치를 계산
    
    Args:
        cell_index: 셀 인덱스 (0-based)
    
    Returns:
        tuple: (열, 행) 위치 (0-based)
    """
    if cell_index is None or cell_index < 0:
        return None, None
    
    col = cell_index // TABLE_HEIGHT
    row = cell_index % TABLE_HEIGHT
    return col, row

def get_detailed_flag_info(converted_group_range, original_grid):
    """
    Flag 조건 충족 시점의 상세 정보를 반환
    
    Args:
        converted_group_range: Converted Grid의 그룹 범위 문자열 (예: "4-6")
        original_grid: 원본 그리드 (T가 변환되기 전)
    
    Returns:
        dict: 상세 정보 딕셔너리
            - converted_cell_index: Converted Grid 셀 인덱스
            - converted_col: Converted Grid 열 위치 (1-based)
            - converted_row: Converted Grid 행 위치 (1-based)
            - t_count: T 개수 (해당 셀 인덱스 이전까지)
            - t_count_debug: 디버깅용 상세 T 개수 정보
            - reconstructed_cell_index: T-Removed Reconstructed Grid 셀 인덱스
            - reconstructed_col: T-Removed Reconstructed Grid 열 위치 (1-based)
            - reconstructed_row: T-Removed Reconstructed Grid 행 위치 (1-based)
            - reconstructed_group: T-Removed Reconstructed Grid 그룹 범위
    """
    # 1. Flag 시점의 셀 인덱스 계산
    flag_index = get_flag_triggered_cell_index(converted_group_range)
    if flag_index is None:
        return None
    
    # 2. Converted Grid 위치 정보
    converted_col, converted_row = get_cell_position_from_index(flag_index)
    
    # 3. T 개수 계산 (디버깅 정보 포함)
    t_count = count_t_before_cell_index(original_grid, flag_index)
    
    # 디버깅: T 개수 계산 상세 정보
    t_count_debug = []
    current_index = 0
    for x in range(TABLE_WIDTH):
        for y in range(TABLE_HEIGHT):
            if current_index >= flag_index:
                break
            if original_grid[x][y] == 't':
                t_count_debug.append(f"열{x+1}행{y+1}(인덱스{current_index})")
            current_index += 1
        if current_index >= flag_index:
            break
    
    # 4. T 개수만큼 인덱스 조정
    adjusted_index = adjust_index_by_t_count(flag_index, original_grid)
    
    # 5. T-Removed Reconstructed Grid 위치 정보
    reconstructed_col, reconstructed_row = get_cell_position_from_index(adjusted_index)
    
    # 6. 그룹 범위 계산
    reconstructed_group = get_reconstructed_group_from_index(adjusted_index)
    
    return {
        'converted_cell_index': flag_index,
        'converted_col': converted_col + 1 if converted_col is not None else None,  # 1-based
        'converted_row': converted_row + 1 if converted_row is not None else None,  # 1-based
        't_count': t_count,
        't_count_debug': t_count_debug,
        'reconstructed_cell_index': adjusted_index,
        'reconstructed_col': reconstructed_col + 1 if reconstructed_col is not None else None,  # 1-based
        'reconstructed_row': reconstructed_row + 1 if reconstructed_row is not None else None,  # 1-based
        'reconstructed_group': reconstructed_group
    }

# ============================================================================
# 3행3열 범위 T 개수 계산 함수 (독립적 구현)
# ============================================================================

def count_t_in_range_3x3(original_grid):
    """
    3행3열까지 포함한 범위의 T 개수를 계산 (독립적 구현)
    - 1열: 모든 행 (1-6행, 0-based: 0-5)
    - 2열: 모든 행 (1-6행, 0-based: 0-5)
    - 3열: 1-3행만 (0-based: 0-2)
    
    Args:
        original_grid: 원본 그리드 (T가 변환되기 전)
    
    Returns:
        int: 범위 내의 T 개수
    """
    if not original_grid:
        return 0
    
    grid_height = len(original_grid[0]) if original_grid and original_grid[0] else 0
    
    t_count = 0
    # 1열 (0): 모든 행 (0-5)
    # 2열 (1): 모든 행 (0-5)
    # 3열 (2): 1-3행만 (0-2)
    for col_offset in range(3):
        x = col_offset
        if x >= len(original_grid):
            break
        
        # 1열, 2열은 모든 행, 3열은 3행까지
        max_row = grid_height if col_offset < 2 else 3
        
        for y in range(min(max_row, grid_height)):
            if original_grid[x][y] == 't':
                t_count += 1
    
    return t_count

def display_t_count_3x3(original_grid):
    """
    3행3열 범위의 T 개수를 오른쪽 상단에 표시 (최소한의 영역, 2줄)
    
    Args:
        original_grid: 원본 그리드 (T가 변환되기 전)
    """
    if not original_grid:
        return
    
    t_count = count_t_in_range_3x3(original_grid)
    
    # 오른쪽 정렬된 컴팩트한 표시 (2줄)
    st.markdown(f"""
    <div style="text-align: right; margin-bottom: 0.5rem;">
        <div style="font-size: 0.9rem; color: #666;">3행3열 범위 T 개수</div>
        <div style="font-size: 1.2rem; font-weight: bold; color: #1e40af;">{t_count}</div>
    </div>
    """, unsafe_allow_html=True)

# ============================================================================
# 기존 함수들
# ============================================================================

def get_original_t_count_before_column(original_grid, column_index):
    """
    원본 그리드에서 특정 열 이전에 있는 T의 개수를 세는 함수
    열 우선 순서로 특정 열 이전까지의 T 개수를 계산
    
    Args:
        original_grid: 원본 그리드 (T가 변환되기 전, aligned_grid)
        column_index: 열 인덱스 (0-based, 이 열 이전까지 계산)
    
    Returns:
        int: 특정 열 이전까지 열 우선 순서로 센 T의 개수
    """
    if not original_grid or column_index < 0:
        return 0
    
    t_count = 0
    # 열 우선 순서로 특정 열 이전까지의 T 개수 세기
    for x in range(column_index):  # column_index 이전까지
        for y in range(TABLE_HEIGHT):
            if original_grid[x][y] == 't':
                t_count += 1
    
    return t_count

def convert_group_to_reconstructed_index(converted_group_range, original_grid):
    """
    Converted Grid의 그룹 범위를 T-Removed Reconstructed Grid의 그룹 범위로 변환
    단순하게 그룹 시작 열 이전의 T 개수만큼 인덱스를 조정
    
    Args:
        converted_group_range: Converted Grid의 그룹 범위 문자열 (예: "4-6")
        original_grid: 원본 그리드 (T가 변환되기 전, aligned_grid)
    
    Returns:
        str: T-Removed Reconstructed Grid의 그룹 범위 (예: "2-4")
    """
    try:
        # 그룹 범위 파싱 (예: "4-6" -> start=3, end=5)
        parts = converted_group_range.split('-')
        if len(parts) != 2:
            return converted_group_range
        
        start_col = int(parts[0]) - 1  # 1-based to 0-based
        end_col = int(parts[1]) - 1    # 1-based to 0-based
        
        # 그룹 시작 열 이전의 T 개수 계산 (열 우선 순서)
        t_count = get_original_t_count_before_column(original_grid, start_col)
        
        # T 개수만큼 인덱스 조정
        reconstructed_start = max(0, start_col - t_count)
        reconstructed_end = max(0, end_col - t_count)
        
        # 1-based로 변환하여 반환
        return f"{reconstructed_start + 1}-{reconstructed_end + 1}"
    except Exception as e:
        return converted_group_range

def remove_tie_and_reconstruct_grid(grid):
    """
    6행×15열 그리드 구조를 유지하면서 T를 제거하고 재구성
    
    과정:
    1. 전체 그리드를 열 우선 순서로 1차원 배열로 펼치기
    2. T 값 제거
    3. 남은 값들로 6행×15열 그리드 재구성
    """
    # 1단계: 그리드를 열 우선 순서로 1차원 배열로 펼치기
    flattened_values = []
    for x in range(TABLE_WIDTH):  # 열 우선 순회 (0~14)
        for y in range(TABLE_HEIGHT):  # 각 열의 행 순회 (0~5)
            cell_value = grid[x][y]
            if cell_value != 't' and cell_value != '':  # T가 아니고 빈 값도 아닌 경우
                flattened_values.append(cell_value)
    
    # 2단계: 새로운 6행×15열 그리드 생성
    new_grid = [['' for _ in range(TABLE_HEIGHT)] for _ in range(TABLE_WIDTH)]
    
    # 3단계: 남은 값들을 열 우선 순서로 새로운 그리드에 배치
    value_index = 0
    for x in range(TABLE_WIDTH):  # 열 우선 순회
        for y in range(TABLE_HEIGHT):  # 각 열의 행 순회
            if value_index < len(flattened_values):
                new_grid[x][y] = flattened_values[value_index]
                value_index += 1
            else:
                break  # 더 이상 채울 값이 없으면 중단
        if value_index >= len(flattened_values):
            break
    
    return new_grid

def divide_grid_into_overlapping_zones_for_reconstructed(grid, zone_width=3):
    """
    T 제거 재구성 그리드용 독립적인 구역 분할 함수
    """
    zones = []
    for start_x in range(15 - zone_width + 1):
        end_x = start_x + zone_width
        zone_data = [[grid[x][y] for y in range(6)] for x in range(start_x, end_x)]
        
        # 기본 조건: b, t, p가 있는지 확인
        has_basic_data = any(cell in {'b', 't', 'p'} for column in zone_data for cell in column)
        
        # 앞 그룹의 Pattern 4 번호가 추출되면 다음 그룹 노출 조건
        should_show_next_group = False
        if start_x > 0:  # 첫 번째 그룹이 아닌 경우
            # 앞 그룹의 Pattern 4 위치 확인 (start_x-1, end_x-1 범위)
            prev_zone_data = [[grid[x][y] for y in range(6)] for x in range(start_x-1, end_x-1)]
            patterns = get_pattern_positions()
            prev_group_patterns = [p for p in patterns if p['columns'][0] >= start_x-1 and p['columns'][1] <= end_x-1]
            
            if len(prev_group_patterns) >= 4:
                # 앞 그룹의 Pattern 4 값 추출
                pattern4_values = []
                for x, y in prev_group_patterns[3]['coordinates']:
                    relative_x = x - (start_x-1)
                    value = prev_zone_data[relative_x][y]
                    if value:
                        pattern4_values.append(value.upper())
                
                # Pattern 4 번호가 추출되면 다음 그룹 노출
                if pattern4_values:
                    pattern4_number = find_pattern_number_only([x.lower() for x in pattern4_values])
                    if pattern4_number and pattern4_number != '-':
                        should_show_next_group = True
        
        # 첫 번째 그룹이거나 앞 그룹의 Pattern 4가 추출된 경우 표시
        if has_basic_data and (start_x == 0 or should_show_next_group):
            zones.append({
                'zone_data': zone_data,
                'start_x': start_x,
                'end_x': end_x - 1
            })
    return zones

def get_first_two_group_values_for_reconstructed(zone):
    """
    T 제거 재구성 그리드용 독립적인 첫 2개 그룹 값 추출 함수
    """
    patterns = get_pattern_positions()
    group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
    
    if len(group_patterns) < 4:
        return ''
        
    pattern_values = []
    for pattern in group_patterns[:4]:
        values = []
        for x, y in pattern['coordinates']:
            relative_x = x - zone['start_x']
            value = zone['zone_data'][relative_x][y]
            if value:
                values.append(value.upper())
        pattern_values.append(values)
        
    groups_123 = []
    pattern_123_valid = True
    
    if len(pattern_values) >= 3:
        for i in range(3):
            if not pattern_values[i]:
                pattern_123_valid = False
                break
            group = find_pattern_group(pattern_values[i])
            if group is None:
                pattern_123_valid = False
                break
            groups_123.append(group)
    
    pattern_123_text = ''.join(groups_123) if pattern_123_valid and len(groups_123) == 3 else ''
    return pattern_123_text[:2] if len(pattern_123_text) >= 2 else ''

def display_pattern1_sequence_prediction_for_reconstructed(zones):
    """Pattern 1 Number의 Sequence prediction 예측값을 표시하는 독립적인 함수"""
    if not zones:
        return
    
    try:
        # Group 1-3 zone만 처리 (start_x=0, end_x=2)
        group_1_3_zones = [zone for zone in zones if zone['start_x'] == 0 and zone['end_x'] == 2]
        
        if not group_1_3_zones:
            return
            
        zone = group_1_3_zones[0]  # Group 1-3 zone
        
        # 패턴 위치 정보 가져오기
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 1:
            return
        
        # Pattern 1 값 추출
        pattern1_values = []
        for x, y in group_patterns[0]['coordinates']:
            relative_x = x - zone['start_x']
            value = zone['zone_data'][relative_x][y]
            if value:
                pattern1_values.append(value.upper())
        
        # Pattern 1 번호 추출
        pattern1_number = find_pattern_number_only([x.lower() for x in pattern1_values]) if pattern1_values else None
        
        if pattern1_number and pattern1_number != '-':
            # 패턴 번호 포맷팅
            def format_pattern_number(pattern_num):
                if pattern_num and len(pattern_num) > 2:
                    return pattern_num[:2]
                elif pattern_num and len(pattern_num) == 1:
                    return '0' + pattern_num
                else:
                    return pattern_num
            
            pattern1_formatted = format_pattern_number(pattern1_number)
            
            # P_Sequence와 B_Sequence 예측값 조회
            p_predicted_value, p_found, _, _, p_gap = get_best_prediction_from_sequence_table(pattern1_formatted, 'P_Sequence')
            b_predicted_value, b_found, _, _, b_gap = get_best_prediction_from_sequence_table(pattern1_formatted, 'B_Sequence')
            
            # 결과 표시
            st.markdown("#### Group 1-3 Pattern 1 Sequence Prediction")
            st.text(f"Pattern 1 Number: {pattern1_number}")
            
            if p_found:
                gap_text = f" Gap={'T' if p_gap > 0 else 'F'}"
                st.text(f"Pattern 1 P_Sequence prediction: {p_predicted_value.upper()}{gap_text}")
            else:
                st.text("Pattern 1 P_Sequence prediction: N/A")
            
            if b_found:
                gap_text = f" Gap={'T' if b_gap > 0 else 'F'}"
                st.text(f"Pattern 1 B_Sequence prediction: {b_predicted_value.upper()}{gap_text}")
            else:
                st.text("Pattern 1 B_Sequence prediction: N/A")
            
            st.markdown("---")
        
    except Exception as e:
        st.error(f"Pattern 1 Sequence Prediction 표시 오류: {str(e)}")

def display_pattern_groups_for_reconstructed(zones):
    """
    T 제거 재구성 그리드용 독립적인 패턴 그룹 분석 표시 함수
    """
    if not zones:
        return
    
    st.markdown("### Pattern Group Analysis (T-Removed Reconstructed)")
    
    # Pattern 1 Number Sequence Prediction (독립적인 새 기능)
    display_pattern1_sequence_prediction_for_reconstructed(zones)
    
    # Display all groups' first 2 values concatenated
    all_first_two = ''
    for zone in zones:
        first_two = get_first_two_group_values_for_reconstructed(zone)
        if first_two:
            all_first_two += first_two
    
    if all_first_two:
        st.text(f"All groups' first 2 values: {all_first_two}")
        st.markdown("---")
    
    # Sort zones by start_x to display in order
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    for zone in sorted_zones:
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            continue
            
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
            
        # 각 패턴별 넘버 리스트
        pattern_numbers = []
        for v in pattern_values[:4]:
            pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
            pattern_numbers.append(pattern_number if pattern_number is not None else '-')
        
        # 넘버 가공
        numbers_dict = process_pattern_numbers(pattern_numbers)
        
        groups_123 = []
        groups_1234 = []
        pattern_123_valid = True
        if len(pattern_values) >= 3:
            for i in range(3):
                if not pattern_values[i]:
                    pattern_123_valid = False
                    break
                group = find_pattern_group(pattern_values[i])
                if group is None:
                    pattern_123_valid = False
                    break
                groups_123.append(group)
        
        pattern_1234_valid = True
        if len(pattern_values) >= 4:
            for i in range(4):
                if not pattern_values[i]:
                    pattern_1234_valid = False
                    break
                group = find_pattern_group(pattern_values[i])
                if group is None:
                    pattern_1234_valid = False
                    break
                groups_1234.append(group)
        
        pattern_123_text = ''.join(groups_123) if pattern_123_valid and len(groups_123) == 3 else ''
        pattern_1234_text = ''.join(groups_1234) if pattern_1234_valid and len(groups_1234) == 4 else ''
        
        first_two = get_first_two_group_values_for_reconstructed(zone)
        group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
        
        # 패턴 번호가 있는지 확인
        has_pattern_numbers = any(pattern_num != '-' for pattern_num in pattern_numbers[:4])
        
        if any([pattern_123_text, pattern_1234_text, first_two]) or has_pattern_numbers:
            st.markdown(f"#### Group {group_range}")
            for idx, v in enumerate(pattern_values[:4]):
                pattern_number = pattern_numbers[idx]
                st.text(f"Pattern {idx+1} Number: {pattern_number if pattern_number is not None else '-'}")
            
            # Add combined pattern numbers display
            if len(pattern_numbers) >= 2:
                pattern1_2 = pattern_numbers[0] + pattern_numbers[1] if pattern_numbers[0] != '-' and pattern_numbers[1] != '-' else '-'
                st.text(f"Pattern 1,2: {pattern1_2}")
            
            if len(pattern_numbers) >= 3:
                pattern1_2_3 = pattern_numbers[0] + pattern_numbers[1] + pattern_numbers[2] if all(p != '-' for p in pattern_numbers[:3]) else '-'
                st.text(f"Pattern 1,2,3: {pattern1_2_3}")
            
            # Add pattern 3,4 combined display
            if len(pattern_numbers) >= 4:
                pattern3_4 = pattern_numbers[2] + pattern_numbers[3] if pattern_numbers[2] != '-' and pattern_numbers[3] != '-' else '-'
                st.text(f"Pattern 3,4: {pattern3_4}")
            
            st.text(f"Pattern 1,2,3 Group: {pattern_123_text}")
            st.text(f"Pattern 1,2,3,4 Group: {pattern_1234_text}")
            st.text(f"First 2 values: {first_two}")
            st.markdown("---")

def display_session_prediction_results_for_reconstructed(zones):
    """Display Session Prediction Results and Sequence Prediction Results for T-Removed Reconstructed Grid"""
    if not zones:
        return
    
    # Session Prediction Results: left to right
    sorted_zones_results = sorted(zones, key=lambda x: x['start_x'])
    
    # Collect all prediction results (Session Prediction Results)
    all_prediction_results = []
    for zone in sorted_zones_results:
        pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results_for_reconstructed(zone)
        if comparison1_2:
            all_prediction_results.append(comparison1_2.upper())
        if comparison1_2_3:
            all_prediction_results.append(comparison1_2_3.upper())
    
    # Display combined prediction results (숨김 처리)
    # if all_prediction_results:
    #     combined_results = ''.join(all_prediction_results)
    #     st.markdown("### Session Prediction Results (T-Removed Reconstructed)")
    #     st.markdown(f"**{combined_results}**")
    #     st.markdown("---")
    
    # Display sequence prediction results using pattern_sequence_prediction table for T-Removed Reconstructed
    sequence_prediction_results = generate_sequence_prediction_results_for_reconstructed(zones)
    if sequence_prediction_results:
        st.markdown("### Sequence Prediction Results (T-Removed Reconstructed)")
        st.markdown(f"**{sequence_prediction_results}**")
        st.markdown("---")
    
    # Display high probability gap results for T-Removed Reconstructed
    high_probability_gap_results = generate_high_probability_gap_results_for_reconstructed(zones)
    if high_probability_gap_results:
        st.markdown("### High Probability Gap Results (T-Removed Reconstructed)")
        st.markdown(f"**{high_probability_gap_results}**")
        
        # Display comparison results (P/F based on sequence prediction results)
        show_gap_comparison_results = False
        high_probability_gap_comparison_results = generate_high_probability_gap_comparison_results_for_reconstructed(zones)
        if show_gap_comparison_results and high_probability_gap_comparison_results:
            st.markdown("### High Probability Gap Comparison Results (T-Removed Reconstructed)")
            st.markdown(f"**{high_probability_gap_comparison_results}**")
        
        st.markdown("---")
        
        # 새로운 독립적인 예측값 표시
        display_independent_prediction_results_for_reconstructed(zones)

def display_session_prediction_results_main(zones):
    """Display Session Prediction Results for Converted Grid"""
    if not zones:
        return

    sorted_zones_results = sorted(zones, key=lambda x: x['start_x'])
    all_prediction_results = []
    for zone in sorted_zones_results:
        pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
        if comparison1_2:
            all_prediction_results.append(comparison1_2.upper())
        if comparison1_2_3:
            all_prediction_results.append(comparison1_2_3.upper())

    if all_prediction_results:
        combined_results = ''.join(all_prediction_results)
        st.markdown("### Session Prediction Results")
        st.markdown(f"**{combined_results}**")
        st.markdown("---")

def display_independent_prediction_results_for_reconstructed(zones):
    """기존 코드와 독립적인 예측값 표시 함수"""
    if not zones:
        return
    
    try:
        # Zone을 왼쪽→오른쪽 순서로 정렬
        sorted_zones = sorted(zones, key=lambda x: x['start_x'])
        
        all_predictions = []  # 예측값만 저장
        
        for zone in sorted_zones:
            # 패턴 위치 정보 가져오기
            patterns = get_pattern_positions()
            group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
            
            if len(group_patterns) < 2:
                continue
            
            # 패턴 값 추출
            pattern_values = []
            for pattern in group_patterns[:2]:  # Pattern 1, Pattern 2만
                values = []
                for x, y in pattern['coordinates']:
                    relative_x = x - zone['start_x']
                    value = zone['zone_data'][relative_x][y]
                    if value:
                        values.append(value.upper())
                pattern_values.append(values)
            
            # 패턴 번호 추출
            pattern_numbers = []
            for v in pattern_values:
                pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
                pattern_numbers.append(pattern_number if pattern_number is not None else '-')
            
            # Pattern 1, Pattern 2 번호 가져오기
            pattern1_number = pattern_numbers[0] if pattern_numbers[0] != '-' else ''
            pattern2_number = pattern_numbers[1] if len(pattern_numbers) > 1 and pattern_numbers[1] != '-' else ''
            
            # 패턴 번호 포맷팅
            def format_pattern_number(pattern_num):
                if pattern_num and len(pattern_num) > 2:
                    return pattern_num[:2]
                elif pattern_num and len(pattern_num) == 1:
                    return '0' + pattern_num
                else:
                    return pattern_num
            
            pattern1_formatted = format_pattern_number(pattern1_number)
            pattern2_formatted = format_pattern_number(pattern2_number)
            
            # 시퀀스 타입 결정
            pattern1_sequence_value = zone['zone_data'][2][0] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 0 else ''
            pattern2_sequence_value = zone['zone_data'][2][3] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 3 else ''
            
            sequence_type1 = 'P_Sequence' if pattern1_sequence_value.upper() == 'P' else 'B_Sequence' if pattern1_sequence_value.upper() == 'B' else ''
            sequence_type2 = 'P_Sequence' if pattern2_sequence_value.upper() == 'P' else 'B_Sequence' if pattern2_sequence_value.upper() == 'B' else ''
            
            # Pattern 1 예측값 조회
            if pattern1_formatted and sequence_type1:
                predicted_value, found, _, _, _ = get_best_prediction_from_sequence_table(pattern1_formatted, sequence_type1)
                if found:
                    all_predictions.append(predicted_value)
            
            # Pattern 2 예측값 조회
            if pattern2_formatted and sequence_type2:
                predicted_value, found, _, _, _ = get_best_prediction_from_sequence_table(pattern2_formatted, sequence_type2)
                if found:
                    all_predictions.append(predicted_value)
        
        # 패턴 상세정보 표시
        display_pattern_details_for_reconstructed(zones)
        
    except Exception as e:
        st.error(f"독립적인 예측값 표시 오류: {str(e)}")

def display_pattern_details_for_reconstructed(zones):
    """패턴 상세정보를 Group 형식으로 표시하는 독립적인 함수"""
    if not zones:
        return
    
    try:
        # Zone을 오른쪽→왼쪽 순서로 정렬 (Group Results와 동일한 순서)
        sorted_zones = sorted(zones, key=lambda x: x['start_x'], reverse=True)
        
        for zone in sorted_zones:
            group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
            
            # 패턴 위치 정보 가져오기
            patterns = get_pattern_positions()
            group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
            
            if len(group_patterns) < 4:
                continue
            
            # 패턴 값 추출
            pattern_values = []
            for pattern in group_patterns[:4]:
                values = []
                for x, y in pattern['coordinates']:
                    relative_x = x - zone['start_x']
                    value = zone['zone_data'][relative_x][y]
                    if value:
                        values.append(value.upper())
                pattern_values.append(values)
            
            # 패턴 번호 추출
            pattern_numbers = []
            for v in pattern_values:
                pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
                pattern_numbers.append(pattern_number if pattern_number is not None else '-')
            
            # 패턴 번호 가공
            numbers_dict = process_pattern_numbers(pattern_numbers)
            
            # Pattern 1,2 조합
            pattern1_2_combined = numbers_dict.get('pattern1_2_combined', '-')
            pattern1_2_result = zone['zone_data'][2][1] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 1 else ''
            
            # Pattern 1,2,3 조합
            pattern1_2_3_combined = numbers_dict.get('pattern1_2_3_combined', '-')
            pattern1_2_3_result = zone['zone_data'][2][4] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 4 else ''
            
            # Pattern 3,4 조합
            pattern3_4_combined = numbers_dict.get('pattern3_4_combined', '-')
            
            # 시퀀스 타입 결정
            pattern1_sequence_value = zone['zone_data'][2][0] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 0 else ''
            pattern2_sequence_value = zone['zone_data'][2][3] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 3 else ''
            
            sequence_type1 = 'P_Sequence' if pattern1_sequence_value.upper() == 'P' else 'B_Sequence' if pattern1_sequence_value.upper() == 'B' else ''
            sequence_type2 = 'P_Sequence' if pattern2_sequence_value.upper() == 'P' else 'B_Sequence' if pattern2_sequence_value.upper() == 'B' else ''
            
            # 패턴 번호 포맷팅
            def format_pattern_number(pattern_num):
                if pattern_num and len(pattern_num) > 2:
                    return pattern_num[:2]
                elif pattern_num and len(pattern_num) == 1:
                    return '0' + pattern_num
                else:
                    return pattern_num
            
            pattern1_formatted = format_pattern_number(pattern_numbers[0]) if pattern_numbers[0] != '-' else ''
            pattern2_formatted = format_pattern_number(pattern_numbers[1]) if len(pattern_numbers) > 1 and pattern_numbers[1] != '-' else ''
            
            # Pattern 1,2 예측 및 비교
            pattern1_2_prediction = ''
            pattern1_2_comparison = ''
            if pattern1_formatted and sequence_type1:
                predicted_value, found, _, _, _ = get_best_prediction_from_sequence_table(pattern1_formatted, sequence_type1)
                if found:
                    pattern1_2_prediction = predicted_value
                    if pattern1_2_result:
                        pattern1_2_comparison = 'W' if pattern1_2_result.upper() == predicted_value.upper() else 'L'
            
            # Pattern 1,2,3 예측 및 비교
            pattern1_2_3_prediction = ''
            pattern1_2_3_comparison = ''
            if pattern2_formatted and sequence_type2:
                predicted_value, found, _, _, _ = get_best_prediction_from_sequence_table(pattern2_formatted, sequence_type2)
                if found:
                    pattern1_2_3_prediction = predicted_value
                    if pattern1_2_3_result:
                        pattern1_2_3_comparison = 'W' if pattern1_2_3_result.upper() == predicted_value.upper() else 'L'
            
            # 표시할 내용이 있는지 확인
            has_content = any([
                pattern1_2_result, pattern1_2_3_result, pattern1_2_prediction, 
                pattern1_2_3_prediction, pattern1_2_comparison, pattern1_2_3_comparison
            ])
            is_group_1_3 = (zone['start_x'] == 0 and zone['end_x'] == 2)
            
            # 패턴 번호가 있는지 확인
            has_pattern_numbers = any(pattern_num != '-' for pattern_num in pattern_numbers[:4])
            
            if not has_content and not is_group_1_3 and not has_pattern_numbers:
                continue  # 표시할 내용이 없으면 건너뛰기
            
            # Group 정보 표시
            st.markdown(f"#### Pattern Analysis Details - Group {group_range} (T-Removed Reconstructed)")
            
            # Pattern 1 상세 정보
            st.markdown("**Pattern 1:**")
            st.text(f"Pattern1 number: {pattern_numbers[0] if pattern_numbers[0] != '-' else 'N/A'}")
            
            # Pattern 1 시퀀스 타입별 예측값 표시
            pattern1_formatted = format_pattern_number(pattern_numbers[0]) if pattern_numbers[0] != '-' else ''
            if pattern1_formatted:
                # P_Sequence 예측값
                p_predicted_value, p_found, _, _, p_gap = get_best_prediction_from_sequence_table(pattern1_formatted, 'P_Sequence')
                if p_found:
                    gap_text = f" Gap={'T' if p_gap > 0 else 'F'}"
                    st.text(f"Pattern1 P_Sequence prediction: {p_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern1 P_Sequence prediction: N/A")
                
                # B_Sequence 예측값
                b_predicted_value, b_found, _, _, b_gap = get_best_prediction_from_sequence_table(pattern1_formatted, 'B_Sequence')
                if b_found:
                    gap_text = f" Gap={'T' if b_gap > 0 else 'F'}"
                    st.text(f"Pattern1 B_Sequence prediction: {b_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern1 B_Sequence prediction: N/A")
            else:
                st.text("Pattern1 P_Sequence prediction: N/A")
                st.text("Pattern1 B_Sequence prediction: N/A")
            
            if pattern1_2_result:
                st.text(f"Pattern 1 result: {pattern1_2_result.upper()}")
            else:
                st.text("Pattern 1 result: N/A")
            if pattern1_2_comparison:
                st.text(f"Prediction Result: {pattern1_2_comparison.upper()}")
            else:
                st.text("Prediction Result: N/A")
            
            st.markdown("---")
            
            # Pattern 2 상세 정보
            st.markdown("**Pattern 2:**")
            st.text(f"Pattern2 number: {pattern_numbers[1] if len(pattern_numbers) > 1 and pattern_numbers[1] != '-' else 'N/A'}")
            
            # Pattern 2 시퀀스 타입별 예측값 표시
            pattern2_formatted = format_pattern_number(pattern_numbers[1]) if len(pattern_numbers) > 1 and pattern_numbers[1] != '-' else ''
            if pattern2_formatted:
                # P_Sequence 예측값
                p_predicted_value, p_found, _, _, p_gap = get_best_prediction_from_sequence_table(pattern2_formatted, 'P_Sequence')
                if p_found:
                    gap_text = f" Gap={'T' if p_gap > 0 else 'F'}"
                    st.text(f"Pattern2 P_Sequence prediction: {p_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern2 P_Sequence prediction: N/A")
                
                # B_Sequence 예측값
                b_predicted_value, b_found, _, _, b_gap = get_best_prediction_from_sequence_table(pattern2_formatted, 'B_Sequence')
                if b_found:
                    gap_text = f" Gap={'T' if b_gap > 0 else 'F'}"
                    st.text(f"Pattern2 B_Sequence prediction: {b_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern2 B_Sequence prediction: N/A")
            else:
                st.text("Pattern2 P_Sequence prediction: N/A")
                st.text("Pattern2 B_Sequence prediction: N/A")
            
            if pattern1_2_3_result:
                st.text(f"Pattern 2 result: {pattern1_2_3_result.upper()}")
            else:
                st.text("Pattern 2 result: N/A")
            if pattern1_2_3_comparison:
                st.text(f"Prediction Result: {pattern1_2_3_comparison.upper()}")
            else:
                st.text("Prediction Result: N/A")
            
            st.markdown("---")
            
            # Pattern 3 상세 정보
            st.markdown("**Pattern 3:**")
            st.text(f"Pattern3 number: {pattern_numbers[2] if len(pattern_numbers) > 2 and pattern_numbers[2] != '-' else 'N/A'}")
            
            # Pattern 3 시퀀스 타입별 예측값 표시
            pattern3_formatted = format_pattern_number(pattern_numbers[2]) if len(pattern_numbers) > 2 and pattern_numbers[2] != '-' else ''
            if pattern3_formatted:
                # P_Sequence 예측값
                p_predicted_value, p_found, _, _, p_gap = get_best_prediction_from_sequence_table(pattern3_formatted, 'P_Sequence')
                if p_found:
                    gap_text = f" Gap={'T' if p_gap > 0 else 'F'}"
                    st.text(f"Pattern3 P_Sequence prediction: {p_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern3 P_Sequence prediction: N/A")
                
                # B_Sequence 예측값
                b_predicted_value, b_found, _, _, b_gap = get_best_prediction_from_sequence_table(pattern3_formatted, 'B_Sequence')
                if b_found:
                    gap_text = f" Gap={'T' if b_gap > 0 else 'F'}"
                    st.text(f"Pattern3 B_Sequence prediction: {b_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern3 B_Sequence prediction: N/A")
            else:
                st.text("Pattern3 P_Sequence prediction: N/A")
                st.text("Pattern3 B_Sequence prediction: N/A")
            
            st.text("Pattern 3 result: N/A")
            st.text("Prediction Result: N/A")
            
            st.markdown("---")
            
            # Pattern 4 상세 정보
            st.markdown("**Pattern 4:**")
            st.text(f"Pattern4 number: {pattern_numbers[3] if len(pattern_numbers) > 3 and pattern_numbers[3] != '-' else 'N/A'}")
            
            # Pattern 4 시퀀스 타입별 예측값 표시
            pattern4_formatted = format_pattern_number(pattern_numbers[3]) if len(pattern_numbers) > 3 and pattern_numbers[3] != '-' else ''
            if pattern4_formatted:
                # P_Sequence 예측값
                p_predicted_value, p_found, _, _, p_gap = get_best_prediction_from_sequence_table(pattern4_formatted, 'P_Sequence')
                if p_found:
                    gap_text = f" Gap={'T' if p_gap > 0 else 'F'}"
                    st.text(f"Pattern4 P_Sequence prediction: {p_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern4 P_Sequence prediction: N/A")
                
                # B_Sequence 예측값
                b_predicted_value, b_found, _, _, b_gap = get_best_prediction_from_sequence_table(pattern4_formatted, 'B_Sequence')
                if b_found:
                    gap_text = f" Gap={'T' if b_gap > 0 else 'F'}"
                    st.text(f"Pattern4 B_Sequence prediction: {b_predicted_value.upper()}{gap_text}")
                else:
                    st.text("Pattern4 B_Sequence prediction: N/A")
            else:
                st.text("Pattern4 P_Sequence prediction: N/A")
                st.text("Pattern4 B_Sequence prediction: N/A")
            
            st.text("Pattern 4 result: N/A")
            st.text("Prediction Result: N/A")
            
            st.markdown("---")
        
    except Exception as e:
        st.error(f"패턴 상세정보 표시 오류: {str(e)}")

def get_pattern_results_for_reconstructed(zone):
    """Extract pattern results and predictions from T-Removed Reconstructed zone data"""
    try:
        # Get Pattern 1,2 result (2nd row 3rd column)
        pattern1_2 = zone['zone_data'][2][1] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 1 else ''
        # Get Pattern 1,2,3 result (5th row 3rd column)
        pattern1_2_3 = zone['zone_data'][2][4] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 4 else ''
        
        # Get patterns from zone for pattern number combination
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) >= 2:  # Pattern 1,2만 있어도 처리하도록 수정
            pattern_values = []
            for pattern in group_patterns[:4]:
                values = []
                for x, y in pattern['coordinates']:
                    relative_x = x - zone['start_x']
                    value = zone['zone_data'][relative_x][y]
                    if value:
                        values.append(value.upper())
                pattern_values.append(values)
                
            # Get pattern numbers
            pattern_numbers = []
            for v in pattern_values[:4]:
                pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
                pattern_numbers.append(pattern_number if pattern_number is not None else '-')
            
            # Get Pattern 1,2 combination
            pattern1_2_combined = pattern_numbers[0] + pattern_numbers[1] if pattern_numbers[0] != '-' and pattern_numbers[1] != '-' else '-'
            # Get Pattern 1,2,3 combination
            pattern1_2_3_combined = pattern_numbers[0] + pattern_numbers[1] + pattern_numbers[2] if all(p != '-' for p in pattern_numbers[:3]) else '-'
            # Get Pattern 3,4 combination
            pattern3_4_combined = pattern_numbers[2] + pattern_numbers[3] if pattern_numbers[2] != '-' and pattern_numbers[3] != '-' else '-'
        else:
            pattern1_2_combined = '-'
            pattern1_2_3_combined = '-'
            pattern3_4_combined = '-'
        
        # Get sequence types for each pattern
        sequence_type_12 = get_pattern_sequence_type(zone)
        sequence_type_123 = get_pattern123_sequence_type(zone)
        
        # Get predictions using hybrid functions
        prediction1_2, found1_2, source1_2 = get_hybrid_pattern_prediction(pattern1_2_combined, sequence_type_12)
        prediction1_2_3, found1_2_3, source1_2_3 = get_hybrid_pattern123_prediction(pattern1_2_3_combined, sequence_type_123)
        
        # Compare and get results
        comparison1_2 = compare_pattern_prediction(pattern1_2, prediction1_2)
        comparison1_2_3 = compare_pattern_prediction(pattern1_2_3, prediction1_2_3)
        
        return pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123
    except Exception as e:
        return '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''

def display_group_1_3_independent():
    """독립적으로 Group 1-3 (T-Removed Reconstructed) 표시"""
    if not hasattr(st.session_state, 'reconstructed_grid') or not st.session_state.reconstructed_grid:
        return
    
    # Group 1-3 zone 생성 (start_x=0, end_x=2)
    zone_data = [[st.session_state.reconstructed_grid[x][y] for y in range(6)] for x in range(0, 3)]
    zone = {
        'zone_data': zone_data,
        'start_x': 0,
        'end_x': 2
    }
    
    # Pattern 정보 추출
    patterns = get_pattern_positions()
    group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
    
    # Pattern 1,2 정보 추출
    pattern1_2_combined = '-'
    pattern1_2_3_combined = '-'
    pattern3_4_combined = '-'
    
    if len(group_patterns) >= 2:
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # Pattern numbers 추출
        pattern_numbers = []
        for v in pattern_values[:4]:
            pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
            pattern_numbers.append(pattern_number if pattern_number is not None else '-')
        
        # Combined patterns 생성
        pattern1_2_combined = pattern_numbers[0] + pattern_numbers[1] if pattern_numbers[0] != '-' and pattern_numbers[1] != '-' else '-'
        pattern1_2_3_combined = pattern_numbers[0] + pattern_numbers[1] + pattern_numbers[2] if all(p != '-' for p in pattern_numbers[:3]) else '-'
        pattern3_4_combined = pattern_numbers[2] + pattern_numbers[3] if pattern_numbers[2] != '-' and pattern_numbers[3] != '-' else '-'
    
    # Group 1-3 표시
    st.markdown("#### Group Results Summary - Group 1-3 (T-Removed Reconstructed)")
    st.text(f"Pattern 1,2 combined: {pattern1_2_combined}")
    st.text(f"Pattern 1,2,3 combined: {pattern1_2_3_combined}")
    st.text(f"Pattern 3,4 combined: {pattern3_4_combined}")
    st.markdown("---")

def display_group_results_for_reconstructed(zones):
    """Display Group Results for T-Removed Reconstructed Grid"""
    if not zones:
        return
    
    # Group info display: right to left
    sorted_zones_groups = sorted(zones, key=lambda x: x['start_x'], reverse=True)
    
    # Display individual group results (right to left)
    for zone in sorted_zones_groups:
        group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
        pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results_for_reconstructed(zone)

        # Group 1-3 조건 확인 (독립적으로 표시되므로 여기서는 제외)
        is_group_1_3 = (zone['start_x'] == 0 and zone['end_x'] == 2)
        
        # Group 1-3은 독립적으로 표시되므로 건너뛰기
        if is_group_1_3:
            continue
        
        # 내용 존재 여부 확인
        has_content = any([
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3
        ])
        
        # 내용이 없으면 건너뛰기
        if not has_content:
            continue

        st.markdown(f"#### Group Results Summary - Group {group_range} (T-Removed Reconstructed)")
        # Pattern 1,2 results
        st.text(f"Pattern 1,2 combined: {pattern1_2_combined}")
        if pattern1_2:
            st.text(f"Pattern 1,2 result: {pattern1_2.upper()}")
            if prediction1_2:
                source_emoji_12 = "🗄️" if source1_2 == "DB" else "📄" if source1_2 == "CSV" else "❓"
                st.text(f"Pattern 1,2 Prediction: {prediction1_2.upper()} (소스: {source_emoji_12} {source1_2})")
                st.text(f"Pattern 1,2 Prediction Result: {comparison1_2.upper()}")
            else:
                st.text("No Pattern 1,2 prediction found")
        # Pattern 1,2,3 results
        st.text(f"Pattern 1,2,3 combined: {pattern1_2_3_combined}")
        if pattern1_2_3:
            st.text(f"Pattern 1,2,3 result: {pattern1_2_3.upper()}")
            if prediction1_2_3:
                source_emoji_123 = "🗄️" if source1_2_3 == "DB" else "📄" if source1_2_3 == "CSV" else "❓"
                st.text(f"Pattern 1,2,3 Prediction: {prediction1_2_3.upper()} (소스: {source_emoji_123} {source1_2_3})")
                st.text(f"Pattern 1,2,3 Prediction Result: {comparison1_2_3.upper()}")
            else:
                st.text("No Pattern 1,2,3 prediction found")
        # Pattern 3,4 combined always at the end
        st.text(f"Pattern 3,4 combined: {pattern3_4_combined}")
        st.markdown("---")

def get_pattern_positions():
    patterns = []
    pattern_number = 1
    
    for start_col in range(TABLE_WIDTH - PATTERN_WIDTH + 1):
        cols = (start_col, start_col + 1)
        
        top_pattern = {
            'pattern_number': pattern_number,
            'columns': cols,
            'rows': PATTERN_TOP_ROWS,
            'coordinates': [(cols[0], y) for y in PATTERN_TOP_ROWS] + [(cols[1], y) for y in PATTERN_TOP_ROWS]
        }
        patterns.append(top_pattern)
        pattern_number += 1
        
        bottom_pattern = {
            'pattern_number': pattern_number,
            'columns': cols,
            'rows': PATTERN_BOTTOM_ROWS,
            'coordinates': [(cols[0], y) for y in PATTERN_BOTTOM_ROWS] + [(cols[1], y) for y in PATTERN_BOTTOM_ROWS]
        }
        patterns.append(bottom_pattern)
        pattern_number += 1
    
    return patterns

# ============================================================================
# Matrix Column Information Functions (독립 구현)
# ============================================================================

def find_pattern_matrix_column(pattern_values):
    """
    pattern.json에서 입력된 시퀀스와 완전히 일치하는 패턴의 matrix_column 값을 반환합니다.
    
    Args:
        pattern_values (list): 패턴 시퀀스 리스트, 예시 ['b', 'b', 'b']
    
    Returns:
        str or None: 
            - 'continuous': 패턴이 continuous인 경우
            - 'non_continuous': 패턴이 non_continuous인 경우
            - None: 패턴을 찾지 못한 경우
    """
    try:
        with open('pattern.json', 'r') as f:
            pattern_data = json.load(f)
        pattern_values = [v.lower() for v in pattern_values if v]
        for group_name in ['groupA', 'groupB']:
            patterns = pattern_data['patterns'][group_name]
            for pattern in patterns:
                if pattern.get('sequence') == pattern_values:
                    characteristics = pattern.get('characteristics', {})
                    return characteristics.get('matrix_column')
        return None
    except Exception as e:
        st.error(f"패턴 matrix_column 검색 중 오류 발생: {str(e)}")
        return None

def extract_pattern_matrix_column_info_from_converted_grid(zones):
    """
    Converted Grid에서 패턴 번호와 matrix_column 정보를 추출하는 독립 함수
    의존성 없이 독립적으로 구현됨
    """
    if not zones:
        return []
    
    results = []
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    for zone in sorted_zones:
        # 패턴 위치 정보 생성 (독립 구현)
        patterns = []
        pattern_number = 1
        for start_col in range(TABLE_WIDTH - PATTERN_WIDTH + 1):
            cols = (start_col, start_col + 1)
            # Top 패턴
            top_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_TOP_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_TOP_ROWS] + [(cols[1], y) for y in PATTERN_TOP_ROWS]
            }
            patterns.append(top_pattern)
            pattern_number += 1
            # Bottom 패턴
            bottom_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_BOTTOM_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_BOTTOM_ROWS] + [(cols[1], y) for y in PATTERN_BOTTOM_ROWS]
            }
            patterns.append(bottom_pattern)
            pattern_number += 1
        
        # Zone 범위 내 패턴 필터링
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            continue
        
        # 패턴 값 추출
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # 패턴 번호와 matrix_column 추출
        zone_result = {
            'zone_range': f"{zone['start_x'] + 1}-{zone['end_x'] + 1}",
            'pattern_numbers': [],
            'matrix_columns': []
        }
        
        for v in pattern_values[:4]:
            if v:
                # 패턴 번호와 matrix_column을 함께 추출 (한 번의 검색으로)
                pattern_values_lower = [x.lower() for x in v]
                pattern_number = None
                matrix_column = None
                try:
                    with open('pattern.json', 'r') as f:
                        pattern_data = json.load(f)
                    for group_name in ['groupA', 'groupB']:
                        patterns_data = pattern_data['patterns'][group_name]
                        for pattern in patterns_data:
                            if pattern.get('sequence') == pattern_values_lower:
                                pattern_number = pattern.get('pattern_number')
                                # 패턴을 찾았을 때 characteristics에서 matrix_column도 함께 추출
                                characteristics = pattern.get('characteristics', {})
                                matrix_column = characteristics.get('matrix_column')
                                break
                        if pattern_number:
                            break
                except Exception as e:
                    pass
                
                # 결과 저장
                zone_result['pattern_numbers'].append(pattern_number if pattern_number else '-')
                
                # matrix_column을 T/F로 변환
                if matrix_column == 'continuous':
                    zone_result['matrix_columns'].append('T')
                elif matrix_column == 'non_continuous':
                    zone_result['matrix_columns'].append('F')
                else:
                    zone_result['matrix_columns'].append('-')
            else:
                zone_result['pattern_numbers'].append('-')
                zone_result['matrix_columns'].append('-')
        
        # 패턴 번호가 하나라도 있으면 결과에 추가
        if any(p != '-' for p in zone_result['pattern_numbers']):
            results.append(zone_result)
    
    return results

def extract_pattern_matrix_column_info_from_reconstructed_grid(zones):
    """
    T-Removed Reconstructed Grid에서 패턴 번호와 matrix_column 정보를 추출하는 독립 함수
    의존성 없이 독립적으로 구현됨
    """
    if not zones:
        return []
    
    results = []
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    for zone in sorted_zones:
        # 패턴 위치 정보 생성 (독립 구현)
        patterns = []
        pattern_number = 1
        for start_col in range(TABLE_WIDTH - PATTERN_WIDTH + 1):
            cols = (start_col, start_col + 1)
            # Top 패턴
            top_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_TOP_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_TOP_ROWS] + [(cols[1], y) for y in PATTERN_TOP_ROWS]
            }
            patterns.append(top_pattern)
            pattern_number += 1
            # Bottom 패턴
            bottom_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_BOTTOM_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_BOTTOM_ROWS] + [(cols[1], y) for y in PATTERN_BOTTOM_ROWS]
            }
            patterns.append(bottom_pattern)
            pattern_number += 1
        
        # Zone 범위 내 패턴 필터링
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            continue
        
        # 패턴 값 추출
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # 패턴 번호와 matrix_column 추출
        zone_result = {
            'zone_range': f"{zone['start_x'] + 1}-{zone['end_x'] + 1}",
            'pattern_numbers': [],
            'matrix_columns': []
        }
        
        for v in pattern_values[:4]:
            if v:
                # 패턴 번호 추출
                pattern_values_lower = [x.lower() for x in v]
                pattern_number = None
                try:
                    with open('pattern.json', 'r') as f:
                        pattern_data = json.load(f)
                    for group_name in ['groupA', 'groupB']:
                        patterns_data = pattern_data['patterns'][group_name]
                        for pattern in patterns_data:
                            if pattern.get('sequence') == pattern_values_lower:
                                pattern_number = pattern.get('pattern_number')
                                break
                        if pattern_number:
                            break
                except Exception as e:
                    pass
                
                # matrix_column 추출
                matrix_column = find_pattern_matrix_column(v)
                
                # 결과 저장
                zone_result['pattern_numbers'].append(pattern_number if pattern_number else '-')
                
                # matrix_column을 T/F로 변환
                if matrix_column == 'continuous':
                    zone_result['matrix_columns'].append('T')
                elif matrix_column == 'non_continuous':
                    zone_result['matrix_columns'].append('F')
                else:
                    zone_result['matrix_columns'].append('-')
            else:
                zone_result['pattern_numbers'].append('-')
                zone_result['matrix_columns'].append('-')
        
        # 패턴 번호가 하나라도 있으면 결과에 추가
        if any(p != '-' for p in zone_result['pattern_numbers']):
            results.append(zone_result)
    
    return results

# ============================================================================
# Matrix Column with Type Functions (독립 구현)
# ============================================================================

def find_pattern_matrix_column_with_type(pattern_values):
    """
    pattern.json에서 입력된 시퀀스와 완전히 일치하는 패턴의 matrix_column과 matrix_type 값을 조회합니다.
    독립적으로 구현된 함수 (기존 find_pattern_matrix_column 참조하지 않음)
    
    Args:
        pattern_values (list): 패턴 시퀀스 리스트, 예시 ['b', 'b', 'b']
    
    Returns:
        str or None: 
            - 'T0', 'T1', 'T2': matrix_column이 continuous이고 matrix_type이 0, 1, 2인 경우
            - 'F0', 'F1', 'F2': matrix_column이 non_continuous이고 matrix_type이 0, 1, 2인 경우
            - None: 패턴을 찾지 못한 경우
    """
    try:
        # matrix_type 필드가 있는 파일 경로 시도 (drive-download-20251215/pattern.json)
        pattern_file_paths = [
            'drive-download-20251215/pattern.json',
            'pattern.json'
        ]
        
        pattern_data = None
        for pattern_path in pattern_file_paths:
            try:
                with open(pattern_path, 'r', encoding='utf-8') as f:
                    pattern_data = json.load(f)
                    # matrix_type 필드가 있는지 확인
                    has_matrix_type = False
                    for group_name in ['groupA', 'groupB']:
                        if pattern_data.get('patterns', {}).get(group_name):
                            first_pattern = pattern_data['patterns'][group_name][0]
                            if 'characteristics' in first_pattern:
                                if 'matrix_type' in first_pattern['characteristics']:
                                    has_matrix_type = True
                                    break
                    if has_matrix_type:
                        break
            except (FileNotFoundError, IOError):
                continue
        
        if not pattern_data:
            st.error("pattern.json 파일을 찾을 수 없습니다.")
            return None
            
        pattern_values = [v.lower() for v in pattern_values if v]
        for group_name in ['groupA', 'groupB']:
            patterns = pattern_data['patterns'][group_name]
            for pattern in patterns:
                if pattern.get('sequence') == pattern_values:
                    characteristics = pattern.get('characteristics', {})
                    matrix_column = characteristics.get('matrix_column')
                    matrix_type = characteristics.get('matrix_type')
                    
                    # matrix_type이 없으면 None 반환 (기본값 0 사용하지 않음)
                    if matrix_type is None:
                        st.warning(f"패턴 {pattern.get('pattern_number')}에 matrix_type 필드가 없습니다.")
                        return None
                    
                    if matrix_column == 'continuous':
                        return f'T{matrix_type}'
                    elif matrix_column == 'non_continuous':
                        return f'F{matrix_type}'
                    else:
                        return None
        return None
    except Exception as e:
        st.error(f"패턴 matrix_column with type 검색 중 오류 발생: {str(e)}")
        return None

def extract_pattern_matrix_column_info_with_type_from_converted_grid(zones):
    """
    Converted Grid에서 패턴 번호와 matrix_column+matrix_type 정보를 추출하는 독립 함수
    기존 extract_pattern_matrix_column_info_from_converted_grid를 복제하여 확장한 버전
    의존성 없이 독립적으로 구현됨
    """
    if not zones:
        return []
    
    results = []
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    for zone in sorted_zones:
        # 패턴 위치 정보 생성 (독립 구현)
        patterns = []
        pattern_number = 1
        for start_col in range(TABLE_WIDTH - PATTERN_WIDTH + 1):
            cols = (start_col, start_col + 1)
            # Top 패턴
            top_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_TOP_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_TOP_ROWS] + [(cols[1], y) for y in PATTERN_TOP_ROWS]
            }
            patterns.append(top_pattern)
            pattern_number += 1
            # Bottom 패턴
            bottom_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_BOTTOM_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_BOTTOM_ROWS] + [(cols[1], y) for y in PATTERN_BOTTOM_ROWS]
            }
            patterns.append(bottom_pattern)
            pattern_number += 1
        
        # Zone 범위 내 패턴 필터링
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            continue
        
        # 패턴 값 추출
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # 패턴 번호와 matrix_column+matrix_type 추출
        zone_result = {
            'zone_range': f"{zone['start_x'] + 1}-{zone['end_x'] + 1}",
            'pattern_numbers': [],
            'matrix_columns_with_type': []
        }
        
        for v in pattern_values[:4]:
            if v:
                # 패턴 번호와 matrix_column+matrix_type을 함께 추출
                pattern_values_lower = [x.lower() for x in v]
                pattern_number = None
                matrix_column_with_type = None
                try:
                    # matrix_type 필드가 있는 파일 경로 시도
                    pattern_file_paths = [
                        'drive-download-20251215/pattern.json',
                        'pattern.json'
                    ]
                    
                    pattern_data = None
                    for pattern_path in pattern_file_paths:
                        try:
                            with open(pattern_path, 'r', encoding='utf-8') as f:
                                pattern_data = json.load(f)
                                # matrix_type 필드가 있는지 확인
                                has_matrix_type = False
                                for group_name_check in ['groupA', 'groupB']:
                                    if pattern_data.get('patterns', {}).get(group_name_check):
                                        first_pattern = pattern_data['patterns'][group_name_check][0]
                                        if 'characteristics' in first_pattern:
                                            if 'matrix_type' in first_pattern['characteristics']:
                                                has_matrix_type = True
                                                break
                                if has_matrix_type:
                                    break
                        except (FileNotFoundError, IOError):
                            continue
                    
                    if pattern_data:
                        for group_name in ['groupA', 'groupB']:
                            patterns_data = pattern_data['patterns'][group_name]
                            for pattern in patterns_data:
                                if pattern.get('sequence') == pattern_values_lower:
                                    pattern_number = pattern.get('pattern_number')
                                    # 패턴을 찾았을 때 characteristics에서 matrix_column과 matrix_type 함께 추출
                                    characteristics = pattern.get('characteristics', {})
                                    matrix_column = characteristics.get('matrix_column')
                                    matrix_type = characteristics.get('matrix_type')
                                    
                                    # matrix_type이 None이면 건너뛰기
                                    if matrix_type is not None:
                                        if matrix_column == 'continuous':
                                            matrix_column_with_type = f'T{matrix_type}'
                                        elif matrix_column == 'non_continuous':
                                            matrix_column_with_type = f'F{matrix_type}'
                                    break
                            if pattern_number:
                                break
                except Exception as e:
                    pass
                
                # 결과 저장
                zone_result['pattern_numbers'].append(pattern_number if pattern_number else '-')
                zone_result['matrix_columns_with_type'].append(matrix_column_with_type if matrix_column_with_type else '-')
            else:
                zone_result['pattern_numbers'].append('-')
                zone_result['matrix_columns_with_type'].append('-')
        
        # 패턴 번호가 하나라도 있으면 결과에 추가
        if any(p != '-' for p in zone_result['pattern_numbers']):
            results.append(zone_result)
    
    return results

def extract_pattern_matrix_column_info_with_type_from_reconstructed_grid(zones):
    """
    T-Removed Reconstructed Grid에서 패턴 번호와 matrix_column+matrix_type 정보를 추출하는 독립 함수
    기존 extract_pattern_matrix_column_info_from_reconstructed_grid를 복제하여 확장한 버전
    의존성 없이 독립적으로 구현됨
    """
    if not zones:
        return []
    
    results = []
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    for zone in sorted_zones:
        # 패턴 위치 정보 생성 (독립 구현)
        patterns = []
        pattern_number = 1
        for start_col in range(TABLE_WIDTH - PATTERN_WIDTH + 1):
            cols = (start_col, start_col + 1)
            # Top 패턴
            top_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_TOP_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_TOP_ROWS] + [(cols[1], y) for y in PATTERN_TOP_ROWS]
            }
            patterns.append(top_pattern)
            pattern_number += 1
            # Bottom 패턴
            bottom_pattern = {
                'pattern_number': pattern_number,
                'columns': cols,
                'rows': PATTERN_BOTTOM_ROWS,
                'coordinates': [(cols[0], y) for y in PATTERN_BOTTOM_ROWS] + [(cols[1], y) for y in PATTERN_BOTTOM_ROWS]
            }
            patterns.append(bottom_pattern)
            pattern_number += 1
        
        # Zone 범위 내 패턴 필터링
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            continue
        
        # 패턴 값 추출
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # 패턴 번호와 matrix_column+matrix_type 추출
        zone_result = {
            'zone_range': f"{zone['start_x'] + 1}-{zone['end_x'] + 1}",
            'pattern_numbers': [],
            'matrix_columns_with_type': []
        }
        
        for v in pattern_values[:4]:
            if v:
                # 패턴 번호 추출
                pattern_values_lower = [x.lower() for x in v]
                pattern_number = None
                matrix_column_with_type = None
                try:
                    # matrix_type 필드가 있는 파일 경로 시도
                    pattern_file_paths = [
                        'drive-download-20251215/pattern.json',
                        'pattern.json'
                    ]
                    
                    pattern_data = None
                    for pattern_path in pattern_file_paths:
                        try:
                            with open(pattern_path, 'r', encoding='utf-8') as f:
                                pattern_data = json.load(f)
                                # matrix_type 필드가 있는지 확인
                                has_matrix_type = False
                                for group_name_check in ['groupA', 'groupB']:
                                    if pattern_data.get('patterns', {}).get(group_name_check):
                                        first_pattern = pattern_data['patterns'][group_name_check][0]
                                        if 'characteristics' in first_pattern:
                                            if 'matrix_type' in first_pattern['characteristics']:
                                                has_matrix_type = True
                                                break
                                if has_matrix_type:
                                    break
                        except (FileNotFoundError, IOError):
                            continue
                    
                    if pattern_data:
                        for group_name in ['groupA', 'groupB']:
                            patterns_data = pattern_data['patterns'][group_name]
                            for pattern in patterns_data:
                                if pattern.get('sequence') == pattern_values_lower:
                                    pattern_number = pattern.get('pattern_number')
                                    # 패턴을 찾았을 때 characteristics에서 matrix_column과 matrix_type 함께 추출
                                    characteristics = pattern.get('characteristics', {})
                                    matrix_column = characteristics.get('matrix_column')
                                    matrix_type = characteristics.get('matrix_type')
                                    
                                    # matrix_type이 None이면 건너뛰기
                                    if matrix_type is not None:
                                        if matrix_column == 'continuous':
                                            matrix_column_with_type = f'T{matrix_type}'
                                        elif matrix_column == 'non_continuous':
                                            matrix_column_with_type = f'F{matrix_type}'
                                    break
                            if pattern_number:
                                break
                except Exception as e:
                    pass
                
                # matrix_column+matrix_type 추출 (독립 함수 사용)
                if not matrix_column_with_type:
                    matrix_column_with_type_value = find_pattern_matrix_column_with_type(v)
                    if matrix_column_with_type_value:
                        matrix_column_with_type = matrix_column_with_type_value
                
                # 결과 저장
                zone_result['pattern_numbers'].append(pattern_number if pattern_number else '-')
                zone_result['matrix_columns_with_type'].append(matrix_column_with_type if matrix_column_with_type else '-')
            else:
                zone_result['pattern_numbers'].append('-')
                zone_result['matrix_columns_with_type'].append('-')
        
        # 패턴 번호가 하나라도 있으면 결과에 추가
        if any(p != '-' for p in zone_result['pattern_numbers']):
            results.append(zone_result)
    
    return results

def compare_matrix_column_with_type(converted_str, reconstructed_str):
    """
    matrix_column with type 값을 비교하는 함수
    기존 비교 (T/T/F)와 확장 비교 (T0/T0/F1 vs T0/T0/F2)를 모두 수행
    그룹 전체(4개 패턴) 비교도 수행
    
    Args:
        converted_str: Converted Grid의 matrix_column with type 문자열 (예: "T0T0F1T2")
        reconstructed_str: Reconstructed Grid의 matrix_column with type 문자열 (예: "T0T0F2T1")
    
    Returns:
        tuple: (basic_match, detailed_match, full_group_match, match_status, full_group_match_status)
            - basic_match: 기존 방식 비교 결과 (T/F만 비교, 앞 3글자)
            - detailed_match: 확장 방식 비교 결과 (앞 3개 패턴, 6글자)
            - full_group_match: 그룹 전체 비교 결과 (4개 패턴 전체)
            - match_status: 앞 3개 패턴 비교의 매치 상태 HTML 문자열
            - full_group_match_status: 그룹 전체 비교의 매치 상태 HTML 문자열
    """
    if converted_str == '-' or reconstructed_str == '-':
        na_status = '<span style="background-color: #D3D3D3; padding: 2px 6px; border-radius: 3px; font-weight: bold;">N/A</span>'
        return False, False, False, na_status, na_status
    
    # 기존 방식: T/F만 추출하여 비교 (앞 3글자)
    c_basic = ''.join([c for c in converted_str if c in ['T', 'F']])[:3]
    r_basic = ''.join([c for c in reconstructed_str if c in ['T', 'F']])[:3]
    basic_match = (c_basic == r_basic)
    
    # 확장 방식: 앞 3개 패턴의 전체 값 비교 (각 2글자씩, 총 6글자)
    c_detailed = converted_str[:6] if len(converted_str) >= 6 else converted_str
    r_detailed = reconstructed_str[:6] if len(reconstructed_str) >= 6 else reconstructed_str
    detailed_match = (c_detailed == r_detailed)
    
    # 그룹 전체 비교: 기본 방식처럼 T/F만 추출하여 4개 문자 비교
    # 전체 문자열에서 T/F만 추출 (예: "T0T0F1T2" → "TTFT")
    c_full_basic = ''.join([c for c in converted_str if c in ['T', 'F']])[:4]
    r_full_basic = ''.join([c for c in reconstructed_str if c in ['T', 'F']])[:4]
    full_group_match = (c_full_basic == r_full_basic)
    
    # 앞 3개 패턴 비교의 매치 상태 결정
    if detailed_match:
        match_status = '<span style="background-color: #90EE90; padding: 2px 6px; border-radius: 3px; font-weight: bold;">MATCH</span>'
    elif basic_match:
        # 기존 방식에서는 match이지만 확장 방식에서는 unmatch
        match_status = '<span style="background-color: #FFD700; padding: 2px 6px; border-radius: 3px; font-weight: bold;">PARTIAL</span>'
    else:
        match_status = '<span style="background-color: #FFB6C1; padding: 2px 6px; border-radius: 3px; font-weight: bold;">UNMATCH</span>'
    
    # 그룹 전체 비교의 매치 상태 결정 (기본 방식처럼 T/F만 비교)
    if full_group_match:
        # 4개 문자가 모두 일치하면 MATCH
        full_group_match_status = '<span style="background-color: #90EE90; padding: 2px 6px; border-radius: 3px; font-weight: bold;">MATCH</span>'
    else:
        # 4개 문자가 일치하지 않으면 UNMATCH
        full_group_match_status = '<span style="background-color: #FFB6C1; padding: 2px 6px; border-radius: 3px; font-weight: bold;">UNMATCH</span>'
    
    return basic_match, detailed_match, full_group_match, match_status, full_group_match_status

def display_grid_matrix_summary(converted_results):
    """
    Converted Grid와 T-Removed Reconstructed Grid의 matrix_column 정보를 한 줄로 표시하는 독립 함수
    C와 R의 그룹 1만 비교하여 match/unmatch 표시
    기존 비교와 확장 비교(matrix_type 포함)를 모두 표시
    """
    st.markdown("#### Grid Matrix Summary")
    
    # 1. Converted Grid Matrix Column 정보 수집 (그룹 1만) - 기존 방식
    converted_matrix_str = '-'
    if converted_results and len(converted_results) > 0:
        first_result = converted_results[0]
        matrix_columns = first_result.get('matrix_columns', [])
        if matrix_columns:
            # 전체 matrix_column 값을 문자열로 연결 (예: "FTFT")
            converted_matrix_str = ''.join(matrix_columns)
    
    # 2. T-Removed Reconstructed Grid Matrix Column 정보 수집 (그룹 1만) - 기존 방식
    reconstructed_matrix_str = '-'
    if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
        reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
        if reconstructed_zones:
            reconstructed_results = extract_pattern_matrix_column_info_from_reconstructed_grid(reconstructed_zones)
            if reconstructed_results and len(reconstructed_results) > 0:
                first_result = reconstructed_results[0]
                matrix_columns = first_result.get('matrix_columns', [])
                if matrix_columns:
                    reconstructed_matrix_str = ''.join(matrix_columns)
    
    # 3. 기존 방식 비교 (T/F만 비교, 앞 3글자)
    match_status_basic = ""
    if converted_matrix_str != '-' and reconstructed_matrix_str != '-':
        # 앞 3글자 비교
        c_first_3 = converted_matrix_str[:3] if len(converted_matrix_str) >= 3 else converted_matrix_str
        r_first_3 = reconstructed_matrix_str[:3] if len(reconstructed_matrix_str) >= 3 else reconstructed_matrix_str
        
        if c_first_3 == r_first_3:
            match_status_basic = '<span style="background-color: #90EE90; padding: 2px 6px; border-radius: 3px; font-weight: bold;">MATCH</span>'
        else:
            match_status_basic = '<span style="background-color: #FFB6C1; padding: 2px 6px; border-radius: 3px; font-weight: bold;">UNMATCH</span>'
    elif converted_matrix_str != '-' or reconstructed_matrix_str != '-':
        match_status_basic = '<span style="background-color: #D3D3D3; padding: 2px 6px; border-radius: 3px; font-weight: bold;">N/A</span>'
    
    # 4. 확장 방식: Matrix Column with Type 정보 수집 (그룹 1만)
    converted_matrix_with_type_str = '-'
    if converted_results and len(converted_results) > 0:
        # 확장 버전 함수로 정보 추출
        # zones를 다시 생성 (convert_results가 있으므로 converted_grid가 존재함)
        if st.session_state.show_grid and st.session_state.converted_grid is not None:
            # divide_grid_into_overlapping_zones 함수 복제 (독립 구현)
            converted_zones_for_type = []
            grid_for_zones = st.session_state.converted_grid
            zone_width = 3
            for start_x in range(15 - zone_width + 1):
                end_x = start_x + zone_width
                zone_data = [[grid_for_zones[x][y] for y in range(6)] for x in range(start_x, end_x)]
                if any(cell in {'b', 't', 'p'} for column in zone_data for cell in column):
                    converted_zones_for_type.append({
                        'zone_data': zone_data,
                        'start_x': start_x,
                        'end_x': end_x - 1
                    })
            
            if converted_zones_for_type:
                converted_results_with_type = extract_pattern_matrix_column_info_with_type_from_converted_grid(converted_zones_for_type)
                if converted_results_with_type and len(converted_results_with_type) > 0:
                    first_result_with_type = converted_results_with_type[0]
                    matrix_columns_with_type = first_result_with_type.get('matrix_columns_with_type', [])
                    if matrix_columns_with_type:
                        converted_matrix_with_type_str = ''.join(matrix_columns_with_type)
    
    reconstructed_matrix_with_type_str = '-'
    if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
        reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
        if reconstructed_zones:
            reconstructed_results_with_type = extract_pattern_matrix_column_info_with_type_from_reconstructed_grid(reconstructed_zones)
            if reconstructed_results_with_type and len(reconstructed_results_with_type) > 0:
                first_result_with_type = reconstructed_results_with_type[0]
                matrix_columns_with_type = first_result_with_type.get('matrix_columns_with_type', [])
                if matrix_columns_with_type:
                    reconstructed_matrix_with_type_str = ''.join(matrix_columns_with_type)
    
    # 5. 확장 방식 비교 (T0/T0/F1 vs T0/T0/F2 등 세분화 비교)
    basic_match, detailed_match, full_group_match, match_status_detailed, full_group_match_status = compare_matrix_column_with_type(
        converted_matrix_with_type_str, 
        reconstructed_matrix_with_type_str
    )
    
    # 6. 기존 방식 표시 (그룹 1만)
    st.markdown("**기존 방식 (T/F만 비교):**")
    st.markdown(f"**C:** {converted_matrix_str} | **R:** {reconstructed_matrix_str} | {match_status_basic}", unsafe_allow_html=True)
    
    # 7. 확장 방식 표시 (그룹 1만, matrix_type 포함, 앞 3개 패턴 비교)
    st.markdown("**확장 방식 - 앞 3개 패턴 (matrix_type 포함):**")
    st.markdown(f"**C:** {converted_matrix_with_type_str[:6] if len(converted_matrix_with_type_str) >= 6 else converted_matrix_with_type_str} | **R:** {reconstructed_matrix_with_type_str[:6] if len(reconstructed_matrix_with_type_str) >= 6 else reconstructed_matrix_with_type_str} | {match_status_detailed}", unsafe_allow_html=True)
    
    # 8. 확장 방식 - 그룹 전체 비교 표시 (4개 패턴 전체)
    st.markdown("**확장 방식 - 그룹 전체 (4개 패턴, matrix_type 포함):**")
    st.markdown(f"**C:** {converted_matrix_with_type_str} | **R:** {reconstructed_matrix_with_type_str} | {full_group_match_status}", unsafe_allow_html=True)
    
    st.markdown("---")

def display_matrix_column_info():
    """
    Converted Grid의 matrix_column 정보와 Sequence Prediction Results를 표시
    오른쪽 영역 최상단에 표시되도록 설계됨
    그룹별로 Pattern 1-2와 Pattern 2-3의 matrix_column 값과 Sequence Prediction Results를 추출하여 표시
    각 Matrix 조합(FF, FT, TF, TT)에 대해 첫 번째로 추출된 Sequence 값을 고정 저장
    """
    st.markdown("### Matrix Column Information")
    
    # Matrix Sequence Mapping 초기화 (없으면)
    if 'matrix_sequence_mapping' not in st.session_state:
        st.session_state.matrix_sequence_mapping = {}
    
    # Matrix Sequence 추출 순서 추적 초기화 (없으면)
    if 'matrix_sequence_order' not in st.session_state:
        st.session_state.matrix_sequence_order = {}
    if 'matrix_sequence_order_counter' not in st.session_state:
        st.session_state.matrix_sequence_order_counter = 0
    
    # Converted Grid 정보 추출 및 표시
    if st.session_state.show_grid and st.session_state.converted_grid is not None:
        zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
        if zones:
            converted_results = extract_pattern_matrix_column_info_from_converted_grid(zones)
            if converted_results:
                # Zone을 zone_range와 매칭하기 위해 딕셔너리 생성
                zones_dict = {}
                sorted_zones = sorted(zones, key=lambda x: x['start_x'])
                for zone in sorted_zones:
                    zone_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
                    zones_dict[zone_range] = zone
                
                # 먼저 모든 데이터를 수집하여 Sequence 값 저장
                flag_triggered_zone = None
                for result in converted_results:
                    matrix_columns = result['matrix_columns']
                    zone_range = result['zone_range']
                    
                    # Pattern 1-2와 Pattern 2-3 값 추출
                    pattern1_2_matrix = ''
                    pattern2_3_matrix = ''
                    
                    if len(matrix_columns) >= 2:
                        # Pattern 1,2 조합 (첫 번째와 두 번째)
                        if matrix_columns[0] != '-' and matrix_columns[1] != '-':
                            pattern1_2_matrix = matrix_columns[0] + matrix_columns[1]
                    
                    if len(matrix_columns) >= 3:
                        # Pattern 2,3 조합 (두 번째와 세 번째)
                        if matrix_columns[1] != '-' and matrix_columns[2] != '-':
                            pattern2_3_matrix = matrix_columns[1] + matrix_columns[2]
                    
                    # Sequence Prediction Results 추출 및 저장
                    if zone_range in zones_dict:
                        zone = zones_dict[zone_range]
                        # get_zone_pattern_sequence_results를 사용하여 Sequence Prediction Results 추출
                        (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
                         sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
                         pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf) = get_zone_pattern_sequence_results(zone)
                        
                        # Pattern 1,2의 Sequence Prediction Results 저장 (첫 번째 값만)
                        new_value_saved = False
                        if pattern1_2_matrix and pattern1_comparison in ['W', 'L']:
                            key_1_2 = f"pattern1_2_{pattern1_2_matrix}"
                            if key_1_2 not in st.session_state.matrix_sequence_mapping:
                                # 첫 번째 값으로 저장
                                st.session_state.matrix_sequence_mapping[key_1_2] = pattern1_comparison
                                # 순서 정보 저장
                                st.session_state.matrix_sequence_order_counter += 1
                                st.session_state.matrix_sequence_order[key_1_2] = {
                                    'order': st.session_state.matrix_sequence_order_counter,
                                    'zone': zone_range,
                                    'matrix': pattern1_2_matrix
                                }
                                new_value_saved = True
                        
                        # Pattern 2,3의 Sequence Prediction Results 저장 (첫 번째 값만)
                        if pattern2_3_matrix and pattern2_comparison in ['W', 'L']:
                            key_2_3 = f"pattern2_3_{pattern2_3_matrix}"
                            if key_2_3 not in st.session_state.matrix_sequence_mapping:
                                # 첫 번째 값으로 저장
                                st.session_state.matrix_sequence_mapping[key_2_3] = pattern2_comparison
                                # 순서 정보 저장
                                st.session_state.matrix_sequence_order_counter += 1
                                st.session_state.matrix_sequence_order[key_2_3] = {
                                    'order': st.session_state.matrix_sequence_order_counter,
                                    'zone': zone_range,
                                    'matrix': pattern2_3_matrix
                                }
                                new_value_saved = True
                        
                        # 새로운 값이 저장된 경우에만 플래그 조건 확인
                        if new_value_saved and (flag_triggered_zone is None):
                            # 조건 1: FF의 Pattern 1,2 Sequence와 Pattern 2,3 Sequence가 모두 채워짐
                            ff_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_FF', '-')
                            ff_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_FF', '-')
                            ff_complete = (ff_p1_2 != '-' and ff_p2_3 != '-')
                            
                            # 조건 2: FT, TF, TT 중에서 Pattern 1,2 Sequence가 하나라도 있고, Pattern 2,3 Sequence가 하나라도 있으면 충족
                            ft_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_FT', '-')
                            ft_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_FT', '-')
                            
                            tf_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_TF', '-')
                            tf_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_TF', '-')
                            
                            tt_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_TT', '-')
                            tt_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_TT', '-')
                            
                            # Pattern 1,2 Sequence가 하나라도 있는지 확인
                            has_p1_2 = (ft_p1_2 != '-' or tf_p1_2 != '-' or tt_p1_2 != '-')
                            # Pattern 2,3 Sequence가 하나라도 있는지 확인
                            has_p2_3 = (ft_p2_3 != '-' or tf_p2_3 != '-' or tt_p2_3 != '-')
                            
                            # 두 값이 각각 하나씩 있으면 조건 충족
                            other_complete = (has_p1_2 and has_p2_3)
                            
                            # 어떤 조합에서 값이 나왔는지 확인 (표시용)
                            other_combo_p1_2 = 'FT' if ft_p1_2 != '-' else ('TF' if tf_p1_2 != '-' else ('TT' if tt_p1_2 != '-' else ''))
                            other_combo_p2_3 = 'FT' if ft_p2_3 != '-' else ('TF' if tf_p2_3 != '-' else ('TT' if tt_p2_3 != '-' else ''))
                            other_p1_2_value = ft_p1_2 if ft_p1_2 != '-' else (tf_p1_2 if tf_p1_2 != '-' else (tt_p1_2 if tt_p1_2 != '-' else '-'))
                            other_p2_3_value = ft_p2_3 if ft_p2_3 != '-' else (tf_p2_3 if tf_p2_3 != '-' else (tt_p2_3 if tt_p2_3 != '-' else '-'))
                            
                            # 두 조건이 모두 충족되면 플래그 설정
                            if ff_complete and other_complete:
                                # 플래그가 아직 설정되지 않은 경우에만 설정
                                if 'matrix_flag_info' not in st.session_state or not st.session_state.matrix_flag_info.get('triggered', False):
                                    flag_triggered_zone = zone_range
                                    st.session_state.matrix_flag_info = {
                                        'triggered': True,
                                        'zone': zone_range,
                                        'ff_p1_2': ff_p1_2,
                                        'ff_p2_3': ff_p2_3,
                                        'other_combo_p1_2': other_combo_p1_2,
                                        'other_combo_p2_3': other_combo_p2_3,
                                        'other_p1_2': other_p1_2_value,
                                        'other_p2_3': other_p2_3_value
                                    }
                
                # Grid Matrix Summary 표시 (독립 함수 호출)
                display_grid_matrix_summary(converted_results)
                
                # 저장된 Matrix 조합별 첫 Sequence 값 테이블 표시
                st.markdown("#### Matrix Combination Sequence Mapping Table")
                
                # Matrix 조합 리스트
                matrix_combinations = ['FF', 'FT', 'TF', 'TT']
                
                # 테이블 데이터 생성
                mapping_table_data = []
                for matrix_combo in matrix_combinations:
                    pattern1_2_key = f"pattern1_2_{matrix_combo}"
                    pattern2_3_key = f"pattern2_3_{matrix_combo}"
                    
                    pattern1_2_sequence = st.session_state.matrix_sequence_mapping.get(pattern1_2_key, '-')
                    pattern2_3_sequence = st.session_state.matrix_sequence_mapping.get(pattern2_3_key, '-')
                    
                    # 순서 정보 가져오기
                    order_info_1_2 = st.session_state.matrix_sequence_order.get(pattern1_2_key, {})
                    order_info_2_3 = st.session_state.matrix_sequence_order.get(pattern2_3_key, {})
                    
                    order_1_2 = order_info_1_2.get('order', None) if pattern1_2_sequence != '-' else None
                    order_2_3 = order_info_2_3.get('order', None) if pattern2_3_sequence != '-' else None
                    
                    # 순서 표시 문자열 생성
                    order_display_1_2 = f"#{order_1_2}" if order_1_2 else '-'
                    order_display_2_3 = f"#{order_2_3}" if order_2_3 else '-'
                    
                    mapping_table_data.append({
                        'Matrix Combination': matrix_combo,
                        'Pattern 1,2 Sequence': f"{pattern1_2_sequence} ({order_display_1_2})" if pattern1_2_sequence != '-' else '-',
                        'Pattern 2,3 Sequence': f"{pattern2_3_sequence} ({order_display_2_3})" if pattern2_3_sequence != '-' else '-'
                    })
                
                # 테이블로 표시
                if mapping_table_data:
                    mapping_df = pd.DataFrame(mapping_table_data)
                    st.table(mapping_df)
                
                # 플래그 조건 재확인 (모든 데이터 수집 후)
                # 조건 1: FF의 Pattern 1,2 Sequence와 Pattern 2,3 Sequence가 모두 채워짐
                ff_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_FF', '-')
                ff_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_FF', '-')
                ff_complete = (ff_p1_2 != '-' and ff_p2_3 != '-')
                
                # 조건 2: FT, TF, TT 중에서 Pattern 1,2 Sequence가 하나라도 있고, Pattern 2,3 Sequence가 하나라도 있으면 충족
                ft_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_FT', '-')
                ft_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_FT', '-')
                
                tf_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_TF', '-')
                tf_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_TF', '-')
                
                tt_p1_2 = st.session_state.matrix_sequence_mapping.get('pattern1_2_TT', '-')
                tt_p2_3 = st.session_state.matrix_sequence_mapping.get('pattern2_3_TT', '-')
                
                # Pattern 1,2 Sequence가 하나라도 있는지 확인
                has_p1_2 = (ft_p1_2 != '-' or tf_p1_2 != '-' or tt_p1_2 != '-')
                # Pattern 2,3 Sequence가 하나라도 있는지 확인
                has_p2_3 = (ft_p2_3 != '-' or tf_p2_3 != '-' or tt_p2_3 != '-')
                
                # 두 값이 각각 하나씩 있으면 조건 충족
                other_complete = (has_p1_2 and has_p2_3)
                
                # 어떤 조합에서 값이 나왔는지 확인 (표시용)
                other_combo_p1_2 = 'FT' if ft_p1_2 != '-' else ('TF' if tf_p1_2 != '-' else ('TT' if tt_p1_2 != '-' else ''))
                other_combo_p2_3 = 'FT' if ft_p2_3 != '-' else ('TF' if tf_p2_3 != '-' else ('TT' if tt_p2_3 != '-' else ''))
                other_p1_2_value = ft_p1_2 if ft_p1_2 != '-' else (tf_p1_2 if tf_p1_2 != '-' else (tt_p1_2 if tt_p1_2 != '-' else '-'))
                other_p2_3_value = ft_p2_3 if ft_p2_3 != '-' else (tf_p2_3 if tf_p2_3 != '-' else (tt_p2_3 if tt_p2_3 != '-' else '-'))
                
                # 두 조건이 모두 충족되면 플래그 설정
                if ff_complete and other_complete:
                    # 플래그가 아직 설정되지 않았거나, 현재 그룹에서 새로 충족된 경우
                    if 'matrix_flag_info' not in st.session_state or not st.session_state.matrix_flag_info.get('triggered', False):
                        # 마지막 그룹을 플래그 위치로 설정
                        flag_zone = converted_results[-1]['zone_range'] if converted_results else 'Unknown'
                        st.session_state.matrix_flag_info = {
                            'triggered': True,
                            'zone': flag_zone,
                            'ff_p1_2': ff_p1_2,
                            'ff_p2_3': ff_p2_3,
                            'other_combo_p1_2': other_combo_p1_2,
                            'other_combo_p2_3': other_combo_p2_3,
                            'other_p1_2': other_p1_2_value,
                            'other_p2_3': other_p2_3_value
                        }
                
                # 플래그 정보 표시
                if 'matrix_flag_info' in st.session_state and st.session_state.matrix_flag_info.get('triggered', False):
                    flag_info = st.session_state.matrix_flag_info
                    st.markdown("#### 🚩 Flag Triggered")
                    
                    # Converted Grid 기준 그룹
                    converted_group = flag_info['zone']
                    
                    # 상세 정보 계산
                    original_grid = st.session_state.grid if st.session_state.grid else None
                    if original_grid:
                        detailed_info = get_detailed_flag_info(converted_group, original_grid)
                        
                        if detailed_info:
                            # Converted Grid 상세 정보
                            st.markdown("##### Converted Grid")
                            st.text(f"그룹 범위: Group {converted_group}")
                            st.text(f"셀 인덱스: {detailed_info['converted_cell_index']}")
                            st.text(f"위치: 열 {detailed_info['converted_col']}, 행 {detailed_info['converted_row']}")
                            
                            # T 개수 정보
                            st.markdown("##### T 변환 정보")
                            st.text(f"T 개수 (셀 인덱스 {detailed_info['converted_cell_index']} 이전): {detailed_info['t_count']}개")
                            if detailed_info.get('t_count_debug'):
                                st.text(f"T 위치: {', '.join(detailed_info['t_count_debug'][:10])}" + ("..." if len(detailed_info['t_count_debug']) > 10 else ""))
                            
                            # T-Removed Reconstructed Grid 상세 정보
                            st.markdown("##### T-Removed Reconstructed Grid")
                            st.text(f"그룹 범위: Group {detailed_info['reconstructed_group']}")
                            st.text(f"셀 인덱스: {detailed_info['reconstructed_cell_index']}")
                            st.text(f"위치: 열 {detailed_info['reconstructed_col']}, 행 {detailed_info['reconstructed_row']}")
                        else:
                            # 상세 정보를 계산할 수 없는 경우 기존 방식 사용
                            st.success(f"**조건 충족 시점 (Converted Grid): Group {converted_group}**")
                            reconstructed_group = convert_flag_group_to_reconstructed_group(converted_group, original_grid)
                            st.success(f"**조건 충족 시점 (T-Removed Reconstructed Grid): Group {reconstructed_group}**")
                    else:
                        st.success(f"**조건 충족 시점 (Converted Grid): Group {converted_group}**")
                        
                        # T-Removed Reconstructed Grid에서 해당 그룹 정보 추출
                        if detailed_info and hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
                            reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
                            if reconstructed_zones:
                                # reconstructed_group 파싱 (예: "2-4" -> start=1, end=3)
                                reconstructed_group = detailed_info['reconstructed_group']
                                parts = reconstructed_group.split('-')
                                if len(parts) == 2:
                                    recon_start = int(parts[0]) - 1
                                    recon_end = int(parts[1]) - 1
                                    
                                    # 해당 그룹 찾기
                                    for recon_zone in reconstructed_zones:
                                        if recon_zone['start_x'] == recon_start and recon_zone['end_x'] == recon_end:
                                            st.markdown("##### T-Removed Reconstructed Grid 해당 그룹 정보")
                                            # 그룹의 패턴 정보 추출
                                            patterns = get_pattern_positions()
                                            group_patterns = [p for p in patterns if p['columns'][0] >= recon_zone['start_x'] and p['columns'][1] <= recon_zone['end_x']]
                                            
                                            if len(group_patterns) >= 4:
                                                pattern_values = []
                                                for pattern in group_patterns[:4]:
                                                    values = []
                                                    for x, y in pattern['coordinates']:
                                                        relative_x = x - recon_zone['start_x']
                                                        value = recon_zone['zone_data'][relative_x][y]
                                                        if value:
                                                            values.append(value.upper())
                                                    pattern_values.append(values)
                                                
                                                # 패턴 번호 추출
                                                pattern_numbers = []
                                                for v in pattern_values[:4]:
                                                    pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
                                                    pattern_numbers.append(pattern_number if pattern_number is not None else '-')
                                                
                                                st.text(f"Pattern 1 Number: {pattern_numbers[0]}")
                                                st.text(f"Pattern 2 Number: {pattern_numbers[1] if len(pattern_numbers) > 1 else '-'}")
                                                st.text(f"Pattern 3 Number: {pattern_numbers[2] if len(pattern_numbers) > 2 else '-'}")
                                                st.text(f"Pattern 4 Number: {pattern_numbers[3] if len(pattern_numbers) > 3 else '-'}")
                                            break
                    
                    st.text(f"FF - Pattern 1,2 Sequence: {flag_info['ff_p1_2']}, Pattern 2,3 Sequence: {flag_info['ff_p2_3']}")
                    st.text(f"{flag_info['other_combo_p1_2']} - Pattern 1,2 Sequence: {flag_info['other_p1_2']}")
                    st.text(f"{flag_info['other_combo_p2_3']} - Pattern 2,3 Sequence: {flag_info['other_p2_3']}")
                else:
                    # 디버깅 정보 표시 (조건이 충족되지 않은 경우)
                    st.markdown("#### 🔍 Flag Status (Debug)")
                    st.text(f"FF Complete: {ff_complete} (P1,2: {ff_p1_2}, P2,3: {ff_p2_3})")
                    st.text(f"FT - P1,2: {ft_p1_2}, P2,3: {ft_p2_3}")
                    st.text(f"TF - P1,2: {tf_p1_2}, P2,3: {tf_p2_3}")
                    st.text(f"TT - P1,2: {tt_p1_2}, P2,3: {tt_p2_3}")
                    st.text(f"Has P1,2: {has_p1_2}, Has P2,3: {has_p2_3}")
                    st.text(f"Other Complete: {other_complete}")
                    st.text(f"Flag Triggered: {ff_complete and other_complete}")
                    # session_state 확인
                    if 'matrix_flag_info' in st.session_state:
                        st.text(f"Flag Info Exists: {st.session_state.matrix_flag_info}")
                    else:
                        st.text("Flag Info: Not set")
                
                # Converted Grid Matrix Column 텍스트 형식 표시 (하단)
                st.markdown("#### Converted Grid Matrix Column")
                
                for result in converted_results:
                    matrix_columns = result['matrix_columns']
                    pattern_numbers = result.get('pattern_numbers', [])
                    zone_range = result['zone_range']
                    
                    # Pattern 1-2와 Pattern 2-3 값 추출
                    pattern1_2_matrix = ''
                    pattern2_3_matrix = ''
                    
                    if len(matrix_columns) >= 2:
                        # Pattern 1,2 조합 (첫 번째와 두 번째)
                        if matrix_columns[0] != '-' and matrix_columns[1] != '-':
                            pattern1_2_matrix = matrix_columns[0] + matrix_columns[1]
                    
                    if len(matrix_columns) >= 3:
                        # Pattern 2,3 조합 (두 번째와 세 번째)
                        if matrix_columns[1] != '-' and matrix_columns[2] != '-':
                            pattern2_3_matrix = matrix_columns[1] + matrix_columns[2]
                    
                    # 저장된 Sequence 값 가져오기
                    pattern1_2_sequence = ''
                    pattern2_3_sequence = ''
                    
                    if pattern1_2_matrix:
                        key_1_2 = f"pattern1_2_{pattern1_2_matrix}"
                        if key_1_2 in st.session_state.matrix_sequence_mapping:
                            pattern1_2_sequence = st.session_state.matrix_sequence_mapping[key_1_2]
                    
                    if pattern2_3_matrix:
                        key_2_3 = f"pattern2_3_{pattern2_3_matrix}"
                        if key_2_3 in st.session_state.matrix_sequence_mapping:
                            pattern2_3_sequence = st.session_state.matrix_sequence_mapping[key_2_3]
                    
                    # 전체 matrix_column 값을 문자열로 연결 (예: "FTFT")
                    matrix_col_str = ''.join(matrix_columns)
                    
                    # 패턴 번호 표시
                    pattern_nums_str = ', '.join([f"P{i+1}:{pattern_numbers[i] if i < len(pattern_numbers) else '-'}" for i in range(4)])
                    st.text(f"Group {zone_range}: {matrix_col_str} ({pattern_nums_str})")
                    
                    # Pattern 1-2와 Pattern 2-3 표시 (Matrix Column)
                    if pattern1_2_matrix:
                        st.text(f"  Pattern 1,2 Matrix: {pattern1_2_matrix}")
                    if pattern2_3_matrix:
                        st.text(f"  Pattern 2,3 Matrix: {pattern2_3_matrix}")
                    
                    # Pattern 1-2와 Pattern 2-3 표시 (Sequence Prediction Results - 저장된 값)
                    if pattern1_2_sequence:
                        st.text(f"  Pattern 1,2 Sequence: {pattern1_2_sequence}")
                    if pattern2_3_sequence:
                        st.text(f"  Pattern 2,3 Sequence: {pattern2_3_sequence}")
                
                # 저장된 매핑 정보 표시 (선택사항 - 디버깅용)
                # st.markdown("#### Matrix Sequence Mapping")
                # st.json(st.session_state.matrix_sequence_mapping)
    
    st.markdown("---")

def divide_grid_into_overlapping_zones(grid, zone_width=3):
    zones = []
    for start_x in range(15 - zone_width + 1):
        end_x = start_x + zone_width
        zone_data = [[grid[x][y] for y in range(6)] for x in range(start_x, end_x)]
        if any(cell in {'b', 't', 'p'} for column in zone_data for cell in column):
            zones.append({
                'zone_data': zone_data,
                'start_x': start_x,
                'end_x': end_x - 1
            })
    return zones

def find_pattern_group(pattern_values):
    try:
        with open('pattern.json', 'r') as f:
            pattern_data = json.load(f)
        
        pattern_values = [v.lower() for v in pattern_values if v]
        
        for group_name in ['groupA', 'groupB']:
            patterns = pattern_data['patterns'][group_name]
            for pattern in patterns:
                if pattern.get('sequence') == pattern_values:
                    return pattern.get('group', group_name[5].lower())
        
        return None
    except Exception as e:
        st.error(f"패턴 그룹 검색 중 오류 발생: {str(e)}")
        return None

def get_first_two_group_values(zone):
    patterns = get_pattern_positions()
    group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
    
    if len(group_patterns) < 4:
        return ''
        
    pattern_values = []
    for pattern in group_patterns[:4]:
        values = []
        for x, y in pattern['coordinates']:
            relative_x = x - zone['start_x']
            value = zone['zone_data'][relative_x][y]
            if value:
                values.append(value.upper())
        pattern_values.append(values)
        
    groups_123 = []
    pattern_123_valid = True
    
    if len(pattern_values) >= 3:
        for i in range(3):
            if not pattern_values[i]:
                pattern_123_valid = False
                break
            group = find_pattern_group(pattern_values[i])
            if group is None:
                pattern_123_valid = False
                break
            groups_123.append(group)
    
    pattern_123_text = ''.join(groups_123) if pattern_123_valid and len(groups_123) == 3 else ''
    return pattern_123_text[:2] if len(pattern_123_text) >= 2 else ''

def find_pattern_number_only(pattern_values):
    """
    pattern.json에서 입력된 시퀀스와 완전히 일치하는 패턴의 넘버만 반환합니다.
    Args:
        pattern_values (list): 예시 ['b', 'b', 'b']
    Returns:
        str or None: 패턴 넘버(예: '144047'), 없으면 None
    """
    try:
        with open('pattern.json', 'r') as f:
            pattern_data = json.load(f)
        pattern_values = [v.lower() for v in pattern_values if v]
        for group_name in ['groupA', 'groupB']:
            patterns = pattern_data['patterns'][group_name]
            for pattern in patterns:
                if pattern.get('sequence') == pattern_values:
                    return pattern.get('pattern_number')
        return None
    except Exception as e:
        st.error(f"패턴 넘버 검색 중 오류 발생: {str(e)}")
        return None

def process_pattern_numbers(pattern_numbers):
    """
    그룹 내 패턴별 넘버 리스트를 받아 아래와 같이 가공하여 반환합니다.
    Args:
        pattern_numbers (list): [패턴1넘버, 패턴2넘버, 패턴3넘버, 패턴4넘버]
    Returns:
        dict: pattern1_number, result1_number, pattern2_number, result2_number
    """
    # None 또는 '-' 처리
    n1 = pattern_numbers[0] if len(pattern_numbers) > 0 and pattern_numbers[0] not in [None, '-'] else ''
    n2 = pattern_numbers[1] if len(pattern_numbers) > 1 and pattern_numbers[1] not in [None, '-'] else ''
    n3 = pattern_numbers[2] if len(pattern_numbers) > 2 and pattern_numbers[2] not in [None, '-'] else ''
    n4 = pattern_numbers[3] if len(pattern_numbers) > 3 and pattern_numbers[3] not in [None, '-'] else ''
    return {
        'pattern1_number': n1 + n2,
        'result1_number': n3,
        'pattern2_number': n1 + n2 + n3,
        'result2_number': n4
    }

def display_pattern_groups(zones):
    if not zones:
        return
    
    st.markdown("### Pattern Group Analysis")
    
    # Display all groups' first 2 values concatenated
    all_first_two = ''
    for zone in zones:
        first_two = get_first_two_group_values(zone)
        if first_two:
            all_first_two += first_two
    
    if all_first_two:
        st.text(f"All groups' first 2 values: {all_first_two}")
        st.markdown("---")
    
    # Sort zones by start_x to display in order
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    for zone in sorted_zones:
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            continue
            
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
            
        # 각 패턴별 넘버 리스트
        pattern_numbers = []
        for v in pattern_values[:4]:
            pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
            pattern_numbers.append(pattern_number if pattern_number is not None else '-')
        
        # 넘버 가공
        numbers_dict = process_pattern_numbers(pattern_numbers)
        
        groups_123 = []
        groups_1234 = []
        pattern_123_valid = True
        if len(pattern_values) >= 3:
            for i in range(3):
                if not pattern_values[i]:
                    pattern_123_valid = False
                    break
                group = find_pattern_group(pattern_values[i])
                if group is None:
                    pattern_123_valid = False
                    break
                groups_123.append(group)
        
        pattern_1234_valid = True
        if len(pattern_values) >= 4:
            for i in range(4):
                if not pattern_values[i]:
                    pattern_1234_valid = False
                    break
                group = find_pattern_group(pattern_values[i])
                if group is None:
                    pattern_1234_valid = False
                    break
                groups_1234.append(group)
        
        pattern_123_text = ''.join(groups_123) if pattern_123_valid and len(groups_123) == 3 else ''
        pattern_1234_text = ''.join(groups_1234) if pattern_1234_valid and len(groups_1234) == 4 else ''
        
        first_two = get_first_two_group_values(zone)
        group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
        
        if any([pattern_123_text, pattern_1234_text, first_two]):
            st.markdown(f"#### Group {group_range}")
            for idx, v in enumerate(pattern_values[:4]):
                pattern_number = pattern_numbers[idx]
                st.text(f"Pattern {idx+1} Number: {pattern_number if pattern_number is not None else '-'}")
            
            # Add combined pattern numbers display
            if len(pattern_numbers) >= 2:
                pattern1_2 = pattern_numbers[0] + pattern_numbers[1] if pattern_numbers[0] != '-' and pattern_numbers[1] != '-' else '-'
                st.text(f"Pattern 1,2: {pattern1_2}")
            
            if len(pattern_numbers) >= 3:
                pattern1_2_3 = pattern_numbers[0] + pattern_numbers[1] + pattern_numbers[2] if all(p != '-' for p in pattern_numbers[:3]) else '-'
                st.text(f"Pattern 1,2,3: {pattern1_2_3}")
            
            # Add pattern 3,4 combined display
            if len(pattern_numbers) >= 4:
                pattern3_4 = pattern_numbers[2] + pattern_numbers[3] if pattern_numbers[2] != '-' and pattern_numbers[3] != '-' else '-'
                st.text(f"Pattern 3,4: {pattern3_4}")
            
            st.text(f"Pattern 1,2,3 Group: {pattern_123_text}")
            st.text(f"Pattern 1,2,3,4 Group: {pattern_1234_text}")
            st.text(f"First 2 values: {first_two}")
            st.markdown("---")

def get_pattern_sequence_type(zone):
    """Get pattern sequence type from zone data for Pattern 1,2"""
    try:
        # Get 1st row 3rd column value (index 0,2)
        value = zone['zone_data'][2][0] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 0 else ''
        return 'P_Sequence' if value.upper() == 'P' else 'B_Sequence' if value.upper() == 'B' else ''
    except Exception as e:
        st.error(f"Error in get_pattern_sequence_type: {str(e)}")  # Error log
        return ''

def get_pattern123_sequence_type(zone):
    """Get pattern sequence type from zone data for Pattern 1,2,3"""
    try:
        # Get 4th row 3rd column value (index 3,2)
        value = zone['zone_data'][2][3] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 3 else ''
        return 'P_Sequence' if value.upper() == 'P' else 'B_Sequence' if value.upper() == 'B' else ''
    except Exception as e:
        st.error(f"Error in get_pattern123_sequence_type: {str(e)}")  # Error log
        return ''

def get_pattern_from_zone(zone):
    """Extract pattern from zone data"""
    try:
        # Get Pattern 1,2 result (2nd row 3rd column)
        pattern = zone['zone_data'][2][1] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 1 else ''
        return pattern.upper() if pattern else ''
    except Exception as e:
        return ''

def get_pattern_prediction_from_db(pattern, sequence_type):
    """DB에서 Pattern 1,2 예측값 조회"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 패턴 번호 전처리 제거 - 원본 형식 그대로 사용
        pattern_str = str(pattern).strip()
        
        # DB에서 예측값 조회
        cursor.execute('''
            SELECT prediction, frequency, success_rate
            FROM pattern12_predictions 
            WHERE pattern_number = ? AND sequence_type = ?
        ''', (pattern_str, sequence_type))
        
        result = cursor.fetchone()
        if result:
            prediction, frequency, success_rate = result
            return prediction, True, frequency, success_rate
        return '', False, 0, 0
        
    except Exception as e:
        st.error(f"DB 예측 조회 오류: {str(e)}")
        return '', False, 0, 0
    finally:
        if conn:
            conn.close()

def get_pattern123_prediction_from_db(pattern, sequence_type):
    """DB에서 Pattern 1,2,3 예측값 조회"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 패턴 번호 전처리 제거 - 원본 형식 그대로 사용
        pattern_str = str(pattern).strip()
        
        # DB에서 예측값 조회
        cursor.execute('''
            SELECT prediction, frequency, success_rate
            FROM pattern123_predictions 
            WHERE pattern_number = ? AND sequence_type = ?
        ''', (pattern_str, sequence_type))
        
        result = cursor.fetchone()
        if result:
            prediction, frequency, success_rate = result
            return prediction, True, frequency, success_rate
        return '', False, 0, 0
        
    except Exception as e:
        st.error(f"DB 예측 조회 오류: {str(e)}")
        return '', False, 0, 0
    finally:
        if conn:
            conn.close()

def get_pattern_prediction(pattern, sequence_type):
    """하이브리드 예측 시스템 (DB 우선, 없으면 CSV)"""
    result, found, source = get_hybrid_pattern_prediction(pattern, sequence_type)
    return result, found

def get_pattern123_prediction(pattern, sequence_type):
    """하이브리드 예측 시스템 (DB 우선, 없으면 CSV)"""
    result, found, source = get_hybrid_pattern123_prediction(pattern, sequence_type)
    return result, found

def update_prediction_tables(pattern_number, sequence_type, prediction, result, pattern_type='12'):
    """예측 결과를 바탕으로 예측 테이블 업데이트"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        table_name = f"pattern{pattern_type}_predictions"
        
        # 패턴 번호 전처리 제거 - 원본 형식 그대로 사용
        pattern_str = str(pattern_number).strip()
        
        # 기존 데이터 조회
        cursor.execute(f'''
            SELECT frequency, success_count 
            FROM {table_name} 
            WHERE pattern_number = ? AND sequence_type = ? AND prediction = ?
        ''', (pattern_str, sequence_type, prediction))
        
        existing = cursor.fetchone()
        
        if existing:
            # 기존 데이터 업데이트
            frequency, success_count = existing
            new_frequency = frequency + 1
            new_success_count = success_count + (1 if result.upper() == 'W' else 0)
            new_success_rate = (new_success_count / new_frequency * 100)
            
            cursor.execute(f'''
                UPDATE {table_name} 
                SET frequency = ?, success_count = ?, success_rate = ?, updated_at = CURRENT_TIMESTAMP
                WHERE pattern_number = ? AND sequence_type = ? AND prediction = ?
            ''', (new_frequency, new_success_count, new_success_rate, pattern_str, sequence_type, prediction))
        else:
            # 새 데이터 삽입
            success_count = 1 if result.upper() == 'W' else 0
            success_rate = 100.0 if result.upper() == 'W' else 0.0
            
            cursor.execute(f'''
                INSERT INTO {table_name} 
                (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                VALUES (?, ?, ?, 1, ?, ?)
            ''', (pattern_str, sequence_type, prediction, success_count, success_rate))
        
        conn.commit()
        return True
        
    except Exception as e:
        st.error(f"예측 테이블 업데이트 오류: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def compare_pattern_prediction(pattern, prediction):
    """Compare pattern result with prediction"""
    if pattern and prediction:
        return 'w' if pattern.upper() == prediction.upper() else 'l'
    return ''

def get_pattern_results(zone):
    """Extract pattern results and predictions from zone data"""
    try:
        # Get Pattern 1,2 result (2nd row 3rd column)
        pattern1_2 = zone['zone_data'][2][1] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 1 else ''
        # Get Pattern 1,2,3 result (5th row 3rd column)
        pattern1_2_3 = zone['zone_data'][2][4] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 4 else ''
        
        # Get patterns from zone for pattern number combination
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) >= 4:
            pattern_values = []
            for pattern in group_patterns[:4]:
                values = []
                for x, y in pattern['coordinates']:
                    relative_x = x - zone['start_x']
                    value = zone['zone_data'][relative_x][y]
                    if value:
                        values.append(value.upper())
                pattern_values.append(values)
                
            # Get pattern numbers
            pattern_numbers = []
            for v in pattern_values[:4]:
                pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
                pattern_numbers.append(pattern_number if pattern_number is not None else '-')
            
            # Get Pattern 1,2 combination
            pattern1_2_combined = pattern_numbers[0] + pattern_numbers[1] if pattern_numbers[0] != '-' and pattern_numbers[1] != '-' else '-'
            # Get Pattern 1,2,3 combination
            pattern1_2_3_combined = pattern_numbers[0] + pattern_numbers[1] + pattern_numbers[2] if all(p != '-' for p in pattern_numbers[:3]) else '-'
            # Get Pattern 3,4 combination
            pattern3_4_combined = pattern_numbers[2] + pattern_numbers[3] if pattern_numbers[2] != '-' and pattern_numbers[3] != '-' else '-'
        else:
            pattern1_2_combined = '-'
            pattern1_2_3_combined = '-'
            pattern3_4_combined = '-'
        
        # Get sequence types for each pattern
        sequence_type_12 = get_pattern_sequence_type(zone)
        sequence_type_123 = get_pattern123_sequence_type(zone)
        
        # Get predictions using hybrid functions
        prediction1_2, found1_2, source1_2 = get_hybrid_pattern_prediction(pattern1_2_combined, sequence_type_12)
        prediction1_2_3, found1_2_3, source1_2_3 = get_hybrid_pattern123_prediction(pattern1_2_3_combined, sequence_type_123)
        
        # Compare and get results
        comparison1_2 = compare_pattern_prediction(pattern1_2, prediction1_2)
        comparison1_2_3 = compare_pattern_prediction(pattern1_2_3, prediction1_2_3)
        
        return pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123
    except Exception as e:
        return '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', '', ''

def save_pattern_analysis(zones, session_id):
    """Save pattern analysis results to database"""
    try:
        # Get absolute path to database file
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 기존 sequence_type 필드를 pattern12_sequence_type으로 변경
        try:
            cursor.execute('ALTER TABLE pattern_analysis RENAME COLUMN sequence_type TO pattern12_sequence_type')
        except:
            pass  # 이미 변경된 경우 무시
        
        # pattern123_sequence_type 필드 추가
        try:
            cursor.execute('ALTER TABLE pattern_analysis ADD COLUMN pattern123_sequence_type TEXT')
        except:
            pass  # 이미 존재하는 경우 무시
        
        # Get current date and total groups
        current_date = datetime.now().date()
        total_groups = len(zones)
        
        # Prepare data for insertion
        for idx, zone in enumerate(zones, 1):
            group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
            
            # Get pattern results
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
            
            # Calculate prediction accuracy
            total_predictions = 0
            correct_predictions = 0
            if comparison1_2:
                total_predictions += 1
                if comparison1_2 == 'W':
                    correct_predictions += 1
            if comparison1_2_3:
                total_predictions += 1
                if comparison1_2_3 == 'W':
                    correct_predictions += 1
            prediction_accuracy = (correct_predictions / total_predictions * 100) if total_predictions > 0 else 0
            
            # Insert data with separate sequence types
            cursor.execute('''
                INSERT INTO pattern_analysis (
                    session_id, session_date, total_groups_in_session,
                    group_id, group_start, group_end, group_sequence,
                    pattern12_result, pattern12_combined, pattern12_prediction, pattern12_prediction_result,
                    pattern123_result, pattern123_combined, pattern123_prediction, pattern123_prediction_result,
                    pattern12_sequence_type, pattern123_sequence_type, prediction_accuracy
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id, current_date, total_groups,
                group_range, zone['start_x'] + 1, zone['end_x'] + 1, idx,
                pattern1_2, pattern1_2_combined, prediction1_2, comparison1_2,
                pattern1_2_3, pattern1_2_3_combined, prediction1_2_3, comparison1_2_3,
                sequence_type_12, sequence_type_123, prediction_accuracy
            ))
        
        conn.commit()
        return True
            
    except Exception as e:
        st.error(f"Database error: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def create_new_tables():
    """새로운 패턴 분석을 위한 테이블 생성"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Session Prediction Results 테이블 생성
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS new_session_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                prediction_results TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # Pattern Group Analysis 테이블 생성
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS new_pattern_group_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                pattern_type TEXT NOT NULL,
                group_range TEXT NOT NULL,
                pattern_result TEXT,
                pattern_combined TEXT,
                sequence_type TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        st.error(f"새로운 테이블 생성 오류: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def save_new_pattern_analysis(zones, session_id, pattern_type):
    """새로운 패턴 분석 결과를 독립적인 테이블에 저장"""
    try:
        # 기존 DB에 새로운 테이블 생성하여 저장
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. Session Prediction Results 저장
        all_prediction_results = []
        sorted_zones_results = sorted(zones, key=lambda x: x['start_x'])
        
        for zone in sorted_zones_results:
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
            
            if comparison1_2:
                all_prediction_results.append(comparison1_2.upper())
            if comparison1_2_3:
                all_prediction_results.append(comparison1_2_3.upper())
        
        if all_prediction_results:
            combined_results = ''.join(all_prediction_results)
            
            # 새로운 테이블에 저장
            cursor.execute('''
                INSERT INTO new_session_predictions (session_id, pattern_type, prediction_results)
                VALUES (?, ?, ?)
            ''', (session_id, pattern_type, combined_results))
        
        # 2. Pattern Group Analysis 저장
        for zone in zones:
            group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
            
            # Pattern1, Pattern2, Pattern3에 따라 다른 데이터 저장
            if pattern_type == 'pattern1':
                pattern_result = pattern1_2
                pattern_combined = pattern1_2_combined
            elif pattern_type == 'pattern2':
                pattern_result = pattern1_2_3
                pattern_combined = pattern1_2_3_combined
            else:  # pattern3
                pattern_result = pattern3_4_combined
                pattern_combined = pattern3_4_combined
            
            cursor.execute('''
                INSERT INTO new_pattern_group_analysis 
                (session_id, pattern_type, group_range, pattern_result, pattern_combined, sequence_type)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (session_id, pattern_type, group_range, pattern_result, pattern_combined, sequence_type))
        
        conn.commit()
        return True
        
    except Exception as e:
        st.error(f"새로운 패턴 분석 저장 오류: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def update_prediction_tables_from_new_data(zones):
    """새로 저장된 패턴 데이터로 예측 테이블 업데이트"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Pattern 1,2 예측 데이터 수집
        pattern12_data = {}
        pattern123_data = {}
        
        for zone in zones:
            # Get pattern results for this zone
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
            
            # Process Pattern 1,2 data
            if pattern1_2_combined and prediction1_2 and comparison1_2:
                key = (pattern1_2_combined, sequence_type)
                if key not in pattern12_data:
                    pattern12_data[key] = {'b': 0, 'p': 0, 'total': 0}
                
                # 실제 결과 계산: w이면 예측값, l이면 예측값의 반대값
                if prediction1_2 == 'b':
                    opposite_result = 'p'
                elif prediction1_2 == 'p':
                    opposite_result = 'b'
                else:
                    opposite_result = prediction1_2
                
                actual_result = prediction1_2 if comparison1_2.upper() == 'W' else opposite_result
                pattern12_data[key][actual_result] += 1
                pattern12_data[key]['total'] += 1
            
            # Process Pattern 1,2,3 data
            if pattern1_2_3_combined and prediction1_2_3 and comparison1_2_3:
                key = (pattern1_2_3_combined, sequence_type)
                if key not in pattern123_data:
                    pattern123_data[key] = {'b': 0, 'p': 0, 'total': 0}
                
                # 실제 결과 계산: w이면 예측값, l이면 예측값의 반대값
                if prediction1_2_3 == 'b':
                    opposite_result = 'p'
                elif prediction1_2_3 == 'p':
                    opposite_result = 'b'
                else:
                    opposite_result = prediction1_2_3
                
                actual_result = prediction1_2_3 if comparison1_2_3.upper() == 'W' else opposite_result
                pattern123_data[key][actual_result] += 1
                pattern123_data[key]['total'] += 1
        
        # Update Pattern 1,2 predictions
        for (pattern_number, sequence_type), data in pattern12_data.items():
            # 가장 많이 나온 결과를 예측값으로 사용
            if data['b'] > data['p']:
                prediction = 'b'
                success_count = data['b']
            elif data['p'] > data['b']:
                prediction = 'p'
                success_count = data['p']
            else:
                # 동점인 경우 기본값
                prediction = 'b'
                success_count = data['b']
            
            frequency = data['total']
            success_rate = (success_count / frequency * 100) if frequency > 0 else 0
            
            # 기존 데이터 조회 (pattern_number와 sequence_type만으로)
            cursor.execute('''
                SELECT frequency, success_count, prediction
                FROM pattern12_predictions 
                WHERE pattern_number = ? AND sequence_type = ?
            ''', (pattern_number, sequence_type))
            
            existing = cursor.fetchone()
            
            if existing:
                # 기존 데이터가 있으면 업데이트
                old_frequency, old_success_count, old_prediction = existing
                
                # 예측값이 변경되었는지 확인
                if old_prediction == prediction:
                    # 같은 예측값이면 기존 데이터에 추가
                    new_frequency = old_frequency + frequency
                    new_success_count = old_success_count + success_count
                    new_success_rate = (new_success_count / new_frequency * 100)
                    
                    cursor.execute('''
                        UPDATE pattern12_predictions 
                        SET frequency = ?, success_count = ?, success_rate = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE pattern_number = ? AND sequence_type = ?
                    ''', (new_frequency, new_success_count, new_success_rate, pattern_number, sequence_type))
                else:
                    # 예측값이 변경되었으면 기존 데이터 삭제 후 새로 삽입
                    cursor.execute('''
                        DELETE FROM pattern12_predictions 
                        WHERE pattern_number = ? AND sequence_type = ?
                    ''', (pattern_number, sequence_type))
                    
                    cursor.execute('''
                        INSERT INTO pattern12_predictions 
                        (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (pattern_number, sequence_type, prediction, frequency, success_count, success_rate))
            else:
                # 새 데이터 삽입
                cursor.execute('''
                    INSERT INTO pattern12_predictions 
                    (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (pattern_number, sequence_type, prediction, frequency, success_count, success_rate))
        
        # Update Pattern 1,2,3 predictions
        for (pattern_number, sequence_type), data in pattern123_data.items():
            # 가장 많이 나온 결과를 예측값으로 사용
            if data['b'] > data['p']:
                prediction = 'b'
                success_count = data['b']
            elif data['p'] > data['b']:
                prediction = 'p'
                success_count = data['p']
            else:
                # 동점인 경우 기본값
                prediction = 'b'
                success_count = data['b']
            
            frequency = data['total']
            success_rate = (success_count / frequency * 100) if frequency > 0 else 0
            
            # 기존 데이터 조회 (pattern_number와 sequence_type만으로)
            cursor.execute('''
                SELECT frequency, success_count, prediction
                FROM pattern123_predictions 
                WHERE pattern_number = ? AND sequence_type = ?
            ''', (pattern_number, sequence_type))
            
            existing = cursor.fetchone()
            
            if existing:
                # 기존 데이터가 있으면 업데이트
                old_frequency, old_success_count, old_prediction = existing
                
                # 예측값이 변경되었는지 확인
                if old_prediction == prediction:
                    # 같은 예측값이면 기존 데이터에 추가
                    new_frequency = old_frequency + frequency
                    new_success_count = old_success_count + success_count
                    new_success_rate = (new_success_count / new_frequency * 100)
                    
                    cursor.execute('''
                        UPDATE pattern123_predictions 
                        SET frequency = ?, success_count = ?, success_rate = ?, updated_at = CURRENT_TIMESTAMP
                        WHERE pattern_number = ? AND sequence_type = ?
                    ''', (new_frequency, new_success_count, new_success_rate, pattern_number, sequence_type))
                else:
                    # 예측값이 변경되었으면 기존 데이터 삭제 후 새로 삽입
                    cursor.execute('''
                        DELETE FROM pattern123_predictions 
                        WHERE pattern_number = ? AND sequence_type = ?
                    ''', (pattern_number, sequence_type))
                    
                    cursor.execute('''
                        INSERT INTO pattern123_predictions 
                        (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (pattern_number, sequence_type, prediction, frequency, success_count, success_rate))
            else:
                # 새 데이터 삽입
                cursor.execute('''
                    INSERT INTO pattern123_predictions 
                    (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (pattern_number, sequence_type, prediction, frequency, success_count, success_rate))
        
        conn.commit()
        return True
        
    except Exception as e:
        st.error(f"예측 테이블 업데이트 오류: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def init_db():
    """Initialize database and create tables if they don't exist"""
    try:
        # Get absolute path to database file
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Create pattern_analysis table if it doesn't exist
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pattern_analysis (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                session_date TEXT NOT NULL,
                total_groups_in_session INTEGER NOT NULL,
                group_id TEXT NOT NULL,
                group_start INTEGER NOT NULL,
                group_end INTEGER NOT NULL,
                group_sequence INTEGER NOT NULL,
                pattern12_result TEXT,
                pattern12_combined TEXT,
                pattern12_prediction TEXT,
                pattern12_prediction_result TEXT,
                pattern123_result TEXT,
                pattern123_combined TEXT,
                pattern123_prediction TEXT,
                pattern123_prediction_result TEXT,
                sequence_type TEXT,
                prediction_accuracy REAL,
                created_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', datetime('now', '+9 hours'))),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        
        conn.commit()
    except Exception as e:
        st.error(f"Database initialization error: {str(e)}")
    finally:
        if conn:
            conn.close()


def ensure_game_outcome_table():
    """최소 데이터 저장용 테이블 생성"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('''
            CREATE TABLE IF NOT EXISTS game_outcome_summary (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                converted_grid TEXT NOT NULL,
                reconstructed_grid TEXT,
                sequence_prediction_results TEXT,
                reconstructed_sequence_prediction_results TEXT,
                reconstructed_gap_results TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')

        conn.commit()
        return True
    except Exception as e:
        st.error(f"Outcome summary table creation error: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()


def serialize_grid_for_storage(grid):
    """그리드를 JSON 문자열로 직렬화"""
    if not grid:
        return ''
    try:
        return json.dumps(grid)
    except Exception:
        return ''


def save_game_outcome_summary(converted_grid_str, reconstructed_grid_str, sequence_results, reconstructed_sequence_results, reconstructed_gap_results):
    """최소 데이터 결과 저장"""
    if not ensure_game_outcome_table():
        return False, None

    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        session_id = str(uuid.uuid4())
        cursor.execute('''
            INSERT INTO game_outcome_summary (
                session_id,
                converted_grid,
                reconstructed_grid,
                sequence_prediction_results,
                reconstructed_sequence_prediction_results,
                reconstructed_gap_results
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', (
            session_id,
            converted_grid_str,
            reconstructed_grid_str,
            sequence_results or '',
            reconstructed_sequence_results or '',
            reconstructed_gap_results or ''
        ))

        conn.commit()
        return True, session_id
    except Exception as e:
        st.error(f"Outcome summary save error: {str(e)}")
        return False, None
    finally:
        if conn:
            conn.close()


# Initialize database when the app starts
init_db()

def create_prediction_tables():
    """예측 데이터를 저장할 테이블들 생성"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Pattern 1,2 예측 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pattern12_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_number TEXT NOT NULL,
                sequence_type TEXT NOT NULL,
                prediction TEXT NOT NULL,
                frequency INTEGER DEFAULT 1,
                success_count INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', datetime('now', '+9 hours'))),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pattern_number, sequence_type)
            )
        ''')
        
        # Pattern 1,2,3 예측 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS pattern123_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_number TEXT NOT NULL,
                sequence_type TEXT NOT NULL,
                prediction TEXT NOT NULL,
                frequency INTEGER DEFAULT 1,
                success_count INTEGER DEFAULT 0,
                success_rate REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT (strftime('%Y-%m-%d %H:%M:%S', datetime('now', '+9 hours'))),
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(pattern_number, sequence_type)
            )
        ''')
        
        conn.commit()
        return True
    except Exception as e:
        st.error(f"예측 테이블 생성 오류: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def build_prediction_tables_from_existing_data():
    """기존 pattern_analysis 데이터로 예측 테이블 구축"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 예측 테이블 초기화
        cursor.execute('DELETE FROM pattern12_predictions')
        cursor.execute('DELETE FROM pattern123_predictions')
        
        # Pattern 1,2 예측 데이터 수집 및 실제 결과 계산
        cursor.execute('''
            SELECT 
                pattern12_combined,
                pattern12_sequence_type,
                pattern12_prediction,
                pattern12_prediction_result,
                COUNT(*) as frequency
            FROM pattern_analysis 
            WHERE pattern12_combined IS NOT NULL 
                AND pattern12_prediction IS NOT NULL 
                AND pattern12_sequence_type IS NOT NULL
                AND pattern12_prediction_result IS NOT NULL
                AND pattern12_prediction_result != ''
            GROUP BY pattern12_combined, pattern12_sequence_type, pattern12_prediction, pattern12_prediction_result
        ''')
        
        pattern12_raw_data = cursor.fetchall()
        
        # Pattern 1,2,3 예측 데이터 수집 및 실제 결과 계산
        cursor.execute('''
            SELECT 
                pattern123_combined,
                pattern123_sequence_type,
                pattern123_prediction,
                pattern123_prediction_result,
                COUNT(*) as frequency
            FROM pattern_analysis 
            WHERE pattern123_combined IS NOT NULL 
                AND pattern123_prediction IS NOT NULL 
                AND pattern123_sequence_type IS NOT NULL
                AND pattern123_prediction_result IS NOT NULL
                AND pattern123_prediction_result != ''
            GROUP BY pattern123_combined, pattern123_sequence_type, pattern123_prediction, pattern123_prediction_result
        ''')
        
        pattern123_raw_data = cursor.fetchall()
        
        # Pattern 1,2 데이터 처리
        pattern12_processed = {}
        for row in pattern12_raw_data:
            pattern_number, sequence_type, prediction, result, frequency = row
            
            # 실제 결과 계산: w이면 예측값, l이면 예측값의 반대값
            if prediction == 'b':
                opposite_result = 'p'
            elif prediction == 'p':
                opposite_result = 'b'
            else:
                opposite_result = prediction
            
            actual_result = prediction if result == 'w' else opposite_result
            
            key = (pattern_number, sequence_type)
            if key not in pattern12_processed:
                pattern12_processed[key] = {'b': 0, 'p': 0, 'total': 0}
            
            pattern12_processed[key][actual_result] += frequency
            pattern12_processed[key]['total'] += frequency
        
        # Pattern 1,2,3 데이터 처리
        pattern123_processed = {}
        for row in pattern123_raw_data:
            pattern_number, sequence_type, prediction, result, frequency = row
            
            # 실제 결과 계산: w이면 예측값, l이면 예측값의 반대값
            if prediction == 'b':
                opposite_result = 'p'
            elif prediction == 'p':
                opposite_result = 'b'
            else:
                opposite_result = prediction
            
            actual_result = prediction if result == 'w' else opposite_result
            
            key = (pattern_number, sequence_type)
            if key not in pattern123_processed:
                pattern123_processed[key] = {'b': 0, 'p': 0, 'total': 0}
            
            pattern123_processed[key][actual_result] += frequency
            pattern123_processed[key]['total'] += frequency
        
        # 예측 테이블에 데이터 삽입
        for (pattern_number, sequence_type), data in pattern12_processed.items():
            # 가장 많이 나온 결과를 예측값으로 사용
            if data['b'] > data['p']:
                prediction = 'b'
                success_count = data['b']
            elif data['p'] > data['b']:
                prediction = 'p'
                success_count = data['p']
            else:
                # 동점인 경우 기본값
                prediction = 'b'
                success_count = data['b']
            
            frequency = data['total']
            success_rate = (success_count / frequency * 100) if frequency > 0 else 0
            
            cursor.execute('''
                INSERT OR REPLACE INTO pattern12_predictions 
                (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (pattern_number, sequence_type, prediction, frequency, success_count, success_rate))
        
        for (pattern_number, sequence_type), data in pattern123_processed.items():
            # 가장 많이 나온 결과를 예측값으로 사용
            if data['b'] > data['p']:
                prediction = 'b'
                success_count = data['b']
            elif data['p'] > data['b']:
                prediction = 'p'
                success_count = data['p']
            else:
                # 동점인 경우 기본값
                prediction = 'b'
                success_count = data['b']
            
            frequency = data['total']
            success_rate = (success_count / frequency * 100) if frequency > 0 else 0
            
            cursor.execute('''
                INSERT OR REPLACE INTO pattern123_predictions 
                (pattern_number, sequence_type, prediction, frequency, success_count, success_rate)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (pattern_number, sequence_type, prediction, frequency, success_count, success_rate))
        
        conn.commit()
        return True
        
    except Exception as e:
        st.error(f"예측 테이블 구축 오류: {str(e)}")
        return False
    finally:
        if conn:
            conn.close()

def get_prediction_table_statistics():
    """예측 테이블의 통계 정보 조회"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # Pattern 1,2 통계
        cursor.execute('''
            SELECT 
                COUNT(*) as total_patterns,
                AVG(success_rate) as avg_success_rate,
                SUM(frequency) as total_frequency,
                COUNT(CASE WHEN success_rate >= 70 THEN 1 END) as high_accuracy_patterns
            FROM pattern12_predictions
        ''')
        pattern12_stats = cursor.fetchone()
        
        # Pattern 1,2,3 통계
        cursor.execute('''
            SELECT 
                COUNT(*) as total_patterns,
                AVG(success_rate) as avg_success_rate,
                SUM(frequency) as total_frequency,
                COUNT(CASE WHEN success_rate >= 70 THEN 1 END) as high_accuracy_patterns
            FROM pattern123_predictions
        ''')
        pattern123_stats = cursor.fetchone()
        
        return {
            'pattern12': {
                'total_patterns': pattern12_stats[0],
                'avg_success_rate': pattern12_stats[1],
                'total_frequency': pattern12_stats[2],
                'high_accuracy_patterns': pattern12_stats[3]
            },
            'pattern123': {
                'total_patterns': pattern123_stats[0],
                'avg_success_rate': pattern123_stats[1],
                'total_frequency': pattern123_stats[2],
                'high_accuracy_patterns': pattern123_stats[3]
            }
        }
        
    except Exception as e:
        st.error(f"통계 조회 오류: {str(e)}")
        return None
    finally:
        if conn:
            conn.close()

def verify_prediction_tables_creation():
    """예측 테이블 생성 검증"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 테이블 존재 여부 확인
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name IN ('pattern12_predictions', 'pattern123_predictions')")
        existing_tables = [row[0] for row in cursor.fetchall()]
        
        # 테이블 구조 확인
        table_structures = {}
        for table_name in ['pattern12_predictions', 'pattern123_predictions']:
            if table_name in existing_tables:
                cursor.execute(f"PRAGMA table_info({table_name})")
                columns = cursor.fetchall()
                table_structures[table_name] = [col[1] for col in columns]
        
        return {
            'tables_exist': len(existing_tables) == 2,
            'table_structures': table_structures,
            'expected_tables': ['pattern12_predictions', 'pattern123_predictions'],
            'found_tables': existing_tables
        }
        
    except Exception as e:
        return {'error': str(e)}
    finally:
        if conn:
            conn.close()

def verify_prediction_data_build():
    """예측 데이터 구축 검증"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 기존 pattern_analysis 데이터 확인
        cursor.execute("SELECT COUNT(*) FROM pattern_analysis")
        total_analysis_records = cursor.fetchone()[0]
        
        # 예측 테이블 데이터 확인
        cursor.execute("SELECT COUNT(*) FROM pattern12_predictions")
        pattern12_records = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM pattern123_predictions")
        pattern123_records = cursor.fetchone()[0]
        
        # 샘플 데이터 확인
        cursor.execute("SELECT * FROM pattern12_predictions LIMIT 3")
        pattern12_samples = cursor.fetchall()
        
        cursor.execute("SELECT * FROM pattern123_predictions LIMIT 3")
        pattern123_samples = cursor.fetchall()
        
        return {
            'total_analysis_records': total_analysis_records,
            'pattern12_records': pattern12_records,
            'pattern123_records': pattern123_records,
            'pattern12_samples': pattern12_samples,
            'pattern123_samples': pattern123_samples,
            'has_data': pattern12_records > 0 and pattern123_records > 0
        }
        
    except Exception as e:
        return {'error': str(e)}
    finally:
        if conn:
            conn.close()

def debug_pattern_analysis_data():
    """pattern_analysis 테이블 데이터 디버깅"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 테이블 구조 확인
        cursor.execute("PRAGMA table_info(pattern_analysis)")
        columns = cursor.fetchall()
        
        # 샘플 데이터 확인
        cursor.execute("SELECT * FROM pattern_analysis LIMIT 5")
        sample_data = cursor.fetchall()
        
        # pattern12_combined, pattern123_combined 컬럼의 데이터 확인
        cursor.execute("""
            SELECT 
                pattern12_combined,
                pattern12_prediction,
                pattern12_prediction_result,
                pattern123_combined,
                pattern123_prediction,
                pattern123_prediction_result,
                sequence_type
            FROM pattern_analysis 
            WHERE pattern12_combined IS NOT NULL 
                OR pattern123_combined IS NOT NULL
            LIMIT 10
        """)
        pattern_data = cursor.fetchall()
        
        # NULL이 아닌 데이터 개수 확인
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(pattern12_combined) as pattern12_count,
                COUNT(pattern12_prediction) as pattern12_prediction_count,
                COUNT(pattern12_prediction_result) as pattern12_result_count,
                COUNT(pattern123_combined) as pattern123_count,
                COUNT(pattern123_prediction) as pattern123_prediction_count,
                COUNT(pattern123_prediction_result) as pattern123_result_count,
                COUNT(sequence_type) as sequence_type_count
            FROM pattern_analysis
        """)
        counts = cursor.fetchone()
        
        return {
            'table_structure': [col[1] for col in columns],
            'sample_data': sample_data,
            'pattern_data': pattern_data,
            'counts': {
                'total_records': counts[0],
                'pattern12_combined': counts[1],
                'pattern12_prediction': counts[2],
                'pattern12_prediction_result': counts[3],
                'pattern123_combined': counts[4],
                'pattern123_prediction': counts[5],
                'pattern123_prediction_result': counts[6],
                'sequence_type': counts[7]
            }
        }
        
    except Exception as e:
        return {'error': str(e)}
    finally:
        if conn:
            conn.close()

def debug_success_calculation():
    """success 계산 로직 디버깅"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # pattern12_prediction_result 값 분포 확인
        cursor.execute("""
            SELECT 
                pattern12_prediction_result,
                COUNT(*) as count
            FROM pattern_analysis 
            WHERE pattern12_prediction_result IS NOT NULL
            GROUP BY pattern12_prediction_result
        """)
        pattern12_results = cursor.fetchall()
        
        # pattern123_prediction_result 값 분포 확인
        cursor.execute("""
            SELECT 
                pattern123_prediction_result,
                COUNT(*) as count
            FROM pattern_analysis 
            WHERE pattern123_prediction_result IS NOT NULL
            GROUP BY pattern123_prediction_result
        """)
        pattern123_results = cursor.fetchall()
        
        # 실제 success 계산 테스트 (대문자 기준)
        cursor.execute("""
            SELECT 
                pattern12_combined,
                sequence_type,
                pattern12_prediction,
                pattern12_prediction_result,
                COUNT(*) as frequency,
                SUM(CASE WHEN pattern12_prediction_result = 'W' THEN 1 ELSE 0 END) as success_count_upper,
                SUM(CASE WHEN pattern12_prediction_result = 'w' THEN 1 ELSE 0 END) as success_count_lower,
                SUM(CASE WHEN UPPER(pattern12_prediction_result) = 'W' THEN 1 ELSE 0 END) as success_count_fixed,
                SUM(CASE WHEN pattern12_prediction_result = 'L' THEN 1 ELSE 0 END) as fail_count_upper,
                SUM(CASE WHEN pattern12_prediction_result = 'l' THEN 1 ELSE 0 END) as fail_count_lower,
                SUM(CASE WHEN pattern12_prediction_result IS NULL THEN 1 ELSE 0 END) as null_count
            FROM pattern_analysis 
            WHERE pattern12_combined IS NOT NULL 
                AND pattern12_prediction IS NOT NULL 
                AND sequence_type IS NOT NULL
                AND pattern12_prediction_result IS NOT NULL
                AND pattern12_prediction_result != ''
            GROUP BY pattern12_combined, sequence_type, pattern12_prediction
            LIMIT 10
        """)
        pattern12_calculation = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                pattern123_combined,
                sequence_type,
                pattern123_prediction,
                pattern123_prediction_result,
                COUNT(*) as frequency,
                SUM(CASE WHEN pattern123_prediction_result = 'W' THEN 1 ELSE 0 END) as success_count_upper,
                SUM(CASE WHEN pattern123_prediction_result = 'w' THEN 1 ELSE 0 END) as success_count_lower,
                SUM(CASE WHEN UPPER(pattern123_prediction_result) = 'W' THEN 1 ELSE 0 END) as success_count_fixed,
                SUM(CASE WHEN pattern123_prediction_result = 'L' THEN 1 ELSE 0 END) as fail_count_upper,
                SUM(CASE WHEN pattern123_prediction_result = 'l' THEN 1 ELSE 0 END) as fail_count_lower,
                SUM(CASE WHEN pattern123_prediction_result IS NULL THEN 1 ELSE 0 END) as null_count
            FROM pattern_analysis 
            WHERE pattern123_combined IS NOT NULL 
                AND pattern123_prediction IS NOT NULL 
                AND sequence_type IS NOT NULL
                AND pattern123_prediction_result IS NOT NULL
                AND pattern123_prediction_result != ''
            GROUP BY pattern123_combined, sequence_type, pattern123_prediction
            LIMIT 10
        """)
        pattern123_calculation = cursor.fetchall()
        
        # 예측 테이블의 실제 데이터 확인
        cursor.execute("SELECT * FROM pattern12_predictions LIMIT 5")
        pattern12_predictions_sample = cursor.fetchall()
        
        cursor.execute("SELECT * FROM pattern123_predictions LIMIT 5")
        pattern123_predictions_sample = cursor.fetchall()
        
        return {
            'pattern12_results_distribution': pattern12_results,
            'pattern123_results_distribution': pattern123_results,
            'pattern12_calculation_sample': pattern12_calculation,
            'pattern123_calculation_sample': pattern123_calculation,
            'pattern12_predictions_sample': pattern12_predictions_sample,
            'pattern123_predictions_sample': pattern123_predictions_sample
        }
        
    except Exception as e:
        return {'error': str(e)}
    finally:
        if conn:
            conn.close()

def debug_prediction_tables_data():
    """예측 테이블의 실제 데이터 확인"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # pattern12_predictions 테이블 실제 데이터 확인
        cursor.execute("SELECT COUNT(*) FROM pattern12_predictions")
        pattern12_total = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_patterns,
                AVG(success_rate) as avg_success_rate,
                SUM(frequency) as total_frequency,
                COUNT(CASE WHEN success_rate >= 70 THEN 1 END) as high_accuracy_patterns,
                SUM(success_count) as total_success_count,
                SUM(frequency) as total_frequency_sum
            FROM pattern12_predictions
        """)
        pattern12_stats = cursor.fetchone()
        
        # pattern123_predictions 테이블 실제 데이터 확인
        cursor.execute("SELECT COUNT(*) FROM pattern123_predictions")
        pattern123_total = cursor.fetchone()[0]
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_patterns,
                AVG(success_rate) as avg_success_rate,
                SUM(frequency) as total_frequency,
                COUNT(CASE WHEN success_rate >= 70 THEN 1 END) as high_accuracy_patterns,
                SUM(success_count) as total_success_count,
                SUM(frequency) as total_frequency_sum
            FROM pattern123_predictions
        """)
        pattern123_stats = cursor.fetchone()
        
        # 실제 데이터 샘플 확인
        cursor.execute("""
            SELECT 
                pattern_number, sequence_type, prediction, frequency, success_count, success_rate
            FROM pattern12_predictions 
            WHERE success_count > 0 
            ORDER BY success_rate DESC 
            LIMIT 10
        """)
        pattern12_success_samples = cursor.fetchall()
        
        cursor.execute("""
            SELECT 
                pattern_number, sequence_type, prediction, frequency, success_count, success_rate
            FROM pattern123_predictions 
            WHERE success_count > 0 
            ORDER BY success_rate DESC 
            LIMIT 10
        """)
        pattern123_success_samples = cursor.fetchall()
        
        # 모든 데이터가 0인지 확인
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN success_count = 0 THEN 1 END) as zero_success_records,
                COUNT(CASE WHEN success_rate = 0 THEN 1 END) as zero_rate_records,
                COUNT(CASE WHEN frequency = 0 THEN 1 END) as zero_frequency_records
            FROM pattern12_predictions
        """)
        pattern12_zero_check = cursor.fetchone()
        
        cursor.execute("""
            SELECT 
                COUNT(*) as total_records,
                COUNT(CASE WHEN success_count = 0 THEN 1 END) as zero_success_records,
                COUNT(CASE WHEN success_rate = 0 THEN 1 END) as zero_rate_records,
                COUNT(CASE WHEN frequency = 0 THEN 1 END) as zero_frequency_records
            FROM pattern123_predictions
        """)
        pattern123_zero_check = cursor.fetchone()
        
        return {
            'pattern12': {
                'total_records': pattern12_total,
                'stats_from_db': {
                    'total_patterns': pattern12_stats[0],
                    'avg_success_rate': pattern12_stats[1],
                    'total_frequency': pattern12_stats[2],
                    'high_accuracy_patterns': pattern12_stats[3],
                    'total_success_count': pattern12_stats[4],
                    'total_frequency_sum': pattern12_stats[5]
                },
                'zero_check': {
                    'total_records': pattern12_zero_check[0],
                    'zero_success_records': pattern12_zero_check[1],
                    'zero_rate_records': pattern12_zero_check[2],
                    'zero_frequency_records': pattern12_zero_check[3]
                },
                'success_samples': pattern12_success_samples
            },
            'pattern123': {
                'total_records': pattern123_total,
                'stats_from_db': {
                    'total_patterns': pattern123_stats[0],
                    'avg_success_rate': pattern123_stats[1],
                    'total_frequency': pattern123_stats[2],
                    'high_accuracy_patterns': pattern123_stats[3],
                    'total_success_count': pattern123_stats[4],
                    'total_frequency_sum': pattern123_stats[5]
                },
                'zero_check': {
                    'total_records': pattern123_zero_check[0],
                    'zero_success_records': pattern123_zero_check[1],
                    'zero_rate_records': pattern123_zero_check[2],
                    'zero_frequency_records': pattern123_zero_check[3]
                },
                'success_samples': pattern123_success_samples
            }
        }
        
    except Exception as e:
        return {'error': str(e)}
    finally:
        if conn:
            conn.close()

def test_db_prediction_functions():
    """2단계: DB 기반 예측 함수 테스트"""
    try:
        # 테스트할 패턴들
        test_patterns = [
            ('0114', 'P_Sequence', '12'),
            ('010101', 'B_Sequence', '123'),
            ('9999', 'P_Sequence', '12'),  # 존재하지 않는 패턴
            ('999999', 'B_Sequence', '123')  # 존재하지 않는 패턴
        ]
        
        results = []
        
        for pattern, sequence_type, pattern_type in test_patterns:
            if pattern_type == '12':
                prediction, found, frequency, success_rate = get_pattern_prediction_from_db(pattern, sequence_type)
            else:
                prediction, found, frequency, success_rate = get_pattern123_prediction_from_db(pattern, sequence_type)
            
            results.append({
                'pattern': pattern,
                'sequence_type': sequence_type,
                'pattern_type': pattern_type,
                'prediction': prediction,
                'found': found,
                'frequency': frequency,
                'success_rate': success_rate
            })
        
        return {
            'test_results': results,
            'summary': {
                'total_tests': len(results),
                'found_patterns': sum(1 for r in results if r['found']),
                'not_found_patterns': sum(1 for r in results if not r['found'])
            }
        }
        
    except Exception as e:
        return {'error': str(e)}

def add_verification_ui():
    """검증을 위한 UI 추가"""
    st.markdown("---")
    st.markdown("### 1단계: 예측 테이블 검증")
    
    col1, col2, col3 = st.columns([1, 1, 1])
    
    with col1:
        if st.button("1. 테이블 생성", key="create_tables_btn"):
            if create_prediction_tables():
                st.success("예측 테이블 생성 완료!")
            else:
                st.error("테이블 생성 실패!")
    
    with col2:
        if st.button("2. 데이터 구축", key="build_data_btn"):
            if build_prediction_tables_from_existing_data():
                st.success("예측 데이터 구축 완료!")
            else:
                st.error("데이터 구축 실패!")
    
    with col3:
        if st.button("3. 통계 확인", key="check_stats_btn"):
            stats = get_prediction_table_statistics()
            if stats:
                st.success("통계 조회 완료!")
                st.json(stats)
            else:
                st.error("통계 조회 실패!")
    
    # 예측 테이블 업데이트 버튼 추가
    st.markdown("### 예측 테이블 업데이트")
    col_update1, col_update2 = st.columns([1, 1])
    
    with col_update1:
        if st.button("예측 테이블 수동 업데이트", key="manual_update_btn"):
            if st.session_state.show_grid and st.session_state.converted_grid is not None:
                zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
                if zones:
                    if update_prediction_tables_from_new_data(zones):
                        st.success("예측 테이블 업데이트 완료!")
                    else:
                        st.error("예측 테이블 업데이트 실패!")
                else:
                    st.warning("업데이트할 데이터가 없습니다.")
            else:
                st.warning("먼저 SVG 코드를 파싱해주세요.")
    
    with col_update2:
        if st.button("전체 데이터로 예측 테이블 재구축", key="rebuild_predictions_btn"):
            if build_prediction_tables_from_existing_data():
                st.success("예측 테이블 재구축 완료!")
            else:
                st.error("예측 테이블 재구축 실패!")
    
    # 2단계 테스트 버튼 추가
    st.markdown("### 2단계: DB 기반 예측 함수 테스트")
    if st.button("4. DB 예측 함수 테스트", key="test_db_prediction_btn"):
        test_results = test_db_prediction_functions()
        if 'error' not in test_results:
            st.success("DB 예측 함수 테스트 완료!")
            st.json(test_results)
        else:
            st.error(f"DB 예측 함수 테스트 실패: {test_results['error']}")
    
    # 검증 결과 표시
    if st.button("검증 실행", key="run_verification_btn"):
        st.markdown("#### 테이블 생성 검증")
        creation_result = verify_prediction_tables_creation()
        st.json(creation_result)
        
        st.markdown("#### 데이터 구축 검증")
        build_result = verify_prediction_data_build()
        st.json(build_result)
        
        st.markdown("#### 통계 정보")
        stats = get_prediction_table_statistics()
        if stats:
            st.json(stats)

        st.markdown("#### pattern_analysis 테이블 디버깅")
        debug_result = debug_pattern_analysis_data()
        st.json(debug_result)

        st.markdown("#### success 계산 로직 디버깅")
        success_debug_result = debug_success_calculation()
        st.json(success_debug_result)

        st.markdown("#### 예측 테이블 실제 데이터 확인")
        debug_prediction_result = debug_prediction_tables_data()
        st.json(debug_prediction_result)

        st.markdown("#### 2단계: DB 기반 예측 함수 테스트")
        test_results = test_db_prediction_functions()
        st.json(test_results)

def main():
    # Set full page width
    st.markdown("""
        <style>
        .stApp {margin-top: -2.5rem;}
        div[data-testid="stExpander"],
        div[data-testid="stExpander"] *,
        div[data-testid="stVerticalBlock"],
        div[data-testid="stVerticalBlock"] *,
        div[data-testid="stElementContainer"],
        div[data-testid="stElementContainer"] *,
        div[data-testid="stHorizontalBlock"],
        div[data-testid="stHorizontalBlock"] *,
        div[data-testid="stColumn"],
        div[data-testid="stColumn"] * {
            margin: 0 !important;
            padding: 0 !important;
            box-shadow: none !important;
            background: none !important;
        }
        </style>
    """, unsafe_allow_html=True)
    
    st.title("Bead Road Parser V12")
    
    # Initialize session state
    if 'text_key' not in st.session_state:
        st.session_state.text_key = 0
    if 'grid' not in st.session_state:
        st.session_state.grid = None
    if 'show_grid' not in st.session_state:
        st.session_state.show_grid = False
    if 'converted_grid' not in st.session_state:
        st.session_state.converted_grid = None
    if 'selected_cell' not in st.session_state:
        st.session_state.selected_cell = None
    if 'converted_grid_history' not in st.session_state:  # Add history state
        st.session_state.converted_grid_history = []
    if 'reconstructed_grid_history' not in st.session_state:  # Add reconstructed grid history state
        st.session_state.reconstructed_grid_history = []
    if 'reconstructed_selected_cell' not in st.session_state:
        st.session_state.reconstructed_selected_cell = None
    if 'reconstructed_bp_btn_value' not in st.session_state:
        st.session_state.reconstructed_bp_btn_value = 'B'
    if 'reconstructed_grid' not in st.session_state:
        st.session_state.reconstructed_grid = None
    if 'matrix_sequence_mapping' not in st.session_state:
        st.session_state.matrix_sequence_mapping = {}  # Matrix 조합별 첫 번째 Sequence 값 저장
    
    # 새로운 테이블 생성
    create_new_tables()
    
    # Split screen into left and right columns (1:1 ratio)
    left_col, right_col = st.columns([1, 1])
    
    # Left column: SVG input and analysis results
    with left_col:
        svg_code = st.text_area("Paste SVG code here", height=68, key=f"svg_input_{st.session_state.text_key}")
        
        col_range_start, col_range_end = st.columns(2)
        with col_range_start:
            slice_start = st.number_input(
                "",
                min_value=1,
                max_value=TABLE_WIDTH,
                value=1,
                step=1,
                format="%d",
                label_visibility="collapsed"
            )
        with col_range_end:
            slice_end = st.number_input(
                "",
                min_value=1,
                max_value=TABLE_WIDTH,
                value=TABLE_WIDTH,
                step=1,
                format="%d",
                label_visibility="collapsed"
            )
        
        if slice_start > slice_end:
            st.warning("분석 시작 열은 종료 열보다 작거나 같아야 합니다.")
        
        col1, col2, col3 = st.columns([1, 3, 1])
        with col1:
            if st.button("Reset"):
                st.session_state.text_key += 1
                st.session_state.grid = None
                st.session_state.show_grid = False
                st.session_state.converted_grid = None
                st.session_state.selected_cell = None
                st.rerun()
        
        with col2:
            if st.button("Parse SVG"):
                if svg_code:
                    try:
                        # 원본 그리드 파싱
                        raw_grid = parse_bead_road_svg(svg_code)
                        start_idx = min(int(slice_start), int(slice_end)) - 1
                        end_idx = max(int(slice_start), int(slice_end)) - 1
                        
                        # 선택한 열 범위를 0번 열부터 재정렬
                        aligned_grid = realign_grid_by_columns(raw_grid, start_idx, end_idx)
                        st.session_state.grid = aligned_grid
                        st.session_state.show_grid = True
                        
                        # 재정렬된 그리드에 대해 변환 및 재구성 수행
                        converted_grid = convert_tie_values(aligned_grid)
                        st.session_state.converted_grid = converted_grid
                        
                        reconstructed_grid = remove_tie_and_reconstruct_grid(aligned_grid)
                        st.session_state.reconstructed_grid = reconstructed_grid
                        
                        st.session_state.selected_cell = None
                        st.success("Successfully parsed the SVG code!")
                    except Exception as e:
                        st.error(f"Error parsing SVG: {str(e)}")
                else:
                    st.warning("Please paste SVG code first")
        
        with col3:
            if st.button("Save Pattern"):
                if st.session_state.show_grid and st.session_state.converted_grid is not None:
                    zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
                    if zones:
                        # Generate unique session ID
                        session_id = str(uuid.uuid4())
                        if save_pattern_analysis(zones, session_id):
                            # Save prediction results to database
                            try:
                                # Collect all prediction results
                                all_prediction_results = []
                                sorted_zones_results = sorted(zones, key=lambda x: x['start_x'])  # Left to right order
                                for zone in sorted_zones_results:
                                    pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
                                    if comparison1_2:
                                        all_prediction_results.append(comparison1_2.upper())
                                    if comparison1_2_3:
                                        all_prediction_results.append(comparison1_2_3.upper())
                                
                                if all_prediction_results:
                                    combined_results = ''.join(all_prediction_results)
                                    
                                    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
                                    conn = sqlite3.connect(db_path)
                                    cursor = conn.cursor()
                                    
                                    # Create table if it doesn't exist
                                    cursor.execute('''
                                        CREATE TABLE IF NOT EXISTS session_prediction_results (
                                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                                            session_id TEXT NOT NULL,
                                            prediction_results TEXT NOT NULL,
                                            created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
                                        )
                                    ''')
                                    
                                    # Insert combined results
                                    cursor.execute('''
                                        INSERT INTO session_prediction_results (session_id, prediction_results)
                                        VALUES (?, ?)
                                    ''', (session_id, combined_results))
                                    
                                    conn.commit()
                            except Exception as e:
                                st.error(f"Error saving prediction results: {str(e)}")
                            finally:
                                if conn:
                                    conn.close()
                            
                            # 새로운 독립적인 함수 호출
                            save_enhanced_prediction_results(zones, session_id)
                            
                            # T-Removed Reconstructed Grid가 존재하면 추가로 저장
                            if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
                                reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
                                if reconstructed_zones:
                                    save_t_removed_reconstructed_results(reconstructed_zones, session_id)
                                    save_t_removed_reconstructed_prediction_results(reconstructed_zones, session_id)
                            
                            st.success("저장 완료!")
                        else:
                            st.error("Failed to save analysis")
                    else:
                        st.warning("저장할 패턴이 없습니다.")
                else:
                    st.warning("먼저 SVG 코드를 파싱해주세요.")
        
        # Display Full Grid if available
        if st.session_state.show_grid and st.session_state.grid is not None:
            display_grid_with_title(st.session_state.grid, "Full Grid")
            
            # Apply T conversion rule
            if st.session_state.converted_grid is None:
                st.session_state.converted_grid = convert_tie_values(st.session_state.grid)
            display_grid_with_title(st.session_state.converted_grid, "Converted Grid")
            
            # Display T-Removed Reconstructed Grid
            if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
                display_grid_with_title(st.session_state.reconstructed_grid, "T-Removed Reconstructed Grid")
                
                # Manual input for T-Removed Reconstructed Grid
                with st.expander("수동 입력 (T-Removed Reconstructed Grid)", expanded=False):
                    # Find all empty cells
                    empty_cells = [(x+1, y+1) for x in range(TABLE_WIDTH) for y in range(TABLE_HEIGHT) if not st.session_state.reconstructed_grid[x][y]]
                    if empty_cells:
                        selected = st.selectbox("비어있는 셀 좌표를 선택하세요 (X, Y)", empty_cells, key="reconstructed_empty_cell_select")
                        st.session_state.reconstructed_selected_cell = selected
                        x, y = selected[0]-1, selected[1]-1
                        st.info(f"선택된 셀: X={x+1}, Y={y+1}")

                        # B/P 버튼 선택 UI
                        colb, colp = st.columns([1,1], gap="large")
                        with colb:
                            if st.button('B', key='reconstructed_bp_btn_b', help='B 선택', use_container_width=True):
                                st.session_state.reconstructed_bp_btn_value = 'B'
                        with colp:
                            if st.button('P', key='reconstructed_bp_btn_p', help='P 선택', use_container_width=True):
                                st.session_state.reconstructed_bp_btn_value = 'P'
                        st.markdown(f'<div style="margin-top:0.5rem;font-size:1.2rem;font-weight:bold;">현재 선택: <span style="color:#1e40af">{st.session_state.reconstructed_bp_btn_value}</span></div>', unsafe_allow_html=True)

                        col_apply, col_undo = st.columns([1,1])
                        with col_apply:
                            if st.button("적용", key="apply_reconstructed_manual"):
                                # Save current grid to history for undo
                                st.session_state.reconstructed_grid_history.append(copy.deepcopy(st.session_state.reconstructed_grid))
                                st.session_state.reconstructed_grid[x][y] = st.session_state.reconstructed_bp_btn_value.lower()
                                st.success(f"({x+1}, {y+1}) 셀을 {st.session_state.reconstructed_bp_btn_value}로 변경했습니다.")
                                st.session_state.reconstructed_selected_cell = None
                                st.rerun()
                        with col_undo:
                            if st.button("되돌리기", key="undo_reconstructed_manual", disabled=len(st.session_state.reconstructed_grid_history) == 0):
                                if st.session_state.reconstructed_grid_history:
                                    st.session_state.reconstructed_grid = st.session_state.reconstructed_grid_history.pop()
                                    st.success("이전 상태로 되돌렸습니다.")
                                    st.session_state.reconstructed_selected_cell = None
                                    st.rerun()
                    else:
                        st.info("비어있는 셀이 없습니다.")
            
            # Manual input for empty cells below the table
            with st.expander("수동 입력 (Converted Grid)", expanded=False):
                # Find all empty cells
                empty_cells = [(x+1, y+1) for x in range(TABLE_WIDTH) for y in range(TABLE_HEIGHT) if not st.session_state.converted_grid[x][y]]
                if empty_cells:
                    selected = st.selectbox("비어있는 셀 좌표를 선택하세요 (X, Y)", empty_cells, key="empty_cell_select")
                    st.session_state.selected_cell = selected
                    x, y = selected[0]-1, selected[1]-1
                    st.info(f"선택된 셀: X={x+1}, Y={y+1}")

                    # B/P 버튼 선택 UI
                    if 'bp_btn_value' not in st.session_state:
                        st.session_state.bp_btn_value = 'B'
                    colb, colp = st.columns([1,1], gap="large")
                    with colb:
                        if st.button('B', key='bp_btn_b', help='B 선택', use_container_width=True):
                            st.session_state.bp_btn_value = 'B'
                    with colp:
                        if st.button('P', key='bp_btn_p', help='P 선택', use_container_width=True):
                            st.session_state.bp_btn_value = 'P'
                    st.markdown(f'<div style="margin-top:0.5rem;font-size:1.2rem;font-weight:bold;">현재 선택: <span style="color:#1e40af">{st.session_state.bp_btn_value}</span></div>', unsafe_allow_html=True)

                    col_apply, col_undo = st.columns([1,1])
                    with col_apply:
                        if st.button("적용", key="apply_manual2"):
                            # Save current grid to history for undo
                            st.session_state.converted_grid_history.append(copy.deepcopy(st.session_state.converted_grid))
                            st.session_state.converted_grid[x][y] = st.session_state.bp_btn_value.lower()
                            st.success(f"({x+1}, {y+1}) 셀을 {st.session_state.bp_btn_value}로 변경했습니다.")
                            st.session_state.selected_cell = None
                            st.rerun()
                    with col_undo:
                        if st.button("되돌리기", key="undo_manual2", disabled=len(st.session_state.converted_grid_history) == 0):
                            if st.session_state.converted_grid_history:
                                st.session_state.converted_grid = st.session_state.converted_grid_history.pop()
                                st.success("이전 상태로 되돌렸습니다.")
                                st.session_state.selected_cell = None
                                st.rerun()
                else:
                    st.info("비어있는 셀이 없습니다.")
            
            # Process T-Removed Reconstructed Grid zones and display pattern analysis (먼저 표시)
            if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
                reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
                if reconstructed_zones:
                    display_pattern_groups_for_reconstructed(reconstructed_zones)
                else:
                    st.info("No zones with relevant data to display for T-Removed Reconstructed Grid.")
            
            # Process zones and display pattern analysis
            zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
            if zones:
                display_pattern_groups(zones)
            else:
                st.info("No zones with relevant data to display.")
    
    # Right column: Group Result
    with right_col:
        # 3행3열 범위 T 개수 표시 (최상단, 오른쪽 정렬)
        if st.session_state.grid is not None:
            display_t_count_3x3(st.session_state.grid)
        
        # Matrix Column Information 표시
        display_matrix_column_info()
        
        # Table Identifier 숨김 처리
        # st.markdown("""
        #     <style>
        #     div[data-testid="stRadio"] {
        #         margin-bottom: 1rem;
        #     }
        #     div[data-testid="stRadio"] > div {
        #         padding: 0.2rem;
        #     }
        #     div[data-testid="stRadio"] > div[role="radiogroup"] > div[role="radio"] {
        #         border: 1px solid #ddd;
        #         border-radius: 4px;
        #         padding: 0.2rem 0.5rem;
        #         margin: 0.1rem;
        #     }
        #     div[data-testid="stRadio"] > div[role="radiogroup"] > div[role="radio"][aria-checked="true"] {
        #         background-color: #1e40af;
        #         color: white;
        #         border-color: #1e40af;
        #     }
        #     </style>
        # """, unsafe_allow_html=True)
        # 
        # st.markdown("#### Table Identifier")
        # options = ['1', '2', '3', '4', '5', '6', '7', '8', '9', '10', '11', '12',
        #           'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'L', 'N',
        #           'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Z']
        # selected = st.radio("Select table identifier", options, horizontal=True)
        # if selected:
        #     st.markdown(f"<div style='margin-top: -0.5rem; margin-bottom: 1rem; color: #1e40af; font-weight: bold;'>Selected: {selected}</div>", unsafe_allow_html=True)
        # st.markdown("---")

        action_left, action_right = st.columns([3, 1])
        with action_left:
            st.markdown("")
        with action_right:
            if st.button("Save Summary", key="save_game_outcome", use_container_width=True):
                converted_grid = st.session_state.converted_grid if st.session_state.show_grid and st.session_state.converted_grid is not None else None
                if not converted_grid:
                    st.warning("저장할 Converted Grid가 없습니다.")
                else:
                    converted_grid_str = serialize_grid_for_storage(converted_grid)
                    reconstructed_grid = st.session_state.reconstructed_grid if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid else None
                    reconstructed_grid_str = serialize_grid_for_storage(reconstructed_grid) if reconstructed_grid else ''

                    converted_zones_for_save = divide_grid_into_overlapping_zones(converted_grid)
                    reconstructed_zones_for_save = divide_grid_into_overlapping_zones_for_reconstructed(reconstructed_grid) if reconstructed_grid else None

                    sequence_results_value = generate_sequence_prediction_results(converted_zones_for_save) if converted_zones_for_save else ''
                    reconstructed_sequence_value = generate_sequence_prediction_results_for_reconstructed(reconstructed_zones_for_save) if reconstructed_zones_for_save else ''
                    reconstructed_gap_value = generate_high_probability_gap_results_for_reconstructed(reconstructed_zones_for_save) if reconstructed_zones_for_save else ''

                    success, saved_session_id = save_game_outcome_summary(
                        converted_grid_str,
                        reconstructed_grid_str,
                        sequence_results_value,
                        reconstructed_sequence_value,
                        reconstructed_gap_value
                    )
                    if success:
                        st.success("Saved")
                    else:
                        st.error("Outcome summary 저장에 실패했습니다.")

        zones = None
        reconstructed_zones = None

        if st.session_state.show_grid and st.session_state.converted_grid is not None:
            zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
            if zones:
                sequence_prediction_results = generate_sequence_prediction_results(zones)
                if sequence_prediction_results:
                    st.markdown("### Sequence Prediction Results")
                    st.markdown(f"**{sequence_prediction_results}**")
                    st.markdown("---")

        if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
            reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
            if reconstructed_zones:
                display_session_prediction_results_for_reconstructed(reconstructed_zones)

        col1, col2, col3, col4 = st.columns([2, 1, 1, 1])
        with col1:
            st.subheader("Group Result")
        with col2:
            # 버튼 높이를 줄이는 CSS 스타일
            st.markdown("""
                <style>
                .stButton > button {
                    height: 2rem;
                    padding: 0.25rem 0.5rem;
                    font-size: 0.8rem;
                    color: black !important;
                    background-color: white !important;
                    border: 1px solid #ddd !important;
                    border-radius: 4px !important;
                    font-weight: bold !important;
                }
                .stButton > button:hover {
                    background-color: #f0f0f0 !important;
                }
                </style>
            """, unsafe_allow_html=True)
            
            if st.button("Pattern1", key="save_pattern1", type="primary"):
                if st.session_state.show_grid and st.session_state.converted_grid is not None:
                    zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
                    if zones:
                        session_id = str(uuid.uuid4())
                        if save_new_pattern_analysis(zones, session_id, 'pattern1'):
                            st.success("Pattern1 분석 결과가 새로운 테이블에 저장되었습니다!")
                        else:
                            st.error("Pattern1 저장에 실패했습니다.")
                    else:
                        st.warning("저장할 패턴이 없습니다.")
                else:
                    st.warning("먼저 SVG 코드를 파싱해주세요.")
        
        with col3:
            if st.button("Pattern2", key="save_pattern2", type="primary"):
                if st.session_state.show_grid and st.session_state.converted_grid is not None:
                    zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
                    if zones:
                        session_id = str(uuid.uuid4())
                        if save_new_pattern_analysis(zones, session_id, 'pattern2'):
                            st.success("Pattern2 분석 결과가 새로운 테이블에 저장되었습니다!")
                        else:
                            st.error("Pattern2 저장에 실패했습니다.")
                    else:
                        st.warning("저장할 패턴이 없습니다.")
                else:
                    st.warning("먼저 SVG 코드를 파싱해주세요.")
        
        with col4:
            if st.button("Pattern3", key="save_pattern3", type="primary"):
                if st.session_state.show_grid and st.session_state.converted_grid is not None:
                    zones = divide_grid_into_overlapping_zones(st.session_state.converted_grid)
                    if zones:
                        session_id = str(uuid.uuid4())
                        if save_new_pattern_analysis(zones, session_id, 'pattern3'):
                            st.success("Pattern3 분석 결과가 새로운 테이블에 저장되었습니다!")
                        else:
                            st.error("Pattern3 저장에 실패했습니다.")
                    else:
                        st.warning("저장할 패턴이 없습니다.")
                else:
                    st.warning("먼저 SVG 코드를 파싱해주세요.")
        
        if zones:
            # Display sequence prediction results using pattern_sequence_prediction table
            sequence_prediction_results = generate_sequence_prediction_results(zones)
            if sequence_prediction_results:
                st.markdown("### Sequence Prediction Results")
                st.markdown(f"**{sequence_prediction_results}**")
                st.markdown("---")

            # Group info display: right to left
            sorted_zones_groups = sorted(zones, key=lambda x: x['start_x'], reverse=True)
            
            # 새로운 독립적인 함수 호출 (숨김 처리)
            # display_enhanced_prediction_results(zones)
            
            # Insert search boxes here
            pattern12_prediction_search_box()
            pattern123_prediction_search_box()
            st.markdown("---")
            
            # Group 1-3 (T-Removed Reconstructed) 독립적으로 표시
            display_group_1_3_independent()
            
            # T-Removed Reconstructed Grid Group Results (다른 그룹들)
            if hasattr(st.session_state, 'reconstructed_grid') and st.session_state.reconstructed_grid:
                reconstructed_zones = divide_grid_into_overlapping_zones_for_reconstructed(st.session_state.reconstructed_grid)
                if reconstructed_zones:
                    display_group_results_for_reconstructed(reconstructed_zones)
                else:
                    st.info("No zones with relevant data to display for T-Removed Reconstructed Grid Group Results.")
            
            # Display individual group results (right to left) - 기존 Group Results
            for zone in sorted_zones_groups:
                group_range = f"{zone['start_x'] + 1}-{zone['end_x'] + 1}"
                pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)

                # Check if there is anything to display for this group
                has_content = any([
                    pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3
                ])
                is_group_1_3 = (zone['start_x'] == 0 and zone['end_x'] == 2)
                if not has_content and not is_group_1_3:
                    continue  # Skip this group if nothing to display and not Group 1-3

                st.markdown(f"#### Group {group_range}")
                # Pattern 1,2 results
                st.text(f"Pattern 1,2 combined: {pattern1_2_combined}")
                if pattern1_2:
                    st.text(f"Pattern 1,2 result: {pattern1_2.upper()}")
                    if prediction1_2:
                        source_emoji_12 = "🗄️" if source1_2 == "DB" else "📄" if source1_2 == "CSV" else "❓"
                        st.text(f"Pattern 1,2 Prediction: {prediction1_2.upper()} (소스: {source_emoji_12} {source1_2})")
                        st.text(f"Pattern 1,2 Prediction Result: {comparison1_2.upper()}")
                    else:
                        st.text("No Pattern 1,2 prediction found")
                # Pattern 1,2,3 results
                st.text(f"Pattern 1,2,3 combined: {pattern1_2_3_combined}")
                if pattern1_2_3:
                    st.text(f"Pattern 1,2,3 result: {pattern1_2_3.upper()}")
                    if prediction1_2_3:
                        source_emoji_123 = "🗄️" if source1_2_3 == "DB" else "📄" if source1_2_3 == "CSV" else "❓"
                        st.text(f"Pattern 1,2,3 Prediction: {prediction1_2_3.upper()} (소스: {source_emoji_123} {source1_2_3})")
                        st.text(f"Pattern 1,2,3 Prediction Result: {comparison1_2_3.upper()}")
                    else:
                        st.text("No Pattern 1,2,3 prediction found")
                    # Pattern 3,4 combined always at the end
                    st.text(f"Pattern 3,4 combined: {pattern3_4_combined}")
                    st.markdown("---")
            
            # 검증 UI 추가
            st.markdown("---")
            add_verification_ui()
            
            # 하이브리드 시스템 테스트 UI 추가
            st.markdown("---")
            add_hybrid_test_ui()
            
            # 중복 데이터 정리 UI 추가
            st.markdown("---")
            add_cleanup_ui()
            


def pattern12_prediction_search_box():
    st.markdown("#### Pattern 1,2 Prediction 검색")
    pattern_input = st.text_input("Pattern 1,2 번호 입력", key="pattern12_search_input")
    if st.button("검색", key="pattern12_search_btn"):
        if pattern_input:
            pred_p, found_p, source_p = get_hybrid_pattern_prediction(pattern_input, "P_Sequence")
            pred_b, found_b, source_b = get_hybrid_pattern_prediction(pattern_input, "B_Sequence")
            
            st.markdown("**P_Sequence 결과:**")
            if found_p:
                source_emoji = "🗄️" if source_p == "DB" else "📄" if source_p == "CSV" else "❓"
                st.success(f"Prediction: {pred_p} (소스: {source_emoji} {source_p})")
            else:
                st.warning("No prediction found for P_Sequence.")
            
            st.markdown("**B_Sequence 결과:**")
            if found_b:
                source_emoji = "🗄️" if source_b == "DB" else "📄" if source_b == "CSV" else "❓"
                st.success(f"Prediction: {pred_b} (소스: {source_emoji} {source_b})")
            else:
                st.warning("No prediction found for B_Sequence.")
        else:
            st.info("패턴 번호를 입력하세요.")

def pattern123_prediction_search_box():
    st.markdown("#### Pattern 1,2,3 Prediction 검색")
    pattern_input = st.text_input("Pattern 1,2,3 번호 입력", key="pattern123_search_input")
    if st.button("검색", key="pattern123_search_btn"):
        if pattern_input:
            pred_p, found_p, source_p = get_hybrid_pattern123_prediction(pattern_input, "P_Sequence")
            pred_b, found_b, source_b = get_hybrid_pattern123_prediction(pattern_input, "B_Sequence")
            
            st.markdown("**P_Sequence 결과:**")
            if found_p:
                source_emoji = "🗄️" if source_p == "DB" else "📄" if source_p == "CSV" else "❓"
                st.success(f"Prediction: {pred_p} (소스: {source_emoji} {source_p})")
            else:
                st.warning("No prediction found for P_Sequence.")
            
            st.markdown("**B_Sequence 결과:**")
            if found_b:
                source_emoji = "🗄️" if source_b == "DB" else "📄" if source_b == "CSV" else "❓"
                st.success(f"Prediction: {pred_b} (소스: {source_emoji} {source_b})")
            else:
                st.warning("No prediction found for B_Sequence.")
        else:
            st.info("패턴 번호를 입력하세요.")

def get_pattern_prediction_from_csv(pattern, sequence_type):
    """Get prediction from CSV file based on pattern and sequence type"""
    try:
        df = pd.read_csv('/Users/tj/test_v3/pattern1_result_v1.csv')
        if sequence_type and pattern:
            # Map sequence type to prediction column
            prediction_col = 'P_Prediction' if sequence_type == 'P_Sequence' else 'B_Prediction'
            # Remove leading zero from 4-digit pattern numbers
            pattern_str = str(pattern).strip()
            if len(pattern_str) == 4 and pattern_str.startswith('0'):
                pattern_str = pattern_str[1:]
            # Find matching pattern in Pattern_Number column
            filtered_df = df[df['Pattern_Number'].astype(str).str.strip() == pattern_str]
            if not filtered_df.empty:
                return filtered_df[prediction_col].iloc[0], True
        return '', False
    except Exception as e:
        st.error(f"Error in get_pattern_prediction_from_csv: {str(e)}")
        return '', False

def get_pattern123_prediction_from_csv(pattern, sequence_type):
    """Get prediction from CSV file based on pattern 1,2,3 and sequence type"""
    try:
        df = pd.read_csv('/Users/tj/test_v3/pattern2_result_v1.csv')
        if sequence_type and pattern:
            # Map sequence type to prediction column
            prediction_col = 'P_Prediction' if sequence_type == 'P_Sequence' else 'B_Prediction'
            # Remove leading zero from 6-digit pattern numbers
            pattern_str = str(pattern).strip()
            if len(pattern_str) == 6 and pattern_str.startswith('0'):
                pattern_str = pattern_str[1:]
            # Find matching pattern in Pattern_Number column
            filtered_df = df[df['Pattern_Number'].astype(str).str.strip() == pattern_str]
            if not filtered_df.empty:
                return filtered_df[prediction_col].iloc[0], True
        return '', False
    except Exception as e:
        st.error(f"Error in get_pattern123_prediction_from_csv: {str(e)}")
        return '', False

def get_hybrid_pattern_prediction(pattern, sequence_type):
    """
    하이브리드 예측 시스템:
    1. DB에서 먼저 검색
    2. DB에 없으면 CSV에서 검색
    3. 둘 다 없으면 기본값 반환
    """
    # 1단계: DB에서 검색
    db_result = get_pattern_prediction_from_db(pattern, sequence_type)
    if db_result and db_result[0]:  # DB에 있으면 사용
        return db_result[0], True, "DB"
    
    # 2단계: CSV에서 검색
    csv_result = get_pattern_prediction_from_csv(pattern, sequence_type)
    if csv_result and csv_result[0]:  # CSV에 있으면 사용
        return csv_result[0], True, "CSV"
    
    # 3단계: 기본값 반환
    return '', False, "NOT_FOUND"

def get_hybrid_pattern123_prediction(pattern, sequence_type):
    """
    하이브리드 예측 시스템 (Pattern 123):
    1. DB에서 먼저 검색
    2. DB에 없으면 CSV에서 검색
    3. 둘 다 없으면 기본값 반환
    """
    # 1단계: DB에서 검색
    db_result = get_pattern123_prediction_from_db(pattern, sequence_type)
    if db_result and db_result[0]:  # DB에 있으면 사용
        return db_result[0], True, "DB"
    
    # 2단계: CSV에서 검색
    csv_result = get_pattern123_prediction_from_csv(pattern, sequence_type)
    if csv_result and csv_result[0]:  # CSV에 있으면 사용
        return csv_result[0], True, "CSV"
    
    # 3단계: 기본값 반환
    return '', False, "NOT_FOUND"

def test_hybrid_prediction_system():
    """하이브리드 예측 시스템 테스트"""
    test_cases = [
        ("0628", "P_Sequence", "pattern12"),
        ("383647", "B_Sequence", "pattern123"),
        ("0101", "P_Sequence", "pattern12"),
        ("010101", "B_Sequence", "pattern123"),
    ]
    
    results = {}
    for pattern, sequence_type, pattern_type in test_cases:
        if pattern_type == "pattern12":
            result, found, source = get_hybrid_pattern_prediction(pattern, sequence_type)
        else:
            result, found, source = get_hybrid_pattern123_prediction(pattern, sequence_type)
        
        results[f"{pattern}_{sequence_type}"] = {
            "pattern": pattern,
            "sequence_type": sequence_type,
            "pattern_type": pattern_type,
            "result": result,
            "found": found,
            "source": source
        }
    
    return results

def add_hybrid_test_ui():
    """하이브리드 시스템 테스트 UI 추가"""
    st.subheader("🔍 하이브리드 예측 시스템 테스트")
    
    if st.button("하이브리드 시스템 테스트 실행"):
        results = test_hybrid_prediction_system()
        
        st.write("### 테스트 결과:")
        for key, data in results.items():
            status_emoji = "✅" if data["found"] else "❌"
            source_emoji = "🗄️" if data["source"] == "DB" else "📄" if data["source"] == "CSV" else "❓"
            
            st.write(f"{status_emoji} **{data['pattern']}** ({data['sequence_type']}) - {data['pattern_type']}")
            st.write(f"   결과: {data['result']} | 소스: {source_emoji} {data['source']}")
            st.write("---")

def add_cleanup_ui():
    """중복 데이터 정리 UI 추가"""
    st.subheader("🧹 중복 데이터 정리")
    
    col1, col2 = st.columns([1, 1])
    
    with col1:
        if st.button("중복 데이터 정리 실행", key="cleanup_btn"):
            result = cleanup_duplicate_data()
            
            if result['success']:
                st.success("중복 데이터 정리 완료!")
                st.write("### 정리 결과:")
                st.write(f"- pattern_analysis: {result['pattern_analysis_count']}개 레코드")
                st.write(f"- session_prediction_results: {result['session_prediction_results_count']}개 레코드")
                st.write(f"- enhanced_prediction_results: {result['enhanced_prediction_results_count']}개 레코드")
            else:
                st.error(f"중복 데이터 정리 실패: {result['error']}")
    
    with col2:
        if st.button("중복 데이터 현황 확인", key="check_duplicates_btn"):
            try:
                db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                
                # 1분 이내 중복 데이터 확인
                cursor.execute("""
                    SELECT prediction_results, COUNT(*) as duplicate_count
                    FROM session_prediction_results 
                    WHERE created_at >= datetime('now', '-1 minute')
                    GROUP BY prediction_results 
                    HAVING COUNT(*) > 1
                    ORDER BY duplicate_count DESC
                """)
                
                duplicates = cursor.fetchall()
                
                if duplicates:
                    st.warning(f"1분 이내 중복 데이터 발견: {len(duplicates)}개 그룹")
                    for prediction_results, count in duplicates:
                        st.write(f"- `{prediction_results}`: {count}개 중복")
                else:
                    st.success("1분 이내 중복 데이터 없음")
                
                # 전체 테이블 레코드 수 확인
                cursor.execute("SELECT COUNT(*) FROM pattern_analysis")
                pattern_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM session_prediction_results")
                session_count = cursor.fetchone()[0]
                
                cursor.execute("SELECT COUNT(*) FROM enhanced_prediction_results")
                enhanced_count = cursor.fetchone()[0]
                
                st.write("### 전체 테이블 현황:")
                st.write(f"- pattern_analysis: {pattern_count}개 레코드")
                st.write(f"- session_prediction_results: {session_count}개 레코드")
                st.write(f"- enhanced_prediction_results: {enhanced_count}개 레코드")
                
            except Exception as e:
                st.error(f"중복 데이터 확인 오류: {str(e)}")
            finally:
                if conn:
                    conn.close()

def display_enhanced_prediction_results(zones):
    """독립적인 예측 결과 표시 함수"""
    if not zones:
        return
    
    # 모든 zone을 왼쪽에서 오른쪽 순서로 정렬
    sorted_zones = sorted(zones, key=lambda x: x['start_x'])
    
    # 모든 예측 결과 수집
    all_enhanced_results = []
    
    for zone in sorted_zones:
        # get_pattern_results 함수로 각 zone의 결과 추출
        pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
        
        # comparison1_2 처리 (CSV 소스일 때 P로 변환)
        if comparison1_2:
            if source1_2 == "CSV":
                enhanced_result = "P"
            else:
                enhanced_result = comparison1_2.upper()
            all_enhanced_results.append(enhanced_result)
        
        # comparison1_2_3 처리 (CSV 소스일 때 P로 변환)
        if comparison1_2_3:
            if source1_2_3 == "CSV":
                enhanced_result = "P"
            else:
                enhanced_result = comparison1_2_3.upper()
            all_enhanced_results.append(enhanced_result)
    
    # 모든 결과를 연결하여 표시
    if all_enhanced_results:
        combined_enhanced_results = ''.join(all_enhanced_results)
        st.markdown("### Enhanced Prediction Results")
        st.markdown(f"**{combined_enhanced_results}**")
        st.markdown("---")


def save_enhanced_prediction_results(zones, session_id):
    """Enhanced Prediction Results를 새로운 테이블에 저장하는 독립적인 함수"""
    if not zones:
        return False
    
    try:
        # 모든 zone을 왼쪽에서 오른쪽 순서로 정렬
        sorted_zones = sorted(zones, key=lambda x: x['start_x'])
        
        # Enhanced Prediction Results 수집
        all_enhanced_results = []
        
        for zone in sorted_zones:
            # get_pattern_results 함수로 각 zone의 결과 추출
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results(zone)
            
            # comparison1_2 처리 (CSV 소스일 때 P로 변환)
            if comparison1_2:
                if source1_2 == "CSV":
                    enhanced_result = "P"
                else:
                    enhanced_result = comparison1_2.upper()
                all_enhanced_results.append(enhanced_result)
            
            # comparison1_2_3 처리 (CSV 소스일 때 P로 변환)
            if comparison1_2_3:
                if source1_2_3 == "CSV":
                    enhanced_result = "P"
                else:
                    enhanced_result = comparison1_2_3.upper()
                all_enhanced_results.append(enhanced_result)
        
        # Enhanced Prediction Results를 DB에 저장
        if all_enhanced_results:
            combined_enhanced_results = ''.join(all_enhanced_results)
            
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # session_prediction_results 테이블 스키마를 참고하여 새로운 테이블 생성
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS enhanced_prediction_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    enhanced_prediction_results TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
                )
            ''')
            
            # Enhanced Prediction Results 삽입
            cursor.execute('''
                INSERT INTO enhanced_prediction_results (session_id, enhanced_prediction_results)
                VALUES (?, ?)
            ''', (session_id, combined_enhanced_results))
            
            conn.commit()
            conn.close()
            return True
        
        return False
        
    except Exception as e:
        st.error(f"Error saving enhanced prediction results: {str(e)}")
        return False

def save_t_removed_reconstructed_results(reconstructed_zones, session_id):
    """T-Removed Reconstructed Grid의 Session Prediction Results를 별도 테이블에 저장"""
    if not reconstructed_zones:
        return False
    
    try:
        # Session Prediction Results 수집 (T-Removed Reconstructed용)
        all_prediction_results = []
        sorted_zones_results = sorted(reconstructed_zones, key=lambda x: x['start_x'])
        
        for zone in sorted_zones_results:
            pattern1_2, pattern1_2_3, prediction1_2, prediction1_2_3, comparison1_2, comparison1_2_3, sequence_type_12, pattern1_2_combined, pattern1_2_3_combined, pattern3_4_combined, source1_2, source1_2_3, sequence_type_123 = get_pattern_results_for_reconstructed(zone)
            if comparison1_2:
                all_prediction_results.append(comparison1_2.upper())
            if comparison1_2_3:
                all_prediction_results.append(comparison1_2_3.upper())
        
        if all_prediction_results:
            combined_results = ''.join(all_prediction_results)
            
            # 새로운 독립적인 테이블에 저장
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 테이블 생성 (없으면)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS t_removed_reconstructed_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    prediction_results TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
                )
            ''')
            
            # T-Removed Reconstructed 결과 삽입
            cursor.execute('''
                INSERT INTO t_removed_reconstructed_results (session_id, prediction_results)
                VALUES (?, ?)
            ''', (session_id, combined_results))
            
            conn.commit()
            conn.close()
            return True
        
        return False
        
    except Exception as e:
        st.error(f"Error saving T-Removed Reconstructed results: {str(e)}")
        return False

def get_pattern_sequence_prediction(first_pattern, sequence_type, fifth_value):
    """pattern_sequence_prediction 테이블에서 예측값 조회"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT occurrence_count, probability
            FROM pattern_sequence_prediction 
            WHERE first_pattern = ? AND sequence_type = ? AND fifth_value = ?
        """, (first_pattern, sequence_type, fifth_value))
        
        result = cursor.fetchone()
        if result:
            occurrence_count, probability = result
            return True, occurrence_count, probability
        return False, 0, 0
        
    except Exception as e:
        st.error(f"패턴 시퀀스 예측 조회 오류: {str(e)}")
        return False, 0, 0
    finally:
        if conn:
            conn.close()

def get_best_prediction_from_sequence_table(first_pattern, sequence_type):
    """해당 패턴과 시퀀스 타입에서 가장 높은 확률의 5번째 값 예측"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        cursor.execute("""
            SELECT fifth_value, occurrence_count, probability, high_probability_gap
            FROM pattern_sequence_prediction 
            WHERE first_pattern = ? AND sequence_type = ?
            ORDER BY probability DESC
            LIMIT 1
        """, (first_pattern, sequence_type))
        
        result = cursor.fetchone()
        if result:
            fifth_value, occurrence_count, probability, high_probability_gap = result
            return fifth_value, True, occurrence_count, probability, high_probability_gap
        return '', False, 0, 0, 0
        
    except Exception as e:
        st.error(f"최적 예측값 조회 오류: {str(e)}")
        return '', False, 0, 0, 0
    finally:
        if conn:
            conn.close()

def get_zone_pattern_sequence_results(zone):
    """새로운 구현: Pattern 1과 Pattern 2 각각 별개로 예측 비교"""
    try:
        # 1. 그룹의 pattern1과 pattern2 number 추출
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            return '', '', '', '', '', '', '', '', '', '', '', '', ''
        
        # 패턴 값 추출
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # 패턴 번호 추출
        pattern_numbers = []
        for v in pattern_values[:4]:
            pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
            pattern_numbers.append(pattern_number if pattern_number is not None else '-')
        
        # Pattern 1, Pattern 2 번호 가져오기
        pattern1_number = pattern_numbers[0] if pattern_numbers[0] != '-' else ''
        pattern2_number = pattern_numbers[1] if len(pattern_numbers) > 1 and pattern_numbers[1] != '-' else ''
        
        # Pattern 번호를 2자리로 포맷 (pattern_sequence_prediction 테이블 조회용)
        def format_pattern_number(pattern_num):
            if pattern_num and len(pattern_num) > 2:
                return pattern_num[:2]
            elif pattern_num and len(pattern_num) == 1:
                return '0' + pattern_num
            else:
                return pattern_num
        
        pattern1_formatted = format_pattern_number(pattern1_number)
        pattern2_formatted = format_pattern_number(pattern2_number)
        
        # 실제 결과 추출
        pattern1_actual_result = zone['zone_data'][2][1] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 1 else ''
        pattern2_actual_result = zone['zone_data'][2][4] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 4 else ''
        
        # 시퀀스 타입 결정 (실제 결과보다 한 칸 위의 값으로 결정)
        pattern1_sequence_value = zone['zone_data'][2][0] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 0 else ''
        pattern2_sequence_value = zone['zone_data'][2][3] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 3 else ''
        
        # 각 패턴별로 독립적인 시퀀스 타입 결정
        sequence_type1 = 'P_Sequence' if pattern1_sequence_value.upper() == 'P' else 'B_Sequence' if pattern1_sequence_value.upper() == 'B' else ''
        sequence_type2 = 'P_Sequence' if pattern2_sequence_value.upper() == 'P' else 'B_Sequence' if pattern2_sequence_value.upper() == 'B' else ''
        
        # Pattern 1 예측 및 비교
        pattern1_prediction = ''
        pattern1_comparison = ''
        pattern1_gap_tf = ''
        if pattern1_formatted and sequence_type1:
            predicted_value, found, _, _, gap = get_best_prediction_from_sequence_table(pattern1_formatted, sequence_type1)
            if found:
                pattern1_prediction = predicted_value
                pattern1_gap_tf = 'T' if gap > 0 else 'F'
                if pattern1_actual_result:
                    pattern1_comparison = 'W' if pattern1_actual_result.upper() == predicted_value.upper() else 'L'
        
        # Pattern 2 예측 및 비교
        pattern2_prediction = ''
        pattern2_comparison = ''
        pattern2_gap_tf = ''
        if pattern2_formatted and sequence_type2:
            predicted_value, found, _, _, gap = get_best_prediction_from_sequence_table(pattern2_formatted, sequence_type2)
            if found:
                pattern2_prediction = predicted_value
                pattern2_gap_tf = 'T' if gap > 0 else 'F'
                if pattern2_actual_result:
                    pattern2_comparison = 'W' if pattern2_actual_result.upper() == predicted_value.upper() else 'L'
        
        # 디버깅용 모든 패턴 정보
        all_pattern_info = f"P1:{pattern_numbers[0]},P2:{pattern_numbers[1]},P3:{pattern_numbers[2]},P4:{pattern_numbers[3] if len(pattern_numbers) > 3 else '-'}"
        
        return (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
                sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
                pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf)
        
    except Exception as e:
        return '', '', '', '', '', '', '', '', '', '', '', '', ''

def generate_sequence_prediction_results(zones):
    """새로운 구현: Pattern 1과 Pattern 2 각각의 W,L 결과를 왼쪽→오른쪽 순서로 연결"""
    try:
        # 4. 1-3 동작을 모든 그룹이 끝날때까지 반복
        # Zone을 왼쪽→오른쪽 순서로 정렬
        sorted_zones = sorted(zones, key=lambda x: x['start_x'])
        
        all_wl_results = []  # W, L 결과만 저장 (Pattern 1, Pattern 2 순서)
        
        for i, zone in enumerate(sorted_zones):
            # 각 그룹에 대해 Pattern 1과 Pattern 2 별개로 처리
            (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
             sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
             pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf) = get_zone_pattern_sequence_results(zone)
            
            # 각 그룹마다 2개의 결과 수집 (Pattern 1, Pattern 2)
            if pattern1_comparison in ['W', 'L']:
                all_wl_results.append(pattern1_comparison)
            if pattern2_comparison in ['W', 'L']:
                all_wl_results.append(pattern2_comparison)
        
        
        # 5. W, L 결과를 왼쪽→오른쪽 순서로 연결
        combined_results = ''.join(all_wl_results)
        return combined_results
        
    except Exception as e:
        st.error(f"시퀀스 예측 결과 생성 오류: {str(e)}")
        return ''

def get_zone_pattern_sequence_results_for_reconstructed(zone):
    """T-Removed Reconstructed Grid 전용: Pattern 1과 Pattern 2 각각 별개로 예측 비교"""
    try:
        # 1. 그룹의 pattern1과 pattern2 number 추출
        patterns = get_pattern_positions()
        group_patterns = [p for p in patterns if p['columns'][0] >= zone['start_x'] and p['columns'][1] <= zone['end_x']]
        
        if len(group_patterns) < 4:
            return '', '', '', '', '', '', '', '', '', '', '', '', ''
        
        # 패턴 값 추출
        pattern_values = []
        for pattern in group_patterns[:4]:
            values = []
            for x, y in pattern['coordinates']:
                relative_x = x - zone['start_x']
                value = zone['zone_data'][relative_x][y]
                if value:
                    values.append(value.upper())
            pattern_values.append(values)
        
        # 패턴 번호 추출
        pattern_numbers = []
        for v in pattern_values[:4]:
            pattern_number = find_pattern_number_only([x.lower() for x in v]) if v else None
            pattern_numbers.append(pattern_number if pattern_number is not None else '-')
        
        # Pattern 1, Pattern 2 번호 가져오기
        pattern1_number = pattern_numbers[0] if pattern_numbers[0] != '-' else ''
        pattern2_number = pattern_numbers[1] if len(pattern_numbers) > 1 and pattern_numbers[1] != '-' else ''
        
        # Pattern 번호를 2자리로 포맷 (pattern_sequence_prediction 테이블 조회용)
        def format_pattern_number(pattern_num):
            if pattern_num and len(pattern_num) > 2:
                return pattern_num[:2]
            elif pattern_num and len(pattern_num) == 1:
                return '0' + pattern_num
            else:
                return pattern_num
        
        pattern1_formatted = format_pattern_number(pattern1_number)
        pattern2_formatted = format_pattern_number(pattern2_number)
        
        # 실제 결과 추출 (T-Removed Reconstructed Grid와 동일한 위치 사용)
        pattern1_actual_result = zone['zone_data'][2][1] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 1 else ''
        pattern2_actual_result = zone['zone_data'][2][4] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 4 else ''
        
        # 시퀀스 타입 결정 (실제 결과보다 한 칸 위의 값으로 결정)
        pattern1_sequence_value = zone['zone_data'][2][0] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 0 else ''
        pattern2_sequence_value = zone['zone_data'][2][3] if len(zone['zone_data']) > 2 and len(zone['zone_data'][2]) > 3 else ''
        
        # 각 패턴별로 독립적인 시퀀스 타입 결정
        sequence_type1 = 'P_Sequence' if pattern1_sequence_value.upper() == 'P' else 'B_Sequence' if pattern1_sequence_value.upper() == 'B' else ''
        sequence_type2 = 'P_Sequence' if pattern2_sequence_value.upper() == 'P' else 'B_Sequence' if pattern2_sequence_value.upper() == 'B' else ''
        
        # Pattern 1 예측 및 비교
        pattern1_prediction = ''
        pattern1_comparison = ''
        pattern1_gap_tf = ''
        if pattern1_formatted and sequence_type1:
            predicted_value, found, _, _, gap = get_best_prediction_from_sequence_table(pattern1_formatted, sequence_type1)
            if found:
                pattern1_prediction = predicted_value
                pattern1_gap_tf = 'T' if gap > 0 else 'F'
                if pattern1_actual_result:
                    pattern1_comparison = 'W' if pattern1_actual_result.upper() == predicted_value.upper() else 'L'
        
        # Pattern 2 예측 및 비교
        pattern2_prediction = ''
        pattern2_comparison = ''
        pattern2_gap_tf = ''
        if pattern2_formatted and sequence_type2:
            predicted_value, found, _, _, gap = get_best_prediction_from_sequence_table(pattern2_formatted, sequence_type2)
            if found:
                pattern2_prediction = predicted_value
                pattern2_gap_tf = 'T' if gap > 0 else 'F'
                if pattern2_actual_result:
                    pattern2_comparison = 'W' if pattern2_actual_result.upper() == predicted_value.upper() else 'L'
        
        # 디버깅용 모든 패턴 정보
        all_pattern_info = f"P1:{pattern_numbers[0]},P2:{pattern_numbers[1]},P3:{pattern_numbers[2]},P4:{pattern_numbers[3] if len(pattern_numbers) > 3 else '-'}"
        
        return (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
                sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
                pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf)
        
    except Exception as e:
        return '', '', '', '', '', '', '', '', '', '', '', '', ''

def generate_sequence_prediction_results_for_reconstructed(zones):
    """T-Removed Reconstructed Grid 전용: Pattern 1과 Pattern 2 각각의 W,L 결과를 왼쪽→오른쪽 순서로 연결"""
    try:
        # Zone을 왼쪽→오른쪽 순서로 정렬
        sorted_zones = sorted(zones, key=lambda x: x['start_x'])
        
        all_wl_results = []  # W, L 결과만 저장 (Pattern 1, Pattern 2 순서)
        
        for i, zone in enumerate(sorted_zones):
            # 각 그룹에 대해 Pattern 1과 Pattern 2 별개로 처리
            (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
             sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
             pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf) = get_zone_pattern_sequence_results_for_reconstructed(zone)
            
            # 각 그룹마다 2개의 결과 수집 (Pattern 1, Pattern 2)
            if pattern1_comparison in ['W', 'L']:
                all_wl_results.append(pattern1_comparison)
            if pattern2_comparison in ['W', 'L']:
                all_wl_results.append(pattern2_comparison)
        
        # 5. W, L 결과를 왼쪽→오른쪽 순서로 연결
        combined_results = ''.join(all_wl_results)
        return combined_results
        
    except Exception as e:
        st.error(f"T-Removed Reconstructed 시퀀스 예측 결과 생성 오류: {str(e)}")
        return ''

def generate_high_probability_gap_results_for_reconstructed(zones):
    """T-Removed Reconstructed Grid 전용: high_probability_gap 값을 T/F로 표시"""
    try:
        # Zone을 왼쪽→오른쪽 순서로 정렬
        sorted_zones = sorted(zones, key=lambda x: x['start_x'])
        
        all_gap_results = []  # T, F 결과만 저장 (Pattern 1, Pattern 2 순서)
        
        for i, zone in enumerate(sorted_zones):
            # 각 그룹에 대해 Pattern 1과 Pattern 2 별개로 처리
            (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
             sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
             pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf) = get_zone_pattern_sequence_results_for_reconstructed(zone)
            
            # 각 그룹마다 2개의 gap 결과 수집 (Pattern 1, Pattern 2)
            if pattern1_gap_tf in ['T', 'F']:
                all_gap_results.append(pattern1_gap_tf)
            if pattern2_gap_tf in ['T', 'F']:
                all_gap_results.append(pattern2_gap_tf)
        
        # T, F 결과를 왼쪽→오른쪽 순서로 연결
        combined_gap_results = ''.join(all_gap_results)
        return combined_gap_results
        
    except Exception as e:
        st.error(f"T-Removed Reconstructed high_probability_gap 결과 생성 오류: {str(e)}")
        return ''

def generate_high_probability_gap_comparison_results_for_reconstructed(zones):
    """T-Removed Reconstructed Grid 전용: High Probability Gap Results와 Sequence Prediction Results 비교하여 P/F 표시"""
    try:
        # Zone을 왼쪽→오른쪽 순서로 정렬
        sorted_zones = sorted(zones, key=lambda x: x['start_x'])
        
        all_gap_results = []  # T, F 결과만 저장 (Pattern 1, Pattern 2 순서)
        all_sequence_results = []  # W, L 결과만 저장 (Pattern 1, Pattern 2 순서)
        
        for i, zone in enumerate(sorted_zones):
            # 각 그룹에 대해 Pattern 1과 Pattern 2 별개로 처리
            (pattern1_actual_result, pattern2_actual_result, pattern1_formatted, pattern2_formatted, 
             sequence_type1, sequence_type2, pattern1_prediction, pattern2_prediction, pattern1_comparison, 
             pattern2_comparison, all_pattern_info, pattern1_gap_tf, pattern2_gap_tf) = get_zone_pattern_sequence_results_for_reconstructed(zone)
            
            # 각 그룹마다 2개의 gap 결과 수집 (Pattern 1, Pattern 2)
            if pattern1_gap_tf in ['T', 'F']:
                all_gap_results.append(pattern1_gap_tf)
            if pattern2_gap_tf in ['T', 'F']:
                all_gap_results.append(pattern2_gap_tf)
            
            # 각 그룹마다 2개의 sequence 결과 수집 (Pattern 1, Pattern 2)
            if pattern1_comparison in ['W', 'L']:
                all_sequence_results.append(pattern1_comparison)
            if pattern2_comparison in ['W', 'L']:
                all_sequence_results.append(pattern2_comparison)
        
        # 비교 결과 생성
        comparison_results = []
        min_length = min(len(all_gap_results), len(all_sequence_results))
        
        for i in range(min_length):
            gap_value = all_gap_results[i]
            sequence_value = all_sequence_results[i]
            
            if gap_value == 'F':
                comparison_results.append('X')
            elif gap_value == 'T':
                if sequence_value == 'W':
                    comparison_results.append('P')
                elif sequence_value == 'L':
                    comparison_results.append('F')
                else:
                    comparison_results.append('X')  # sequence 값이 없는 경우
            else:
                comparison_results.append('X')  # gap 값이 없는 경우
        
        # 비교 결과를 왼쪽→오른쪽 순서로 연결
        combined_comparison_results = ''.join(comparison_results)
        return combined_comparison_results
        
    except Exception as e:
        st.error(f"T-Removed Reconstructed high_probability_gap 비교 결과 생성 오류: {str(e)}")
        return ''

def save_t_removed_reconstructed_prediction_results(reconstructed_zones, session_id):
    """T-Removed Reconstructed Grid의 Sequence Prediction Results와 High Probability Gap Results를 독립적으로 저장"""
    if not reconstructed_zones:
        return False
    
    try:
        # 1. Sequence Prediction Results 생성
        sequence_prediction_results = generate_sequence_prediction_results_for_reconstructed(reconstructed_zones)
        
        # 2. High Probability Gap Results 생성
        high_probability_gap_results = generate_high_probability_gap_results_for_reconstructed(reconstructed_zones)
        
        # 3. High Probability Gap Comparison Results 생성
        high_probability_gap_comparison_results = generate_high_probability_gap_comparison_results_for_reconstructed(reconstructed_zones)
        
        # 4. 둘 다 결과가 있을 때만 저장
        if sequence_prediction_results and high_probability_gap_results:
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            
            # 테이블 생성 (없으면)
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS t_removed_reconstructed_prediction_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    sequence_prediction_results TEXT NOT NULL,
                    high_probability_gap_results TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            
            # 인덱스 생성 (없으면)
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_t_removed_reconstructed_session_id 
                ON t_removed_reconstructed_prediction_results(session_id)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_t_removed_reconstructed_created_at 
                ON t_removed_reconstructed_prediction_results(created_at DESC)
            ''')
            
            # 결과 삽입
            cursor.execute('''
                INSERT INTO t_removed_reconstructed_prediction_results 
                (session_id, sequence_prediction_results, high_probability_gap_results, high_probability_gap_comparison_results)
                VALUES (?, ?, ?, ?)
            ''', (session_id, sequence_prediction_results, high_probability_gap_results, high_probability_gap_comparison_results))
            
            conn.commit()
            conn.close()
            return True
        
        return False
        
    except Exception as e:
        st.error(f"Error saving T-Removed Reconstructed prediction results: {str(e)}")
        return False

def cleanup_duplicate_data():
    """중복 데이터 정리 함수 - 1분 이내 생성된 중복 데이터 제거"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        # 1. 백업 생성
        cursor.execute("CREATE TABLE IF NOT EXISTS pattern_analysis_backup AS SELECT * FROM pattern_analysis")
        cursor.execute("CREATE TABLE IF NOT EXISTS session_prediction_results_backup AS SELECT * FROM session_prediction_results")
        cursor.execute("CREATE TABLE IF NOT EXISTS enhanced_prediction_results_backup AS SELECT * FROM enhanced_prediction_results")
        
        # 2. 중복 데이터 식별 및 삭제
        cursor.execute("""
            WITH duplicates AS (
                SELECT prediction_results
                FROM session_prediction_results 
                WHERE created_at >= datetime('now', '-1 minute')
                GROUP BY prediction_results 
                HAVING COUNT(*) > 1
            ),
            to_delete AS (
                SELECT session_id
                FROM session_prediction_results spr
                INNER JOIN duplicates d ON spr.prediction_results = d.prediction_results
                WHERE session_id NOT IN (
                    SELECT session_id
                    FROM (
                        SELECT session_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY prediction_results 
                                   ORDER BY created_at DESC
                               ) as rn
                        FROM session_prediction_results
                        WHERE prediction_results IN (SELECT prediction_results FROM duplicates)
                    ) ranked
                    WHERE rn = 1
                )
            )
            DELETE FROM session_prediction_results WHERE session_id IN (SELECT session_id FROM to_delete)
        """)
        
        # 3. pattern_analysis 테이블에서 중복 제거
        cursor.execute("""
            DELETE FROM pattern_analysis 
            WHERE session_id NOT IN (SELECT session_id FROM session_prediction_results)
        """)
        
        # 4. enhanced_prediction_results 테이블에서 중복 제거
        cursor.execute("""
            DELETE FROM enhanced_prediction_results 
            WHERE session_id NOT IN (SELECT session_id FROM session_prediction_results)
        """)
        
        conn.commit()
        
        # 5. 정리 결과 확인
        cursor.execute("SELECT COUNT(*) FROM pattern_analysis")
        pattern_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM session_prediction_results")
        session_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM enhanced_prediction_results")
        enhanced_count = cursor.fetchone()[0]
        
        return {
            'success': True,
            'pattern_analysis_count': pattern_count,
            'session_prediction_results_count': session_count,
            'enhanced_prediction_results_count': enhanced_count
        }
        
    except Exception as e:
        st.error(f"중복 데이터 정리 오류: {str(e)}")
        return {'success': False, 'error': str(e)}
    finally:
        if conn:
            conn.close()


if __name__ == "__main__":
    main() 