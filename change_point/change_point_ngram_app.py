"""
Change-point Detection 기반 N-gram 생성 앱
변화점 탐지를 통한 N-gram 생성 및 관리
"""

import streamlit as st
import pandas as pd
import os
import sys
import time

# 상위 디렉토리의 모듈 import를 위한 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import svg_parser_module as _svg_parser
from svg_parser_module import (
    parse_bead_road_svg,
    grid_to_string_column_wise,
    get_change_point_db_connection,
    create_change_point_preprocessed_grid_strings_table,
    create_change_point_ngram_chunks_table,
    generate_and_save_ngram_chunks_change_point,
    TABLE_WIDTH,
    TABLE_HEIGHT,
)
# 파싱에 사용하는 메인 컨테이너 클래스명 (모듈 상수와 동기화, 없으면 기본값)
# [유지보수] 클래스명 변경 시 가이드 8번과 함께 fallback 값 수정 (콘솔 복사 명령에 사용)
BEAD_ROAD_MAIN_CONTAINER_CLASS = getattr(_svg_parser, 'BEAD_ROAD_MAIN_CONTAINER_CLASS', 'pf_pi')

# 페이지 설정
st.set_page_config(
    page_title="Change-point N-gram Generator",
    page_icon="🔍",
    layout="wide"
)

def display_grid_visualization(grid):
    """
    파싱된 Grid를 시각적으로 표시
    """
    st.markdown("### 📊 Grid 시각화")
    
    # Grid 데이터프레임 생성 (행과 열을 반대로 표시)
    display_data = []
    # 통계 정보를 한 번의 순회로 계산 (성능 최적화)
    total_cells = TABLE_WIDTH * TABLE_HEIGHT
    filled_cells = 0
    b_count = 0
    p_count = 0
    t_count = 0
    
    for row_idx in range(TABLE_HEIGHT):
        row_data = []
        for col_idx in range(TABLE_WIDTH):
            cell_value = grid[col_idx][row_idx]
            if cell_value == 'b':
                row_data.append('🔴 B')
                filled_cells += 1
                b_count += 1
            elif cell_value == 'p':
                row_data.append('🔵 P')
                filled_cells += 1
                p_count += 1
            elif cell_value == 't':
                row_data.append('⚪ T')
                filled_cells += 1
                t_count += 1
            else:
                row_data.append('⚫')
        display_data.append(row_data)
    
    # 컬럼명 생성
    columns = [f"Col {i+1}" for i in range(TABLE_WIDTH)]
    
    # 데이터프레임 생성 및 표시
    df = pd.DataFrame(display_data, columns=columns)
    st.dataframe(df, use_container_width=True, hide_index=True)
    
    # 통계 정보
    col_stat1, col_stat2, col_stat3, col_stat4, col_stat5 = st.columns(5)
    with col_stat1:
        st.metric("총 셀 수", total_cells)
    with col_stat2:
        st.metric("채워진 셀", filled_cells)
    with col_stat3:
        st.metric("🔴 B", b_count)
    with col_stat4:
        st.metric("🔵 P", p_count)
    with col_stat5:
        st.metric("⚪ T", t_count)


def detect_change_points(grid_string):
    """
    Change-point Detection: 변화점 감지 및 앵커 위치 반환
    """
    anchors = []
    change_points = []
    
    for i in range(len(grid_string) - 1):
        if grid_string[i] != grid_string[i+1]:
            # 변화점 감지
            change_points.append({
                'index': i,
                'from': grid_string[i],
                'to': grid_string[i+1],
                'anchor': i  # 변화 이전 위치가 앵커
            })
            anchors.append(i)
    
    # 중복 제거
    anchors = sorted(list(set(anchors)))
    
    return anchors, change_points


