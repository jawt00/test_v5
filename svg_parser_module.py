"""
SVG 파싱 모듈
독립적으로 사용 가능한 SVG 파싱 및 데이터베이스 저장 기능
"""

import sqlite3
import os
import uuid
import time
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
    - 2025-01-XX: rU_rX → rf_ri, rU_pa → rf_qW, rU_rZ → rf_rk (최신 구조)
    - 이전: rg_rj → qz_qC (메인 컨테이너)
    - 이전: rg_qu → qz_pO (행)
    - 이전: rg_rl → qz_qF (셀)
    
    다음 변경 시 이 부분만 수정하면 됩니다:
    - main_container = soup.find('div', class_='rf_ri')
    - rows = main_container.find_all('div', class_='rf_qW')
    - cells = row.find_all('div', class_='rf_rk')
    """
    soup = BeautifulSoup(svg_code, 'html.parser')
    # 그리드 초기화: 각 셀을 명시적으로 빈 문자열로 초기화
    grid = [['' for _ in range(TABLE_HEIGHT)] for _ in range(TABLE_WIDTH)]
    
    # [유지보수] 클래스명 변경 시 이 부분만 수정
    main_container = soup.find('div', class_='rf_ri')
    if not main_container:
        return grid
    
    # [유지보수] 클래스명 변경 시 이 부분만 수정
    rows = main_container.find_all('div', class_='rf_qW')
    
    for row_idx, row in enumerate(rows):
        if row_idx >= TABLE_HEIGHT:
            break
        
        # [유지보수] 클래스명 변경 시 이 부분만 수정
        cells = row.find_all('div', class_='rf_rk')
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
    """데이터베이스 연결 (타임아웃 설정으로 락 방지)"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)
        # 디렉토리가 없으면 생성
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        # SQLite는 파일이 없으면 자동으로 생성하므로 존재 여부 확인 불필요
        # timeout=20: 락 대기 시간 20초 (기본값은 무한대기)
        # check_same_thread=False: 멀티스레드 환경에서 사용 가능
        conn = sqlite3.connect(db_path, timeout=20.0, check_same_thread=False)
        # WAL 모드 활성화로 동시성 향상 (선택사항)
        conn.execute('PRAGMA journal_mode=WAL;')
        return conn
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
        
        # grid_string에 UNIQUE 제약 추가 (기존 테이블에도 적용 가능)
        cursor.execute('''
            CREATE UNIQUE INDEX IF NOT EXISTS idx_grid_string_unique 
            ON preprocessed_grid_strings(grid_string)
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


def generate_and_save_ngram_chunks(grid_string_id, grid_string, window_sizes=[5, 6, 7, 8, 9], conn=None):
    """
    grid_string에서 ngram_chunks를 생성하여 DB에 저장
    
    Args:
        grid_string_id: preprocessed_grid_strings 테이블의 id
        grid_string: 처리할 grid string
        window_sizes: 생성할 윈도우 크기 리스트
        conn: 기존 데이터베이스 연결 (None이면 새로 생성)
    
    Returns:
        dict: {window_size: chunk_count}
    """
    # 기존 연결이 있으면 사용, 없으면 새로 생성
    should_close_conn = False
    if conn is None:
        conn = get_db_connection()
        should_close_conn = True
    
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
        # 새로 생성한 연결만 닫기
        if should_close_conn:
            conn.close()


# ============================================================================
# Change-point Detection 기반 N-gram 생성 함수들
# ============================================================================

# Change-point Detection용 DB 경로
CHANGE_POINT_DB_PATH = os.path.join('change_point', 'change_point_ngram.db')

def get_change_point_db_connection():
    """Change-point Detection용 데이터베이스 연결 (타임아웃 설정으로 락 방지)"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), CHANGE_POINT_DB_PATH)
        # 디렉토리가 없으면 생성
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        # SQLite는 파일이 없으면 자동으로 생성하므로 존재 여부 확인 불필요
        # timeout=20: 락 대기 시간 20초 (기본값은 무한대기)
        # check_same_thread=False: 멀티스레드 환경에서 사용 가능
        conn = sqlite3.connect(db_path, timeout=20.0, check_same_thread=False)
        # WAL 모드 활성화로 동시성 향상 (선택사항)
        conn.execute('PRAGMA journal_mode=WAL;')
        return conn
    except Exception as e:
        raise Exception(f"Change-point 데이터베이스 연결 실패: {str(e)} (경로: {db_path})")


def create_change_point_preprocessed_grid_strings_table():
    """Change-point Detection용 preprocessed_grid_strings 테이블 생성"""
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS preprocessed_grid_strings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_string TEXT NOT NULL UNIQUE,
                source_session_id TEXT,
                source_id TEXT,
                string_length INTEGER NOT NULL,
                b_count INTEGER NOT NULL DEFAULT 0,
                p_count INTEGER NOT NULL DEFAULT 0,
                t_count INTEGER NOT NULL DEFAULT 0,
                b_ratio REAL NOT NULL DEFAULT 0.0,
                p_ratio REAL NOT NULL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_string_length 
            ON preprocessed_grid_strings(string_length)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_string_created 
            ON preprocessed_grid_strings(created_at)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise Exception(f"테이블 생성 실패: {str(e)}")
    finally:
        conn.close()


def create_change_point_ngram_chunks_table():
    """Change-point Detection용 ngram_chunks_change_point 테이블 생성"""
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS ngram_chunks_change_point (
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
            ON ngram_chunks_change_point(grid_string_id, window_size)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_prefix_window 
            ON ngram_chunks_change_point(prefix, window_size)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_window_size 
            ON ngram_chunks_change_point(window_size)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        raise Exception(f"테이블 생성 실패: {str(e)}")
    finally:
        conn.close()


def _save_to_change_point_db(grid_string, source_session_id=None, source_id=None):
    """
    Change-point DB에 grid_string 저장 (내부 함수)
    중복된 경우 기존 레코드 ID 반환
    """
    try:
        create_change_point_preprocessed_grid_strings_table()
        conn = get_change_point_db_connection()
        cursor = conn.cursor()
        
        # 통계 계산
        string_length = len(grid_string)
        b_count = grid_string.count('b')
        p_count = grid_string.count('p')
        t_count = grid_string.count('t')
        b_ratio = (b_count / string_length * 100) if string_length > 0 else 0.0
        p_ratio = (p_count / string_length * 100) if string_length > 0 else 0.0
        
        # 저장
        cursor.execute('''
            INSERT OR IGNORE INTO preprocessed_grid_strings (
                grid_string, source_session_id, source_id, string_length, 
                b_count, p_count, t_count, b_ratio, p_ratio
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (grid_string, source_session_id, source_id, string_length, 
              b_count, p_count, t_count, b_ratio, p_ratio))
        
        # INSERT OR IGNORE의 경우, 중복이면 rowcount가 0이 됨
        if cursor.rowcount == 0:
            # 중복된 경우 기존 레코드의 id를 조회
            cursor.execute('''
                SELECT id FROM preprocessed_grid_strings 
                WHERE grid_string = ?
            ''', (grid_string,))
            result = cursor.fetchone()
            if result:
                record_id = result[0]
                conn.commit()
                conn.close()
                return record_id
            else:
                raise Exception("중복 저장 시도했으나 기존 레코드를 찾을 수 없습니다.")
        
        record_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return record_id
        
    except Exception as e:
        if 'conn' in locals():
            try:
                conn.rollback()
                conn.close()
            except:
                pass
        raise Exception(f"Change-point DB 저장 오류: {str(e)}")


def generate_and_save_ngram_chunks_change_point(grid_string_id, grid_string, window_sizes=[5, 6, 7, 8, 9], conn=None):
    """
    Change-point Detection 기반으로 ngram_chunks를 생성하여 새로운 DB에 저장
    
    Args:
        grid_string_id: preprocessed_grid_strings 테이블의 id (새로운 DB)
        grid_string: 처리할 grid string
        window_sizes: 생성할 윈도우 크기 리스트
        conn: 기존 데이터베이스 연결 (None이면 새로 생성, change_point_ngram.db 사용)
    
    Returns:
        dict: {window_size: chunk_count}
    """
    # 기존 연결이 있으면 사용, 없으면 새로 생성
    should_close_conn = False
    if conn is None:
        conn = get_change_point_db_connection()
        should_close_conn = True
    
    cursor = conn.cursor()
    result = {}
    
    try:
        # 1. 앵커 위치 수집 (변화점 감지)
        anchors = []
        for i in range(len(grid_string) - 1):
            if grid_string[i] != grid_string[i+1]:
                # 변화점 감지 시 이전 위치(i)를 앵커로 추가
                anchors.append(i)
        
        # 중복 제거 (정렬되어 있으므로 set 사용 가능)
        anchors = sorted(list(set(anchors)))
        
        # 2. 각 윈도우 크기에 대해 N-gram 생성
        for window_size in window_sizes:
            if len(grid_string) < window_size:
                result[window_size] = 0
                continue
            
            chunk_count = 0
            # 앵커 리스트를 순회하며 ngram 생성
            for anchor in anchors:
                # 앵커 위치에서 window_size만큼 추출 가능한지 확인
                if anchor + window_size <= len(grid_string):
                    chunk = grid_string[anchor:anchor + window_size]
                    prefix = chunk[:-1]
                    suffix = chunk[-1]
                    
                    # DB에 저장 (중복 방지)
                    cursor.execute('''
                        INSERT OR IGNORE INTO ngram_chunks_change_point (
                            grid_string_id, window_size, chunk_index,
                            prefix, suffix, full_chunk
                        ) VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        grid_string_id,
                        window_size,
                        anchor,
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
        # 새로 생성한 연결만 닫기
        if should_close_conn:
            conn.close()


def save_parsed_grid_string_to_db(grid_string, source_session_id=None, source_id=None, max_retries=3):
    """
    파싱된 grid_string을 preprocessed_grid_strings 테이블에 저장
    그리고 ngram_chunks도 자동으로 생성하여 저장
    중복된 grid_string인 경우 기존 레코드를 반환하고 새로 저장하지 않음 (DB 레벨 중복 방지)
    
    Args:
        grid_string: 저장할 grid string
        source_session_id: 소스 세션 ID (None이면 고유한 값 생성)
        source_id: 소스 ID (None이면 UUID 생성)
        max_retries: 최대 재시도 횟수 (기본값: 3)
    
    Returns:
        int: 저장된 레코드의 id (중복인 경우 기존 레코드의 id)
    """
    # 테이블 생성 확인
    create_preprocessed_grid_strings_table()
    create_ngram_chunks_table()
    
    # 재시도 로직
    for attempt in range(max_retries):
        conn = None
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            
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
            
            # grid_string 저장 (중복 시 무시)
            cursor.execute('''
                INSERT OR IGNORE INTO preprocessed_grid_strings (
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
            
            # INSERT OR IGNORE의 경우, 중복이면 rowcount가 0이 됨
            if cursor.rowcount == 0:
                # 중복된 경우 기존 레코드의 id를 조회
                cursor.execute('''
                    SELECT id FROM preprocessed_grid_strings 
                    WHERE grid_string = ?
                ''', (grid_string,))
                result = cursor.fetchone()
                if result:
                    record_id = result[0]
                    # 이미 저장된 레코드이므로 ngram_chunks는 생성하지 않음
                    conn.commit()
                    
                    # Change-point DB에도 동기화 (중복 체크)
                    try:
                        change_point_record_id = _save_to_change_point_db(
                            grid_string, source_session_id, source_id
                        )
                        # Change-point DB의 ngram_chunks_change_point도 생성 (이미 저장된 경우는 생성 안 함)
                        if change_point_record_id:
                            try:
                                # 기존 레코드인지 확인 (새로 저장된 경우만 생성)
                                cp_conn = get_change_point_db_connection()
                                cp_cursor = cp_conn.cursor()
                                cp_cursor.execute('''
                                    SELECT COUNT(*) FROM ngram_chunks_change_point 
                                    WHERE grid_string_id = ?
                                ''', (change_point_record_id,))
                                count = cp_cursor.fetchone()[0]
                                cp_conn.close()
                                
                                if count == 0:
                                    # ngram_chunks_change_point가 없으면 생성
                                    generate_and_save_ngram_chunks_change_point(
                                        change_point_record_id, grid_string
                                    )
                            except Exception as cp_ngram_error:
                                import warnings
                                warnings.warn(f"ngram_chunks_change_point 생성 중 오류 발생: {str(cp_ngram_error)}")
                    except Exception as cp_error:
                        import warnings
                        warnings.warn(f"Change-point DB 동기화 중 오류 발생: {str(cp_error)}")
                    
                    return record_id
                else:
                    # 예상치 못한 상황
                    raise Exception("중복 저장 시도했으나 기존 레코드를 찾을 수 없습니다.")
            
            record_id = cursor.lastrowid
            conn.commit()
            
            # ngram_chunks 생성 및 저장 (새로 저장된 경우에만)
            # 같은 연결을 재사용하여 락 방지
            try:
                generate_and_save_ngram_chunks(record_id, grid_string, conn=conn)
            except Exception as ngram_error:
                # ngram_chunks 생성 실패해도 레코드는 저장되었으므로 경고만
                import warnings
                warnings.warn(f"ngram_chunks 생성 중 오류 발생 (레코드는 저장됨): {str(ngram_error)}")
            
            # Change-point DB에도 저장 (두 DB에 동기화)
            try:
                change_point_record_id = _save_to_change_point_db(
                    grid_string, source_session_id, source_id
                )
                # Change-point DB의 ngram_chunks_change_point도 생성
                if change_point_record_id:
                    try:
                        generate_and_save_ngram_chunks_change_point(
                            change_point_record_id, grid_string
                        )
                    except Exception as cp_ngram_error:
                        import warnings
                        warnings.warn(f"ngram_chunks_change_point 생성 중 오류 발생: {str(cp_ngram_error)}")
            except Exception as cp_error:
                # Change-point DB 저장 실패해도 기존 저장은 성공했으므로 경고만
                import warnings
                warnings.warn(f"Change-point DB 저장 중 오류 발생 (기존 DB는 저장됨): {str(cp_error)}")
            
            return record_id
            
        except sqlite3.OperationalError as e:
            # 데이터베이스 락 오류인 경우 재시도
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
                try:
                    conn.close()
                except:
                    pass
            
            if "database is locked" in str(e).lower() and attempt < max_retries - 1:
                # 재시도 전에 짧은 대기 시간
                wait_time = (attempt + 1) * 0.5  # 0.5초, 1초, 1.5초...
                time.sleep(wait_time)
                continue  # 재시도
            else:
                # 최대 재시도 횟수 초과 또는 다른 오류
                raise e
                
        except sqlite3.IntegrityError as e:
            # UNIQUE 제약 위반 (혹시 모를 경우를 대비)
            if conn:
                try:
                    conn.rollback()
                    # 기존 레코드 조회
                    cursor.execute('''
                        SELECT id FROM preprocessed_grid_strings 
                        WHERE grid_string = ?
                    ''', (grid_string,))
                    result = cursor.fetchone()
                    if result:
                        return result[0]
                except:
                    pass
            raise e
            
        except Exception as e:
            if conn:
                try:
                    conn.rollback()
                except:
                    pass
            raise e
            
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass

