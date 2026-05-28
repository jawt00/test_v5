"""
Suffix 윈도우 크기별 신뢰도 조회 앱
ngram_chunk 테이블의 데이터를 기반으로 suffix의 윈도우 크기별 신뢰도를 계산하여 표시
"""

import streamlit as st
import pandas as pd
import os
import sys

# 상위 디렉토리의 모듈 import를 위한 경로 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from svg_parser_module import get_change_point_db_connection

# 페이지 설정
st.set_page_config(
    page_title="Suffix 신뢰도 조회",
    page_icon="📊",
    layout="wide"
)

def calculate_suffix_confidence(df):
    """
    ngram_chunk 데이터에서 suffix별 신뢰도 계산
    
    Args:
        df: ngram_chunk 테이블의 DataFrame (window_size, prefix, suffix, count 포함)
           이미 집계된 데이터이므로 count 컬럼을 직접 사용
    
    Returns:
        DataFrame: suffix별 신뢰도 정보
    """
    if df.empty:
        return pd.DataFrame()
    
    # 이미 집계된 데이터이므로 count 컬럼을 직접 사용
    # 각 prefix별로 suffix 분포 계산
    prefix_totals = df.groupby(['window_size', 'prefix'])['count'].sum().reset_index(name='total_count')
    
    # 병합하여 비율 계산
    merged = df.merge(prefix_totals, on=['window_size', 'prefix'])
    merged['ratio'] = merged['count'] / merged['total_count']
    
    # 각 prefix별로 b와 p suffix의 비율 계산
    suffix_stats = []
    for (window_size, prefix), group in merged.groupby(['window_size', 'prefix']):
        # suffix별 count 합계 계산
        b_count = group[group['suffix'] == 'b']['count'].sum()
        p_count = group[group['suffix'] == 'p']['count'].sum()
        t_count = group[group['suffix'] == 't']['count'].sum()
        total = b_count + p_count + t_count
        
        if total > 0:
            b_ratio = b_count / total
            p_ratio = p_count / total
            t_ratio = t_count / total
            
            # 신뢰도 계산: b와 p의 비율 차이 (절대값)
            confidence = abs(b_ratio - p_ratio)
            
            # 가장 빈도가 높은 suffix
            most_common_idx = group['count'].idxmax()
            most_common_suffix = group.loc[most_common_idx, 'suffix']
            most_common_count = group.loc[most_common_idx, 'count']
            most_common_ratio = group.loc[most_common_idx, 'ratio']
            
            suffix_stats.append({
                'window_size': window_size,
                'prefix': prefix,
                'b_count': b_count,
                'p_count': p_count,
                't_count': t_count,
                'total_count': total,
                'b_ratio': b_ratio,
                'p_ratio': p_ratio,
                't_ratio': t_ratio,
                'confidence': confidence,
                'most_common_suffix': most_common_suffix,
                'most_common_count': most_common_count,
                'most_common_ratio': most_common_ratio
            })
    
    return pd.DataFrame(suffix_stats)


def load_ngram_chunks():
    """
    ngram_chunk 테이블에서 데이터 로드
    """
    try:
        conn = get_change_point_db_connection()
        if conn is None:
            return pd.DataFrame()
        
        # 테이블 이름 확인 (ngram_chunk 또는 ngram_chunks_change_point)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE '%ngram%'")
        tables = [row[0] for row in cursor.fetchall()]
        
        table_name = None
        if 'ngram_chunk' in tables:
            table_name = 'ngram_chunk'
        elif 'ngram_chunks_change_point' in tables:
            table_name = 'ngram_chunks_change_point'
        else:
            st.error("ngram_chunk 또는 ngram_chunks_change_point 테이블을 찾을 수 없습니다.")
            conn.close()
            return pd.DataFrame()
        
        query = f"""
            SELECT window_size, prefix, suffix, COUNT(*) as count
            FROM {table_name}
            GROUP BY window_size, prefix, suffix
            ORDER BY window_size, prefix, suffix
        """
        
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
        
    except Exception as e:
        st.error(f"데이터 로드 오류: {str(e)}")
        return pd.DataFrame()


def get_summary_statistics(confidence_df):
    """
    신뢰도 데이터의 요약 통계 계산
    
    Args:
        confidence_df: calculate_suffix_confidence로 계산된 DataFrame
    
    Returns:
        dict: 요약 통계
    """
    if confidence_df.empty:
        return {}
    
    summary = {}
    
    for window_size in sorted(confidence_df['window_size'].unique()):
        window_data = confidence_df[confidence_df['window_size'] == window_size]
        
        summary[window_size] = {
            'total_prefixes': len(window_data),
            'avg_confidence': window_data['confidence'].mean(),
            'max_confidence': window_data['confidence'].max(),
            'min_confidence': window_data['confidence'].min(),
            'high_confidence_count': len(window_data[window_data['confidence'] >= 0.5]),
            'medium_confidence_count': len(window_data[(window_data['confidence'] >= 0.3) & (window_data['confidence'] < 0.5)]),
            'low_confidence_count': len(window_data[window_data['confidence'] < 0.3]),
            'total_ngrams': window_data['total_count'].sum(),
            'avg_total_count': window_data['total_count'].mean(),
        }
    
    return summary


