"""
SVG 파싱 모듈
독립적으로 사용 가능한 SVG 파싱 및 데이터베이스 저장 기능
"""

import sqlite3
import os
import uuid
from datetime import datetime
from bs4 import BeautifulSoup

# Table dimensions
TABLE_WIDTH = 15
TABLE_HEIGHT = 6

# DB 경로 (hypothesis_validation_app.py와 동일한 DB 사용)
DB_PATH = 'hypothesis_validation.db'


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
    - 2025-01-XX: qR_qU → oX_o1, qR_qY → oX_pa, qR_qZ → oX_pb (최신 구조)
    - 2025-01-XX: oX_o1 → rU_rX, oX_pa → rU_pa, oX_pb → rU_rZ (최신 구조)
    - 이전: rg_rj → qz_qC (메인 컨테이너)
    - 이전: rg_qu → qz_pO (행)
    - 이전: rg_rl → qz_qF (셀)
    
    다음 변경 시 이 부분만 수정하면 됩니다:
    - main_container = soup.find('div', class_='rU_rX')
    - rows = main_container.find_all('div', class_='rU_pa')
    - cells = row.find_all('div', class_='rU_rZ')
    """
    soup = BeautifulSoup(svg_code, 'html.parser')
    # 그리드 초기화: 각 셀을 명시적으로 빈 문자열로 초기화
    grid = [['' for _ in range(TABLE_HEIGHT)] for _ in range(TABLE_WIDTH)]
    
    # [유지보수] 클래스명 변경 시 이 부분만 수정
    main_container = soup.find('div', class_='rU_rX')
    if not main_container:
        return grid
    
    # [유지보수] 클래스명 변경 시 이 부분만 수정
    rows = main_container.find_all('div', class_='rU_pa')
    
    for row_idx, row in enumerate(rows):
        if row_idx >= TABLE_HEIGHT:
            break
        
        # [유지보수] 클래스명 변경 시 이 부분만 수정
        cells = row.find_all('div', class_='rU_rZ')
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
            
            # 값 설정 (빈 값이 아닐 때만)
            if result:
                grid[col_idx][row_idx] = result
    
    return grid


def grid_to_string_column_wise(grid):
    """
    Grid를 열 우선 순서로 문자열로 변환
    
    Args:
        grid: 2D 리스트 (15x6)
    
    Returns:
        str: 열 우선 순서로 변환된 문자열
    """
    if not grid:
        return ''
    
    result = []
    for x in range(TABLE_WIDTH):  # 열 우선 순회 (0~14)
        for y in range(TABLE_HEIGHT):  # 각 열의 행 순회 (0~5)
            cell_value = grid[x][y]
            # 빈 문자열, None, 't' 제외하고 유효한 값만 추가
            if cell_value and str(cell_value).strip() != '' and str(cell_value).lower() != 't':
                result.append(str(cell_value).lower())
    
    return ''.join(result)


def get_db_connection():
    """데이터베이스 연결"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)
        # 디렉토리가 없으면 생성
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        # SQLite는 파일이 없으면 자동으로 생성하므로 존재 여부 확인 불필요
        return sqlite3.connect(db_path)
    except Exception as e:
        raise Exception(f"데이터베이스 연결 실패: {str(e)} (경로: {db_path})")


def create_preprocessed_grid_strings_table():
    """preprocessed_grid_strings 테이블 생성"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS preprocessed_grid_strings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_session_id TEXT UNIQUE,
                source_id TEXT,
                grid_string TEXT NOT NULL,
                string_length INTEGER NOT NULL,
                b_count INTEGER NOT NULL,
                p_count INTEGER NOT NULL,
                b_ratio REAL,
                p_ratio REAL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                processed_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_source_session_id 
            ON preprocessed_grid_strings(source_session_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_created_at 
            ON preprocessed_grid_strings(created_at)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise Exception(f"테이블 생성 실패: {str(e)}")
    finally:
        conn.close()


def create_ngram_chunks_table():
    """ngram_chunks 테이블 생성"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ngram_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_string_id INTEGER NOT NULL,
                window_size INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                suffix TEXT NOT NULL,
                full_chunk TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id),
                UNIQUE(grid_string_id, window_size, chunk_index)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_string_window 
            ON ngram_chunks(grid_string_id, window_size)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prefix_window 
            ON ngram_chunks(prefix, window_size)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_window_size 
            ON ngram_chunks(window_size)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise Exception(f"테이블 생성 실패: {str(e)}")
    finally:
        conn.close()