def load_recent_grid_strings():
    """
    최근 저장된 Grid String 목록 로드
    """
    try:
        conn = get_change_point_db_connection()
        if conn is None:
            return pd.DataFrame()
        
        query = """
            SELECT 
                id,
                grid_string,
                string_length,
                b_count,
                p_count,
                b_ratio,
                p_ratio,
                created_at
            FROM preprocessed_grid_strings
            ORDER BY created_at DESC
            LIMIT 50
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        st.error(f"데이터 로드 오류: {str(e)}")
        return pd.DataFrame()


def save_grid_string_to_db(grid_string):
    """
    Grid String을 Change-point DB에 저장
    그리고 ngram_chunks_change_point도 자동으로 생성하여 저장
    또한 hypothesis_validation.db에도 동기화하여 저장
    중복된 grid_string인 경우 기존 레코드를 반환하고 새로 저장하지 않음
    """
    # 테이블 생성 확인
    create_change_point_preprocessed_grid_strings_table()
    create_change_point_ngram_chunks_table()
    
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    
    try:
        # 통계 계산
        string_length = len(grid_string)
        b_count = grid_string.count('b')
        p_count = grid_string.count('p')
        t_count = grid_string.count('t')
        b_ratio = (b_count / string_length * 100) if string_length > 0 else 0.0
        p_ratio = (p_count / string_length * 100) if string_length > 0 else 0.0
        
        # source_session_id 생성 (hypothesis_validation.db와 동기화를 위해)
        from datetime import datetime
        import uuid
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        unique_id = str(uuid.uuid4())[:8]
        source_session_id = f'change_point_svg_parse_{timestamp}_{unique_id}'
        source_id = str(uuid.uuid4())
        
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
                # 중복된 경우에도 누락된 윈도우 크기 확인 및 생성
                # 윈도우 크기별로 확인하고 없는 것만 생성
                target_window_sizes = [5, 6, 7, 8, 9, 10, 11, 12]
                missing_window_sizes = []
                for window_size in target_window_sizes:
                    cursor.execute('''
                        SELECT COUNT(*) FROM ngram_chunks_change_point 
                        WHERE grid_string_id = ? AND window_size = ?
                    ''', (record_id, window_size))
                    existing_count = cursor.fetchone()[0]
                    if existing_count == 0:
                        missing_window_sizes.append(window_size)
                
                # 누락된 윈도우 크기가 있으면 생성
                if missing_window_sizes:
                    try:
                        generate_and_save_ngram_chunks_change_point(
                            record_id,
                            grid_string,
                            window_sizes=missing_window_sizes,
                            conn=conn
                        )
                    except Exception as ngram_error:
                        import warnings
                        warnings.warn(f"ngram_chunks_change_point 생성 중 오류 발생: {str(ngram_error)}")
                
                conn.commit()
                conn.close()
                
                # hypothesis_validation.db에도 동기화 (중복 체크)
                try:
                    from svg_parser_module import get_db_connection, create_preprocessed_grid_strings_table, create_ngram_chunks_table, generate_and_save_ngram_chunks
                    import uuid
                    from datetime import datetime
                    
                    # hypothesis_validation.db에 저장
                    create_preprocessed_grid_strings_table()
                    create_ngram_chunks_table()
                    
                    hv_conn = get_db_connection()
                    hv_cursor = hv_conn.cursor()
                    
                    b_count = grid_string.count('b')
                    p_count = grid_string.count('p')
                    string_length = len(grid_string)
                    
                    hv_cursor.execute('''
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
                    
                    if hv_cursor.rowcount > 0:
                        hv_record_id = hv_cursor.lastrowid
                        hv_conn.commit()
                        # ngram_chunks 생성
                        try:
                            generate_and_save_ngram_chunks(hv_record_id, grid_string, conn=hv_conn)
                        except Exception as ngram_error:
                            import warnings
                            warnings.warn(f"ngram_chunks 생성 중 오류 발생: {str(ngram_error)}")
                    else:
                        # 중복된 경우 기존 레코드 ID 조회
                        hv_cursor.execute('''
                            SELECT id FROM preprocessed_grid_strings 
                            WHERE grid_string = ?
                        ''', (grid_string,))
                        result = hv_cursor.fetchone()
                        if result:
                            hv_record_id = result[0]
                            # ngram_chunks가 있는지 확인
                            hv_cursor.execute('''
                                SELECT COUNT(*) FROM ngram_chunks 
                                WHERE grid_string_id = ?
                            ''', (hv_record_id,))
                            count = hv_cursor.fetchone()[0]
                            if count == 0:
                                # ngram_chunks가 없으면 생성
                                try:
                                    generate_and_save_ngram_chunks(hv_record_id, grid_string, conn=hv_conn)
                                except Exception as ngram_error:
                                    import warnings
                                    warnings.warn(f"ngram_chunks 생성 중 오류 발생: {str(ngram_error)}")
                    
                    hv_conn.commit()
                    hv_conn.close()
                except Exception as sync_error:
                    import warnings
                    warnings.warn(f"hypothesis_validation.db 동기화 중 오류 발생: {str(sync_error)}")
                
                return record_id
            else:
                # 예상치 못한 상황
                raise Exception("중복 저장 시도했으나 기존 레코드를 찾을 수 없습니다.")
        
        record_id = cursor.lastrowid
        conn.commit()
        
        # ngram_chunks_change_point 생성 및 저장 (새로 저장된 경우에만)
        # 같은 연결을 재사용하여 락 방지
        try:
            generate_and_save_ngram_chunks_change_point(
                record_id, 
                grid_string, 
                window_sizes=[5, 6, 7, 8, 9, 10, 11, 12],
                conn=conn
            )
        except Exception as ngram_error:
            # ngram_chunks 생성 실패해도 레코드는 저장되었으므로 경고만
            import warnings
            warnings.warn(f"ngram_chunks_change_point 생성 중 오류 발생 (레코드는 저장됨): {str(ngram_error)}")
        
        conn.close()
        
        # hypothesis_validation.db에도 동기화하여 저장
        try:
            from svg_parser_module import get_db_connection, create_preprocessed_grid_strings_table, create_ngram_chunks_table, generate_and_save_ngram_chunks
            
            # hypothesis_validation.db에 저장
            create_preprocessed_grid_strings_table()
            create_ngram_chunks_table()
            
            hv_conn = get_db_connection()
            hv_cursor = hv_conn.cursor()
            
            b_count = grid_string.count('b')
            p_count = grid_string.count('p')
            string_length = len(grid_string)
            
            hv_cursor.execute('''
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
            
            if hv_cursor.rowcount > 0:
                hv_record_id = hv_cursor.lastrowid
                hv_conn.commit()
                # ngram_chunks 생성
                try:
                    generate_and_save_ngram_chunks(hv_record_id, grid_string, conn=hv_conn)
                except Exception as ngram_error:
                    import warnings
                    warnings.warn(f"ngram_chunks 생성 중 오류 발생: {str(ngram_error)}")
            else:
                # 중복된 경우 기존 레코드 ID 조회
                hv_cursor.execute('''
                    SELECT id FROM preprocessed_grid_strings 
                    WHERE grid_string = ?
                ''', (grid_string,))
                result = hv_cursor.fetchone()
                if result:
                    hv_record_id = result[0]
                    # ngram_chunks가 있는지 확인
                    hv_cursor.execute('''
                        SELECT COUNT(*) FROM ngram_chunks 
                        WHERE grid_string_id = ?
                    ''', (hv_record_id,))
                    count = hv_cursor.fetchone()[0]
                    if count == 0:
                        # ngram_chunks가 없으면 생성
                        try:
                            generate_and_save_ngram_chunks(hv_record_id, grid_string, conn=hv_conn)
                        except Exception as ngram_error:
                            import warnings
                            warnings.warn(f"ngram_chunks 생성 중 오류 발생: {str(ngram_error)}")
            
            hv_conn.commit()
            hv_conn.close()
        except Exception as sync_error:
            # 동기화 실패해도 Change-point DB는 저장되었으므로 경고만
            import warnings
            warnings.warn(f"hypothesis_validation.db 동기화 중 오류 발생 (Change-point DB는 저장됨): {str(sync_error)}")
        
        return record_id
        
    except Exception as e:
        if conn:
            conn.rollback()
            conn.close()
        raise Exception(f"DB 저장 오류: {str(e)}")


def main():
    st.title("🔍 Change-point Detection 기반 N-gram 생성")
    st.markdown("변화점 탐지를 통해 의미 있는 위치에서만 N-gram을 생성합니다.")
    st.markdown("---")
    
    # 테이블 생성 확인
    try:
        create_change_point_preprocessed_grid_strings_table()
        create_change_point_ngram_chunks_table()
    except Exception as e:
        st.warning(f"테이블 생성 확인 중 오류: {str(e)}")
    
    # SVG 입력 섹션
    st.header("📝 SVG 코드 입력")
    # 브라우저 콘솔에서 그리드 HTML 복사용 (파싱 클래스명과 동기화)
    copy_cmd = f"copy(document.querySelector('.{BEAD_ROAD_MAIN_CONTAINER_CLASS}').outerHTML);"
    st.caption("브라우저 개발자도구 콘솔에서 그리드 복사:")
    st.code(copy_cmd, language="javascript")
    
    # SVG 입력 리셋을 위한 key 관리
    if 'svg_input_key_counter' not in st.session_state:
        st.session_state.svg_input_key_counter = 0
    
    svg_code_input = st.text_area(
        "SVG 코드 입력",
        value="",
        help="SVG 코드를 붙여넣으세요",
        key=f"svg_input_{st.session_state.svg_input_key_counter}",
        height=200
    )
    
    col_svg1, col_svg2 = st.columns([3, 1])
    
    with col_svg2:
        st.markdown("<br>", unsafe_allow_html=True)
        parse_button = st.button("🔍 파싱", type="primary", use_container_width=True, key="parse_svg_button")
    
    with col_svg2:
        st.markdown("<br>", unsafe_allow_html=True)
        save_button = st.button("💾 DB 저장", use_container_width=True, key="save_parsed_to_db_button", 
                                disabled=('parsed_grid_string' not in st.session_state or not st.session_state.parsed_grid_string))
    
    with col_svg2:
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 리셋", use_container_width=True, key="reset_svg_input_button"):
            st.session_state.svg_input_key_counter += 1
            if 'parsed_grid_string' in st.session_state:
                del st.session_state.parsed_grid_string
            if 'parsed_grid' in st.session_state:
                del st.session_state.parsed_grid
            if 'parsing_error' in st.session_state:
                del st.session_state.parsing_error
            if 'parsing_traceback' in st.session_state:
                del st.session_state.parsing_traceback
            if 'cached_recent_data' in st.session_state:
                del st.session_state.cached_recent_data
            st.rerun()
    
    with col_svg1:
        if svg_code_input:
            st.info("SVG 코드를 입력한 후 '파싱' 버튼을 클릭하세요.")
    
    # 파싱 실행
    if parse_button and svg_code_input:
        if not svg_code_input or not svg_code_input.strip():
            st.warning("⚠️ SVG 코드를 입력해주세요.")
        else:
            # 파싱 전에 이전 파싱 결과 초기화 (중복 방지)
            if 'parsed_grid_string' in st.session_state:
                del st.session_state.parsed_grid_string
            if 'parsed_grid' in st.session_state:
                del st.session_state.parsed_grid
            if 'parsing_error' in st.session_state:
                del st.session_state.parsing_error
            if 'parsing_traceback' in st.session_state:
                del st.session_state.parsing_traceback
            
            # 파싱 실행
            with st.spinner("SVG 파싱 중..."):
                try:
                    # SVG 파싱
                    parsed_grid = parse_bead_road_svg(svg_code_input)
                    
                    # Grid를 문자열로 변환
                    grid_string_parsed = grid_to_string_column_wise(parsed_grid)
                    
                    if grid_string_parsed:
                        # Session state에 저장하여 다음 단계에서 사용
                        st.session_state.parsed_grid_string = grid_string_parsed
                        st.session_state.parsed_grid = parsed_grid
                        
                        # 파싱 완료 후 캐시 무효화 (목록이 업데이트되어야 함)
                        if 'cached_recent_data' in st.session_state:
                            del st.session_state.cached_recent_data
                    else:
                        st.session_state.parsing_error = "파싱된 Grid에서 유효한 문자열을 추출할 수 없습니다."
                        st.session_state.parsed_grid = parsed_grid
                except Exception as parse_error:
                    st.session_state.parsing_error = str(parse_error)
                    import traceback
                    st.session_state.parsing_traceback = traceback.format_exc()
            
            # 파싱 완료 후 리렌더링
            st.rerun()
    
    # 파싱 결과 표시 (별도 렌더링으로 분리)
    grid_string_input = ""
    parsed_grid = None
    
    if 'parsed_grid_string' in st.session_state and st.session_state.parsed_grid_string:
        st.success(f"✅ 파싱 완료! Grid String 길이: {len(st.session_state.parsed_grid_string)}")
        
        # DB 저장 기능 (파싱 완료 메시지 바로 아래에 표시)
        if save_button:
            try:
                with st.spinner("DB 저장 중..."):
                    # DB에 저장
                    grid_string_to_save = st.session_state.parsed_grid_string
                    record_id = save_grid_string_to_db(grid_string_to_save)
                    st.success(f"✅ DB 저장 완료! (Record ID: {record_id})")
                    st.info("💡 ngram_chunks_change_point도 자동으로 생성되어 저장되었습니다.")
                    # 저장 후 캐시 무효화 (목록이 업데이트되어야 함)
                    if 'cached_recent_data' in st.session_state:
                        del st.session_state.cached_recent_data
            except Exception as e:
                st.error(f"❌ DB 저장 중 오류 발생: {str(e)}")
                import traceback
                st.error(f"상세 오류: {traceback.format_exc()}")
        
        # 파싱된 Grid String 전체 표시
        st.markdown("**파싱된 Grid String:**")
        st.code(st.session_state.parsed_grid_string, language=None)
        
        # Grid 시각화 표시
        if 'parsed_grid' in st.session_state:
            display_grid_visualization(st.session_state.parsed_grid)
        
        grid_string_input = st.session_state.parsed_grid_string
        parsed_grid = st.session_state.parsed_grid
    
    # 파싱 오류 표시
    if 'parsing_error' in st.session_state:
        st.error(f"❌ SVG 파싱 중 오류 발생: {st.session_state.parsing_error}")
        if 'parsing_traceback' in st.session_state:
            st.error(f"상세 오류: {st.session_state.parsing_traceback}")
        if 'parsed_grid' in st.session_state:
            st.warning("⚠️ 파싱된 Grid에서 유효한 문자열을 추출할 수 없습니다.")
            display_grid_visualization(st.session_state.parsed_grid)
    
    st.markdown("---")
    
    # Change-point Detection 섹션
    if grid_string_input and grid_string_input.strip():
        st.header("🔍 Change-point Detection")
        
        # 변화점 감지
        anchors, change_points = detect_change_points(grid_string_input)
        
        col_info1, col_info2, col_info3 = st.columns(3)
        with col_info1:
            st.metric("Grid String 길이", len(grid_string_input))
        with col_info2:
            st.metric("감지된 변화점", len(change_points))
        with col_info3:
            st.metric("앵커 위치 수", len(anchors))
        
        # 변화점 상세 정보
        if change_points:
            st.markdown("### 변화점 상세 정보")
            change_points_df = pd.DataFrame(change_points)
            st.dataframe(change_points_df, use_container_width=True, hide_index=True)
            
            # 앵커 위치 표시
            st.markdown("### 앵커 위치")
            st.code(f"앵커 인덱스: {anchors}")
        
        # N-gram 생성 섹션 (추가 생성용 - 저장 시 이미 자동 생성됨)
        st.markdown("---")
        st.header("📦 추가 N-gram 생성 (선택사항)")
        st.info("💡 DB 저장 시 기본 윈도우 크기(5, 6, 7, 8, 9, 10, 11, 12)로 N-gram이 자동 생성됩니다. 다른 윈도우 크기로 추가 생성하려면 이 섹션을 사용하세요.")
        
        col_gen1, col_gen2 = st.columns([2, 1])
        
        with col_gen1:
            window_sizes = st.multiselect(
                "윈도우 크기 선택",
                options=[5, 6, 7, 8, 9, 10, 11, 12],
                default=[],
                key="window_sizes_select"
            )
        
        # N-gram 생성 버튼
        col_btn1, col_btn2 = st.columns([1, 4])
        
        with col_btn1:
            # 저장된 grid_string_id 확인
            saved_grid_string_id = None
            if 'parsed_grid_string' in st.session_state:
                try:
                    conn = get_change_point_db_connection()
                    cursor = conn.cursor()
                    cursor.execute('''
                        SELECT id FROM preprocessed_grid_strings 
                        WHERE grid_string = ?
                    ''', (st.session_state.parsed_grid_string,))
                    result = cursor.fetchone()
                    if result:
                        saved_grid_string_id = result[0]
                    conn.close()
                except:
                    pass
            
            generate_button = st.button(
                "🚀 추가 N-gram 생성", 
                type="primary", 
                use_container_width=True, 
                key="generate_ngram_button",
                disabled=(saved_grid_string_id is None or not window_sizes)
            )
            
            if saved_grid_string_id is None:
                st.caption("⚠️ 먼저 DB에 저장해주세요")
            elif not window_sizes:
                st.caption("⚠️ 윈도우 크기를 선택해주세요")
        
        if generate_button:
            if not window_sizes:
                st.warning("⚠️ 윈도우 크기를 선택해주세요.")
            elif saved_grid_string_id is None:
                st.warning("⚠️ 먼저 '💾 DB 저장' 버튼을 클릭하여 Grid String을 저장해주세요.")
            else:
                try:
                    with st.spinner("추가 N-gram 생성 중..."):
                        # N-gram 생성 (이미 저장된 grid_string_id 사용)
                        result = generate_and_save_ngram_chunks_change_point(
                            saved_grid_string_id,
                            grid_string_input,
                            window_sizes=window_sizes
                        )
                        
                        # 결과 표시
                        st.success("✅ 추가 N-gram 생성 완료!")
                        st.markdown("### 생성 결과")
                        result_df = pd.DataFrame([
                            {'윈도우 크기': k, '생성된 N-gram 수': v}
                            for k, v in result.items()
                        ])
                        st.dataframe(result_df, use_container_width=True, hide_index=True)
                        
                        total_ngrams = sum(result.values())
                        st.metric("총 생성된 N-gram 수", total_ngrams)
                            
                except Exception as e:
                    st.error(f"❌ N-gram 생성 중 오류 발생: {str(e)}")
                    import traceback
                    st.error(f"상세 오류: {traceback.format_exc()}")
    
    st.markdown("---")
    
    # 저장된 데이터 목록
    st.header("📋 저장된 데이터 목록")
    
    refresh_clicked = st.button("🔄 목록 새로고침", key="refresh_data_list")
    
    # 세션 상태에 데이터 캐시 저장
    if 'cached_recent_data' not in st.session_state or refresh_clicked:
        with st.spinner("데이터 로딩 중..."):
            st.session_state.cached_recent_data = load_recent_grid_strings()
            st.session_state.cached_recent_data_timestamp = time.time()
    
    df_recent = st.session_state.cached_recent_data
    
    if len(df_recent) > 0:
        st.info(f"최근 저장된 데이터: {len(df_recent)}개")
        
        # 컬럼명 한글화
        display_df = df_recent.copy()
        column_mapping = {
            'id': 'ID',
            'grid_string': 'Grid String',
            'string_length': '길이',
            'b_count': 'B 개수',
            'p_count': 'P 개수',
            'b_ratio': 'B 비율 (%)',
            'p_ratio': 'P 비율 (%)',
            'created_at': '생성일시'
        }
        display_df = display_df.rename(columns=column_mapping)
        
        # 데이터 표시
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        
        # 통계 정보
        st.markdown("### 📊 통계 정보")
        col_stat1, col_stat2, col_stat3, col_stat4 = st.columns(4)
        with col_stat1:
            st.metric("총 레코드 수", len(df_recent))
        with col_stat2:
            avg_length = df_recent['string_length'].mean() if len(df_recent) > 0 else 0
            st.metric("평균 길이", f"{avg_length:.1f}")
        with col_stat3:
            total_b = df_recent['b_count'].sum() if len(df_recent) > 0 else 0
            st.metric("총 B 개수", f"{total_b:,}")
        with col_stat4:
            total_p = df_recent['p_count'].sum() if len(df_recent) > 0 else 0
            st.metric("총 P 개수", f"{total_p:,}")
    else:
        st.info("저장된 데이터가 없습니다. Grid String을 입력하고 N-gram을 생성해주세요.")
    
    st.markdown("---")
    
    # 사용 방법 안내
    with st.expander("ℹ️ 사용 방법", expanded=False):
        st.markdown("""
        ### Change-point Detection 기반 N-gram 생성 앱 사용 방법
        
        1. **SVG 코드 입력 및 파싱**
           - SVG 코드를 텍스트 영역에 붙여넣고 '파싱' 버튼 클릭
           - 현재 지원하는 클래스명: `pf_pi` (메인 컨테이너), `pf_ow` (행), `pf_pk` (셀)
           - 브라우저에서 그리드 복사: 개발자도구 콘솔에 `copy(document.querySelector('.pf_pi').outerHTML);` 실행
           - 파싱된 Grid String과 시각화가 표시됩니다
        
        2. **DB 저장 (자동 N-gram 생성 포함)**
           - 파싱 완료 후 '💾 DB 저장' 버튼 클릭
           - Grid String이 데이터베이스에 저장됩니다
           - **자동으로 Change-point Detection 기반 N-gram이 생성되어 저장됩니다** (윈도우 크기: 5, 6, 7, 8, 9, 10, 11, 12)
        
        3. **Change-point Detection**
           - 저장 후 자동으로 변화점 감지 및 앵커 위치 계산
           - 변화점 상세 정보 확인
        
        4. **추가 N-gram 생성 (선택사항)**
           - 다른 윈도우 크기로 추가 생성하려면 이 섹션 사용
           - 윈도우 크기 선택 후 '🚀 추가 N-gram 생성' 버튼 클릭
        
        5. **데이터 관리**
           - 저장된 Grid String 목록 조회
           - 생성된 N-gram 통계 확인
        
        ### Change-point Detection 규칙
        - **Trigger**: `Input[i] ≠ Input[i+1]` 일 때 변화점 감지
        - **Anchor**: 변화 감지 이전 위치 (i)를 앵커로 사용
        - 앵커 위치에서만 N-gram 생성 (기존 슬라이딩 윈도우와 다름)
        
        ### 주의사항
        - 클래스명이 변경되면 `svg_parser_module.py`의 `parse_bead_road_svg` 함수를 수정해야 합니다
        """)


if __name__ == "__main__":
    main()