def main():
    st.title("📊 Suffix 윈도우 크기별 신뢰도 조회")
    st.markdown("ngram_chunk 테이블의 데이터를 기반으로 suffix의 윈도우 크기별 신뢰도를 계산하여 표시합니다.")
    st.markdown("---")
    
    # 데이터 로드
    with st.spinner("데이터 로딩 중..."):
        ngram_df = load_ngram_chunks()
    
    if ngram_df.empty:
        st.warning("⚠️ 데이터가 없습니다. ngram_chunk 테이블에 데이터가 있는지 확인해주세요.")
        return
    
    st.success(f"✅ {len(ngram_df)}개의 ngram 데이터를 로드했습니다.")
    
    # 신뢰도 계산
    with st.spinner("신뢰도 계산 중..."):
        confidence_df = calculate_suffix_confidence(ngram_df)
    
    if confidence_df.empty:
        st.warning("⚠️ 신뢰도를 계산할 수 없습니다.")
        return
    
    # 요약 통계
    st.header("📈 요약 통계")
    summary = get_summary_statistics(confidence_df)
    
    # 윈도우 크기별 요약 표시
    summary_rows = []
    for window_size in sorted(summary.keys()):
        stats = summary[window_size]
        summary_rows.append({
            '윈도우 크기': window_size,
            '총 Prefix 수': stats['total_prefixes'],
            '평균 신뢰도': f"{stats['avg_confidence']:.4f}",
            '최대 신뢰도': f"{stats['max_confidence']:.4f}",
            '최소 신뢰도': f"{stats['min_confidence']:.4f}",
            '고신뢰도 (≥0.5)': stats['high_confidence_count'],
            '중신뢰도 (0.3~0.5)': stats['medium_confidence_count'],
            '저신뢰도 (<0.3)': stats['low_confidence_count'],
            '총 N-gram 수': stats['total_ngrams'],
            '평균 N-gram 수': f"{stats['avg_total_count']:.1f}",
        })
    
    summary_df = pd.DataFrame(summary_rows)
    st.dataframe(summary_df, use_container_width=True, hide_index=True)
    
    st.markdown("---")
    
    # 윈도우 크기별 상세 신뢰도 테이블
    st.header("📋 윈도우 크기별 상세 신뢰도")
    
    window_sizes = sorted(confidence_df['window_size'].unique())
    selected_window = st.selectbox(
        "윈도우 크기 선택",
        options=window_sizes,
        index=0 if window_sizes else None
    )
    
    if selected_window:
        window_data = confidence_df[confidence_df['window_size'] == selected_window].copy()
        
        # 정렬 옵션
        col_sort1, col_sort2 = st.columns(2)
        with col_sort1:
            sort_by = st.selectbox(
                "정렬 기준",
                options=['confidence', 'total_count', 'prefix'],
                format_func=lambda x: {'confidence': '신뢰도', 'total_count': '총 개수', 'prefix': 'Prefix'}[x],
                index=0
            )
        with col_sort2:
            sort_ascending = st.checkbox("오름차순", value=False)
        
        # 정렬
        window_data = window_data.sort_values(by=sort_by, ascending=sort_ascending)
        
        # 순서 번호 추가 (1부터 시작)
        window_data = window_data.reset_index(drop=True)
        window_data.insert(0, '순서', range(1, len(window_data) + 1))
        
        # 컬럼명 한글화
        display_data = window_data.copy()
        display_data = display_data.rename(columns={
            'window_size': '윈도우 크기',
            'prefix': 'Prefix',
            'b_count': 'B 개수',
            'p_count': 'P 개수',
            't_count': 'T 개수',
            'total_count': '총 개수',
            'b_ratio': 'B 비율',
            'p_ratio': 'P 비율',
            't_ratio': 'T 비율',
            'confidence': '신뢰도',
            'most_common_suffix': '가장 빈도 높은 Suffix',
            'most_common_count': '가장 빈도 높은 개수',
            'most_common_ratio': '가장 빈도 높은 비율'
        })
        
        # 숫자 포맷팅
        display_data['B 비율'] = display_data['B 비율'].apply(lambda x: f"{x:.4f}")
        display_data['P 비율'] = display_data['P 비율'].apply(lambda x: f"{x:.4f}")
        display_data['T 비율'] = display_data['T 비율'].apply(lambda x: f"{x:.4f}")
        display_data['신뢰도'] = display_data['신뢰도'].apply(lambda x: f"{x:.4f}")
        display_data['가장 빈도 높은 비율'] = display_data['가장 빈도 높은 비율'].apply(lambda x: f"{x:.4f}")
        
        # 컬럼 순서 조정 (순서가 가장 왼쪽에 오도록)
        column_order = ['순서'] + [col for col in display_data.columns if col != '순서']
        display_data = display_data[column_order]
        
        st.dataframe(
            display_data,
            use_container_width=True,
            hide_index=True
        )
        
        st.info(f"총 {len(window_data)}개의 prefix에 대한 신뢰도 정보를 표시합니다.")
    
    st.markdown("---")
    
    # 전체 데이터 다운로드
    st.header("💾 데이터 다운로드")
    
    col_dl1, col_dl2 = st.columns(2)
    
    with col_dl1:
        # 신뢰도 데이터 다운로드
        csv_confidence = confidence_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 신뢰도 데이터 다운로드 (CSV)",
            data=csv_confidence,
            file_name=f"suffix_confidence_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )
    
    with col_dl2:
        # 요약 통계 다운로드
        csv_summary = summary_df.to_csv(index=False).encode('utf-8-sig')
        st.download_button(
            label="📥 요약 통계 다운로드 (CSV)",
            data=csv_summary,
            file_name=f"suffix_confidence_summary_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}.csv",
            mime="text/csv"
        )


if __name__ == "__main__":
    main()