def generate_and_save_ngram_chunks(grid_string_id, grid_string, window_sizes=[5, 6, 7, 8, 9]):
    """
    grid_string에서 ngram_chunks를 생성하여 DB에 저장
    
    Args:
        grid_string_id: preprocessed_grid_strings 테이블의 id
        grid_string: 처리할 grid string
        window_sizes: 생성할 윈도우 크기 리스트
    
    Returns:
        dict: {window_size: chunk_count}
    """
    conn = get_db_connection()
    
    cursor = conn.cursor()
    result = {}
    
    try:
        for window_size in window_sizes:
            if len(grid_string) < window_size:
                result[window_size] = 0
                continue
            
            chunk_count = 0
            # N-gram 청크 생성
            for i in range(len(grid_string) - window_size + 1):
                chunk = grid_string[i:i + window_size]
                prefix = chunk[:-1]
                suffix = chunk[-1]
                
                # DB에 저장 (중복 방지)
                cursor.execute('''
                    INSERT OR IGNORE INTO ngram_chunks (
                        grid_string_id, window_size, chunk_index,
                        prefix, suffix, full_chunk
                    ) VALUES (?, ?, ?, ?, ?, ?)
                ''', (
                    grid_string_id,
                    window_size,
                    i,
                    prefix,
                    suffix,
                    chunk
                ))
                
                if cursor.rowcount > 0:
                    chunk_count += 1
            
            result[window_size] = chunk_count
        
        conn.commit()
        return result
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()


def save_parsed_grid_string_to_db(grid_string, source_session_id=None, source_id=None):
    """
    파싱된 grid_string을 preprocessed_grid_strings 테이블에 저장
    그리고 ngram_chunks도 자동으로 생성하여 저장
    
    Args:
        grid_string: 저장할 grid string
        source_session_id: 소스 세션 ID (None이면 고유한 값 생성)
        source_id: 소스 ID (None이면 UUID 생성)
    
    Returns:
        int: 저장된 레코드의 id
    """
    # 테이블 생성 확인
    create_preprocessed_grid_strings_table()
    create_ngram_chunks_table()
    
    conn = get_db_connection()
    
    cursor = conn.cursor()
    
    try:
        b_count = grid_string.count('b')
        p_count = grid_string.count('p')
        string_length = len(grid_string)
        
        # source_session_id가 None이면 고유한 값 생성 (UNIQUE 제약조건 때문에)
        if source_session_id is None:
            # 타임스탬프와 UUID를 조합하여 고유한 값 생성
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            unique_id = str(uuid.uuid4())[:8]
            source_session_id = f'manual_svg_parse_{timestamp}_{unique_id}'
        
        # source_id가 None이면 UUID 생성
        if source_id is None:
            source_id = str(uuid.uuid4())
        
        # grid_string 저장
        cursor.execute('''
            INSERT INTO preprocessed_grid_strings (
                source_session_id, source_id, grid_string,
                string_length, b_count, p_count, b_ratio, p_ratio,
                created_at, processed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'), datetime('now', '+9 hours'))
        ''', (
            source_session_id,
            source_id,
            grid_string,
            string_length,
            b_count,
            p_count,
            (b_count / string_length * 100) if string_length > 0 else 0,
            (p_count / string_length * 100) if string_length > 0 else 0
        ))
        
        record_id = cursor.lastrowid
        conn.commit()
        
        # ngram_chunks 생성 및 저장
        generate_and_save_ngram_chunks(record_id, grid_string)
        
        return record_id
        
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

