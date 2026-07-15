"""
SVG 파싱 전용 앱
hypothesis_validation_app의 SVG 파싱 기능을 독립적으로 사용할 수 있는 앱
"""

import streamlit as st
import pandas as pd
import os
import time
from svg_parser_module import (
    grid_to_string_column_wise,
    save_parsed_grid_string_to_db,
    create_preprocessed_grid_strings_table,
    create_ngram_chunks_table,
    TABLE_WIDTH,
    TABLE_HEIGHT
)
# [OPTIONAL P/B/T] 제거 시 아래 import 및 파싱 블록 삭제
from svg_parser_pbt_extension import parse_bead_road_svg_with_pbt_fallback

# 페이지 설정
st.set_page_config(
    page_title="SVG Parser",
    page_icon="📥",
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

def load_recent_parsed_data():
    """
    최근 파싱된 데이터 목록 로드
    """
    try:
        from svg_parser_module import get_db_connection
        conn = get_db_connection()
        if conn is None:
            return pd.DataFrame()
        
        query = """
            SELECT 
                id,
                source_session_id,
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

def main():
    st.title("📥 SVG Parser")
    st.markdown("SVG 코드를 입력하여 Grid String을 추출하고 데이터베이스에 저장합니다.")
    st.markdown("---")
    
    # 테이블 생성 확인
    try:
        create_preprocessed_grid_strings_table()
        create_ngram_chunks_table()
    except Exception as e:
        st.warning(f"테이블 생성 확인 중 오류: {str(e)}")
    
    # SVG 입력 섹션
    st.header("📝 SVG 코드 입력")
    
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
            # SVG 입력 초기화 (key 변경으로 text_area 리셋)
            st.session_state.svg_input_key_counter += 1
            # 파싱된 Grid String 초기화
            if 'parsed_grid_string' in st.session_state:
                del st.session_state.parsed_grid_string
            if 'parsed_grid' in st.session_state:
                del st.session_state.parsed_grid
            if 'parsing_error' in st.session_state:
                del st.session_state.parsing_error
            if 'parsing_traceback' in st.session_state:
                del st.session_state.parsing_traceback
            if 'parser_used' in st.session_state:
                del st.session_state.parser_used
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
            if 'parser_used' in st.session_state:
                del st.session_state.parser_used
            
            # 파싱 실행
            with st.spinner("SVG 파싱 중..."):
                try:
                    # [OPTIONAL P/B/T] 한글 + P/B/T fallback 파싱
                    parsed_grid, parser_used = parse_bead_road_svg_with_pbt_fallback(svg_code_input)
                    st.session_state.parser_used = parser_used
                    
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
    if 'parsed_grid_string' in st.session_state and st.session_state.parsed_grid_string:
        parser_label = st.session_state.get('parser_used', 'korean')
        if parser_label == 'pbt':
            st.info("P/B/T 영문 마크업 파서로 파싱되었습니다.")
        st.success(f"✅ 파싱 완료! Grid String 길이: {len(st.session_state.parsed_grid_string)}")
        
        # DB 저장 기능 (파싱 완료 메시지 바로 아래에 표시)
        if save_button:
            try:
                with st.spinner("DB 저장 중..."):
                    # DB에 저장
                    grid_string_to_save = st.session_state.parsed_grid_string
                    record_id = save_parsed_grid_string_to_db(grid_string_to_save)
                    st.success(f"✅ DB 저장 완료! (Record ID: {record_id})")
                    st.info("💡 ngram_chunks도 자동으로 생성되어 저장되었습니다.")
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
    
    # 파싱 오류 표시
    if 'parsing_error' in st.session_state:
        st.error(f"❌ SVG 파싱 중 오류 발생: {st.session_state.parsing_error}")
        if 'parsing_traceback' in st.session_state:
            st.error(f"상세 오류: {st.session_state.parsing_traceback}")
        if 'parsed_grid' in st.session_state:
            st.warning("⚠️ 파싱된 Grid에서 유효한 문자열을 추출할 수 없습니다.")
            display_grid_visualization(st.session_state.parsed_grid)
    
    st.markdown("---")
    
    # 저장된 데이터 목록
    st.header("📋 저장된 데이터 목록")
    
    # 데이터 새로고침 버튼 (성능 최적화: 버튼 클릭 시에만 DB 쿼리 실행)
    refresh_clicked = st.button("🔄 목록 새로고침", key="refresh_data_list")
    
    # 세션 상태에 데이터 캐시 저장 (성능 최적화)
    if 'cached_recent_data' not in st.session_state or refresh_clicked:
        with st.spinner("데이터 로딩 중..."):
            st.session_state.cached_recent_data = load_recent_parsed_data()
            st.session_state.cached_recent_data_timestamp = time.time()
    
    df_recent = st.session_state.cached_recent_data
    
    if len(df_recent) > 0:
        st.info(f"최근 저장된 데이터: {len(df_recent)}개")
        
        # 컬럼명 한글화
        display_df = df_recent.copy()
        column_mapping = {
            'id': 'ID',
            'source_session_id': '세션 ID',
            'grid_string': 'Grid String',
            'string_length': '길이',
            'b_count': 'B 개수',
            'p_count': 'P 개수',
            'b_ratio': 'B 비율 (%)',
            'p_ratio': 'P 비율 (%)',
            'created_at': '생성일시'
        }
        display_df = display_df.rename(columns=column_mapping)
        
        # 컬럼 순서 지정
        column_order = [
            'ID',
            '세션 ID',
            'Grid String',
            '길이',
            'B 개수',
            'P 개수',
            'B 비율 (%)',
            'P 비율 (%)',
            '생성일시'
        ]
        
        # 존재하는 컬럼만 선택
        available_columns = [col for col in column_order if col in display_df.columns]
        display_df = display_df[available_columns]
        
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
        st.info("저장된 데이터가 없습니다. SVG를 파싱하고 저장해주세요.")
    
    st.markdown("---")
    
    # 사용 방법 안내
    with st.expander("ℹ️ 사용 방법", expanded=False):
        st.markdown("""
        ### SVG 파싱 앱 사용 방법
        
        1. **SVG 코드 입력**
           - SVG 코드를 텍스트 영역에 붙여넣으세요
           - 현재 지원하는 클래스명: `uv_uA` (메인 컨테이너), `uv_uF` (행), `uv_uG` (셀)
        
        2. **파싱 실행**
           - "파싱" 버튼을 클릭하여 SVG를 파싱합니다
           - 한글(플/뱅/무) 또는 영문(P/B/T) 마크업을 자동 감지합니다
           - 파싱된 Grid String과 시각화가 표시됩니다
        
        3. **DB 저장**
           - 파싱된 Grid String을 데이터베이스에 저장합니다
           - 저장 시 ngram_chunks도 자동으로 생성됩니다
        
        4. **리셋**
           - 입력과 파싱 결과를 초기화합니다
        
        ### 주의사항
        - 클래스명이 변경되면 `svg_parser_module.py`의 `parse_bead_road_svg` 함수를 수정해야 합니다
        - 파싱된 데이터는 `hypothesis_validation.db`의 `preprocessed_grid_strings` 테이블에 저장됩니다
        """)

if __name__ == "__main__":
    main()
