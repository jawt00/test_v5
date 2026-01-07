import streamlit as st
import sqlite3
import pandas as pd
import os
from datetime import datetime

# 페이지 설정
st.set_page_config(
    page_title="Game Outcome Summary V2 Viewer",
    page_icon="📊",
    layout="wide"
)

# 데이터베이스 연결
def get_db_connection():
    """데이터베이스 연결"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'pattern_analysis_v4.db')
        if not os.path.exists(db_path):
            st.error(f"데이터베이스 파일을 찾을 수 없습니다: {db_path}")
            return None
        return sqlite3.connect(db_path)
    except Exception as e:
        st.error(f"데이터베이스 연결 오류: {str(e)}")
        return None

# 데이터 로드 (캐시 없이 매번 최신 데이터 로드)
def load_game_outcome_v2_data():
    """game_outcome_summary_v2 테이블에서 데이터 로드"""
    try:
        conn = get_db_connection()
        if conn is None:
            return pd.DataFrame()
        
        query = """
            SELECT 
                id,
                session_id,
                sequence_prediction_results,
                reconstructed_sequence_prediction_results,
                reconstructed_gap_results,
                converted_grid,
                reconstructed_grid,
                created_at
            FROM game_outcome_summary_v2
            ORDER BY created_at DESC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except sqlite3.OperationalError as e:
        if "no such table" in str(e).lower():
            st.error("테이블 'game_outcome_summary_v2'가 존재하지 않습니다.")
        else:
            st.error(f"데이터베이스 쿼리 오류: {str(e)}")
        return pd.DataFrame()
    except Exception as e:
        st.error(f"데이터 로드 오류: {str(e)}")
        return pd.DataFrame()

# 분석 함수들
def analyze_prediction_comparison(df):
    """
    두 예측 결과를 비교하여 분석
    sequence_prediction_results와 reconstructed_sequence_prediction_results 비교
    """
    analysis_results = {
        'first_match_position': [],  # 첫 번째로 같은 문자가 나오는 위치
        'max_match_position': [],  # 최대 몇 번째 위치까지 같은 문자가 나왔는지
        'position_match': {1: [], 2: [], 3: []},  # 위치별로 같은 문자인지 (True/False)
        'first_different_match_position': []  # 첫 문자가 다른 경우, 몇 번째에서 같은 문자가 나오는지
    }
    
    valid_comparisons = 0
    session_info_list = []  # 세션 정보 저장 (순서 유지)
    
    for idx, row in df.iterrows():
        seq1 = str(row.get('sequence_prediction_results', '')) if pd.notna(row.get('sequence_prediction_results')) else ''
        seq2 = str(row.get('reconstructed_sequence_prediction_results', '')) if pd.notna(row.get('reconstructed_sequence_prediction_results')) else ''
        
        # 빈 문자열이면 스킵
        if not seq1 or not seq2:
            continue
        
        # 문자열 길이 맞추기 (짧은 길이 기준)
        min_len = min(len(seq1), len(seq2))
        seq1 = seq1[:min_len]
        seq2 = seq2[:min_len]
        
        # 결과 길이가 2미만인 것은 유효하지 않아서 제거
        if min_len < 2:
            continue
        
        valid_comparisons += 1
        
        # 1. 첫 번째로 같은 문자가 나오는 위치 찾기
        first_match_pos = None
        for i in range(min_len):
            if seq1[i] == seq2[i]:
                first_match_pos = i + 1  # 1-based index
                break
        analysis_results['first_match_position'].append(first_match_pos if first_match_pos else None)
        
        # 2. 최대 몇 번째 위치까지 같은 문자가 나왔는지
        max_match_pos = 0
        for i in range(min_len):
            if seq1[i] == seq2[i]:
                max_match_pos = i + 1  # 1-based index
            else:
                break  # 연속된 매칭만 카운트
        analysis_results['max_match_position'].append(max_match_pos)
        
        # 3. 위치별로 같은 문자인지 (첫 번째, 두 번째, 세 번째)
        for pos in [1, 2, 3]:
            if pos <= min_len:
                is_match = (seq1[pos-1] == seq2[pos-1])
                analysis_results['position_match'][pos].append(is_match)
        
        # 4. 첫 문자가 다른 경우, 몇 번째에서 같은 문자가 나오는지
        if seq1[0] != seq2[0]:
            match_pos = None
            for i in range(1, min_len):  # 두 번째 문자부터 시작
                if seq1[i] == seq2[i]:
                    match_pos = i + 1  # 1-based index
                    break
            analysis_results['first_different_match_position'].append(match_pos)
        
        # 5. 교차 패턴 분석 (첫 번째 결과에 따라 다음 패턴이 바뀌는 위치 추적)
        # min_len >= 2이므로 바로 진행
        first_same = (seq1[0] == seq2[0])
        converted_within_3 = False  # 3번째까지 전환 여부
        
        # 첫 번째가 같을 때 → 다음에 다른 문자가 나오는 위치 찾기
        if first_same:
            different_pos = None
            for i in range(1, min_len):
                if seq1[i] != seq2[i]:  # 다른 문자가 나오는 위치
                    different_pos = i + 1  # 1-based index
                    break
            analysis_results.setdefault('first_same_next_different_position', []).append(different_pos)
            
            # 3번째까지 전환 여부 확인 (2, 3번째 중 하나라도 다르면 전환 성공)
            if min_len >= 2 and seq1[1] != seq2[1]:
                converted_within_3 = True
            elif min_len >= 3 and seq1[2] != seq2[2]:
                converted_within_3 = True
        else:
            # 첫 번째가 다를 때 → 다음에 같은 문자가 나오는 위치 찾기
            same_pos = None
            for i in range(1, min_len):
                if seq1[i] == seq2[i]:  # 같은 문자가 나오는 위치
                    same_pos = i + 1  # 1-based index
                    break
            analysis_results.setdefault('first_different_next_same_position', []).append(same_pos)
            
            # 3번째까지 전환 여부 확인 (2, 3번째 중 하나라도 같으면 전환 성공)
            if min_len >= 2 and seq1[1] == seq2[1]:
                converted_within_3 = True
            elif min_len >= 3 and seq1[2] == seq2[2]:
                converted_within_3 = True
        
        # 세션별 3번째까지 전환 여부 저장 (순서 유지)
        # 최대연속실패 분석에는 길이 3 이상인 것만 포함
        if min_len >= 3:
            analysis_results.setdefault('converted_within_3_by_session', []).append(converted_within_3)
        else:
            # 길이 3 미만인 경우 최대연속실패 분석에서 제외 (전환 성공으로 처리하지 않음)
            # 즉, 이 경우는 최대연속실패 분석에 포함되지 않음
            pass
        
        # 세션 정보 저장 (순서 유지) - 생성시간 포함, 길이 정보도 저장
        session_info_list.append({
            'id': row.get('id'),
            'session_id': row.get('session_id'),
            'created_at': row.get('created_at'),
            'sequence_prediction_results': seq1,
            'reconstructed_sequence_prediction_results': seq2,
            'converted_within_3': converted_within_3,
            'min_len': min_len  # 길이 정보 저장
        })
    
    # 세션 정보를 분석 결과에 저장
    analysis_results['session_info_list'] = session_info_list
    
    return analysis_results, valid_comparisons

def calculate_statistics(analysis_results, valid_comparisons):
    """분석 결과로부터 통계 계산"""
    stats = {}
    
    # 1. 첫 번째로 같은 문자가 나오는 위치 통계
    first_match_positions = [pos for pos in analysis_results['first_match_position'] if pos is not None]
    if first_match_positions:
        stats['first_match'] = {
            'mean': sum(first_match_positions) / len(first_match_positions),
            'min': min(first_match_positions),
            'max': max(first_match_positions),
            'distribution': {i: first_match_positions.count(i) for i in range(1, max(first_match_positions) + 1) if i in first_match_positions}
        }
    else:
        stats['first_match'] = None
    
    # 2. 최대 몇 번째 위치까지 같은 문자가 나왔는지
    max_match_positions = analysis_results['max_match_position']
    if max_match_positions:
        stats['max_match'] = {
            'mean': sum(max_match_positions) / len(max_match_positions),
            'min': min(max_match_positions),
            'max': max(max_match_positions),
            'distribution': {i: max_match_positions.count(i) for i in range(1, max(max_match_positions) + 1) if i in max_match_positions}
        }
    else:
        stats['max_match'] = None
    
    # 3. 위치별로 같은 문자인지 비율
    stats['position_match_rate'] = {}
    for pos in [1, 2, 3]:
        matches = analysis_results['position_match'][pos]
        if matches:
            match_count = sum(matches)
            total_count = len(matches)
            stats['position_match_rate'][pos] = {
                'match_count': match_count,
                'total_count': total_count,
                'rate': (match_count / total_count * 100) if total_count > 0 else 0
            }
        else:
            stats['position_match_rate'][pos] = None
    
    # 4. 첫 문자가 다른 경우, 몇 번째에서 같은 문자가 나오는지
    first_different_matches = [pos for pos in analysis_results['first_different_match_position'] if pos is not None]
    if first_different_matches:
        stats['first_different_match'] = {
            'mean': sum(first_different_matches) / len(first_different_matches),
            'min': min(first_different_matches),
            'max': max(first_different_matches),
            'distribution': {i: first_different_matches.count(i) for i in range(1, max(first_different_matches) + 1) if i in first_different_matches},
            'no_match_count': analysis_results['first_different_match_position'].count(None)
        }
    else:
        stats['first_different_match'] = {
            'no_match_count': len(analysis_results['first_different_match_position'])
        }
    
    stats['valid_comparisons'] = valid_comparisons
    
    return stats

def analyze_max_position_strategy(stats, analysis_results):
    """
    최대 위치 분석을 통한 전략 인사이트 도출
    """
    insights = {
        'strategy_type': None,  # 'same' or 'different'
        'confidence_level': None,  # 'high', 'medium', 'low'
        'recommended_approach': [],
        'risk_assessment': [],
        'detailed_analysis': {}
    }
    
    if not stats['max_match'] or not stats['position_match_rate']:
        return insights
    
    max_match_stats = stats['max_match']
    position_rates = stats['position_match_rate']
    
    # 평균 최대 위치
    avg_max_pos = max_match_stats['mean']
    max_max_pos = max_match_stats['max']
    
    # 위치별 일치율
    pos1_rate = position_rates[1]['rate'] if position_rates[1] else 0
    pos2_rate = position_rates[2]['rate'] if position_rates[2] else 0
    pos3_rate = position_rates[3]['rate'] if position_rates[3] else 0
    
    # 최대 위치 분포 분석
    max_pos_distribution = max_match_stats.get('distribution', {})
    pos3_count = max_pos_distribution.get(3, 0)
    pos2_count = max_pos_distribution.get(2, 0)
    pos1_count = max_pos_distribution.get(1, 0)
    pos0_count = max_pos_distribution.get(0, 0) if 0 in max_pos_distribution else 0
    total_count = len(analysis_results['max_match_position'])
    
    # 상세 분석 저장
    insights['detailed_analysis'] = {
        'avg_max_position': avg_max_pos,
        'max_max_position': max_max_pos,
        'position_rates': {
            1: pos1_rate,
            2: pos2_rate,
            3: pos3_rate
        },
        'max_position_distribution': max_pos_distribution,
        'pos3_ratio': (pos3_count / total_count * 100) if total_count > 0 else 0,
        'pos2_or_below_ratio': ((pos2_count + pos1_count + pos0_count) / total_count * 100) if total_count > 0 else 0
    }
    
    # 전략 결정 로직
    # 1. 최대 위치가 3일 때의 의미 분석
    if max_max_pos == 3:
        pos3_ratio = (pos3_count / total_count * 100) if total_count > 0 else 0
        
        # 최대 위치가 3인 경우가 많고, 위치별 일치율이 높으면 -> 같은 문자 전략
        if pos3_ratio >= 30 and pos1_rate >= 50:
            insights['strategy_type'] = 'same'
            insights['confidence_level'] = 'high' if pos3_ratio >= 50 else 'medium'
            insights['recommended_approach'].append(
                f"✅ **같은 문자 접근 전략 추천**: 최대 위치 3인 경우가 {pos3_ratio:.1f}%로 높고, "
                f"1번째 위치 일치율이 {pos1_rate:.1f}%입니다. 처음부터 같은 문자로 예측하는 전략이 유효할 가능성이 높습니다."
            )
        # 최대 위치가 3이지만 전체적으로 일치율이 낮으면 -> 주의 필요
        elif pos3_ratio < 30 or pos1_rate < 50:
            insights['strategy_type'] = 'mixed'
            insights['confidence_level'] = 'medium' if pos1_rate >= 40 else 'low'
            insights['recommended_approach'].append(
                f"⚠️ **혼합 전략 권장**: 최대 위치가 3이지만 전체 일치율이 낮습니다. "
                f"1번째 위치 일치율 {pos1_rate:.1f}%, 최대 위치 3인 경우 {pos3_ratio:.1f}%. "
                f"보수적인 접근이 필요합니다."
            )
    
    # 2. 평균 최대 위치 분석
    if avg_max_pos >= 2.5:
        insights['recommended_approach'].append(
            f"💡 평균 최대 위치가 {avg_max_pos:.2f}로 높습니다. 처음 2-3개 예측에 신뢰를 둘 수 있습니다."
        )
        if insights['strategy_type'] is None:
            insights['strategy_type'] = 'same'
            insights['confidence_level'] = 'high'
    elif avg_max_pos >= 1.5:
        insights['recommended_approach'].append(
            f"📊 평균 최대 위치가 {avg_max_pos:.2f}입니다. 첫 1-2개 예측에만 신뢰를 두고 이후는 주의해야 합니다."
        )
    else:
        insights['recommended_approach'].append(
            f"⚠️ 평균 최대 위치가 {avg_max_pos:.2f}로 낮습니다. 예측 신뢰도가 낮을 수 있습니다."
        )
        if insights['strategy_type'] is None:
            insights['strategy_type'] = 'different'
            insights['confidence_level'] = 'low'
    
    # 3. 위치별 일치율 종합 분석
    avg_position_rate = (pos1_rate + pos2_rate + pos3_rate) / 3
    if avg_position_rate >= 60:
        insights['recommended_approach'].append(
            f"🎯 전체 평균 일치율이 {avg_position_rate:.1f}%로 높습니다. 같은 문자 접근 전략이 유효할 가능성이 높습니다."
        )
    elif avg_position_rate >= 40:
        insights['recommended_approach'].append(
            f"📈 전체 평균 일치율이 {avg_position_rate:.1f}%입니다. 조건부로 같은 문자 접근을 고려할 수 있습니다."
        )
    else:
        insights['recommended_approach'].append(
            f"⚠️ 전체 평균 일치율이 {avg_position_rate:.1f}%로 낮습니다. 다른 문자 접근을 고려하거나 매우 보수적인 전략이 필요합니다."
        )
    
    # 4. 리스크 평가
    if pos1_rate < 50:
        insights['risk_assessment'].append(
            f"🔴 **고위험**: 1번째 위치 일치율이 {pos1_rate:.1f}%로 50% 미만입니다. 첫 예측 신뢰도가 낮습니다."
        )
    elif pos1_rate < 60:
        insights['risk_assessment'].append(
            f"🟡 **중위험**: 1번째 위치 일치율이 {pos1_rate:.1f}%입니다. 첫 예측에 주의가 필요합니다."
        )
    else:
        insights['risk_assessment'].append(
            f"🟢 **저위험**: 1번째 위치 일치율이 {pos1_rate:.1f}%로 높습니다. 첫 예측 신뢰도가 높습니다."
        )
    
    # 최대 위치 3인 경우의 특별 분석
    if max_max_pos == 3:
        pos2_or_below = pos2_count + pos1_count + pos0_count
        pos2_or_below_ratio = (pos2_or_below / total_count * 100) if total_count > 0 else 0
        
        if pos2_or_below_ratio > 50:
            insights['recommended_approach'].append(
                f"⚠️ 최대 위치가 2 이하인 경우가 {pos2_or_below_ratio:.1f}%로 많습니다. "
                f"최대 위치 3이라고 해도 실제로는 2번째 위치까지만 일치하는 경우가 많으므로, "
                f"3번째 위치 이후의 예측은 신뢰하지 않는 것이 좋습니다."
            )
        else:
            insights['recommended_approach'].append(
                f"✅ 최대 위치가 3인 경우가 {pos3_ratio:.1f}%로 높습니다. "
                f"처음 3개 위치까지 일치하는 경우가 많으므로, 3번째 위치까지는 같은 문자 전략을 고려할 수 있습니다."
            )
    
    return insights

def analyze_cross_pattern_strategy(analysis_results, stats, df_filtered=None):
    """
    교차 패턴 전략 분석
    전략: 첫 번째 결과를 확인하고, 같으면 다음은 다를 것으로, 다르면 다음은 같을 것으로 접근
    위치 정보를 포함한 상세 분석
    """
    strategy_analysis = {
        'strategy_name': '교차 패턴 전략',
        'description': '첫 번째 결과가 같으면 → 다음에 다른 문자가 나오는 위치\n첫 번째 결과가 다르면 → 다음에 같은 문자가 나오는 위치',
        'first_same_next_different': None,  # 첫 번째가 같을 때 다음에 다른 문자가 나오는 위치 분석
        'first_different_next_same': None,  # 첫 번째가 다를 때 다음에 같은 문자가 나오는 위치 분석
        'recommendation': None,
        'comparison_with_same_strategy': None
    }
    
    # 첫 번째가 같을 때 → 다음에 다른 문자가 나오는 위치 분석
    first_same_positions = [pos for pos in analysis_results.get('first_same_next_different_position', []) if pos is not None]
    if first_same_positions:
        total_count = len(analysis_results.get('first_same_next_different_position', []))
        no_change_count = analysis_results.get('first_same_next_different_position', []).count(None)
        
        strategy_analysis['first_same_next_different'] = {
            'total_count': total_count,
            'found_count': len(first_same_positions),
            'no_change_count': no_change_count,
            'no_change_rate': (no_change_count / total_count * 100) if total_count > 0 else 0,
            'mean_position': sum(first_same_positions) / len(first_same_positions) if first_same_positions else None,
            'min_position': min(first_same_positions) if first_same_positions else None,
            'max_position': max(first_same_positions) if first_same_positions else None,
            'distribution': {i: first_same_positions.count(i) for i in sorted(set(first_same_positions))}
        }
    else:
        first_same_all = analysis_results.get('first_same_next_different_position', [])
        if first_same_all:
            strategy_analysis['first_same_next_different'] = {
                'total_count': len(first_same_all),
                'found_count': 0,
                'no_change_count': first_same_all.count(None),
                'no_change_rate': 100.0,
                'mean_position': None,
                'min_position': None,
                'max_position': None,
                'distribution': {}
            }
    
    # 첫 번째가 다를 때 → 다음에 같은 문자가 나오는 위치 분석
    first_different_positions = [pos for pos in analysis_results.get('first_different_next_same_position', []) if pos is not None]
    if first_different_positions:
        total_count = len(analysis_results.get('first_different_next_same_position', []))
        no_change_count = analysis_results.get('first_different_next_same_position', []).count(None)
        
        strategy_analysis['first_different_next_same'] = {
            'total_count': total_count,
            'found_count': len(first_different_positions),
            'no_change_count': no_change_count,
            'no_change_rate': (no_change_count / total_count * 100) if total_count > 0 else 0,
            'mean_position': sum(first_different_positions) / len(first_different_positions) if first_different_positions else None,
            'min_position': min(first_different_positions) if first_different_positions else None,
            'max_position': max(first_different_positions) if first_different_positions else None,
            'distribution': {i: first_different_positions.count(i) for i in sorted(set(first_different_positions))}
        }
    else:
        first_different_all = analysis_results.get('first_different_next_same_position', [])
        if first_different_all:
            strategy_analysis['first_different_next_same'] = {
                'total_count': len(first_different_all),
                'found_count': 0,
                'no_change_count': first_different_all.count(None),
                'no_change_rate': 100.0,
                'mean_position': None,
                'min_position': None,
                'max_position': None,
                'distribution': {}
            }
    
    # 추천 평가
    recommendations = []
    
    if strategy_analysis['first_same_next_different']:
        data = strategy_analysis['first_same_next_different']
        if data['mean_position']:
            recommendations.append(
                f"✅ **첫 번째가 같을 때**: 평균 {data['mean_position']:.1f}번째 위치에서 다른 문자가 나옵니다. "
                f"(총 {data['found_count']}건 중 평균 위치 {data['mean_position']:.1f})"
            )
            if data['mean_position'] <= 2:
                recommendations.append(
                    f"💡 첫 번째가 같으면, 2번째 위치에서 다른 문자가 나올 가능성이 높습니다. 빠르게 전환할 수 있습니다."
                )
        if data['no_change_rate'] > 50:
            recommendations.append(
                f"⚠️ 첫 번째가 같을 때 패턴이 바뀌지 않는 경우가 {data['no_change_rate']:.1f}%로 높습니다."
            )
    
    if strategy_analysis['first_different_next_same']:
        data = strategy_analysis['first_different_next_same']
        if data['mean_position']:
            recommendations.append(
                f"✅ **첫 번째가 다를 때**: 평균 {data['mean_position']:.1f}번째 위치에서 같은 문자가 나옵니다. "
                f"(총 {data['found_count']}건 중 평균 위치 {data['mean_position']:.1f})"
            )
            if data['mean_position'] <= 2:
                recommendations.append(
                    f"💡 첫 번째가 다르면, 2번째 위치에서 같은 문자가 나올 가능성이 높습니다. 빠르게 일치할 수 있습니다."
                )
        if data['no_change_rate'] > 50:
            recommendations.append(
                f"⚠️ 첫 번째가 다를 때 패턴이 바뀌지 않는 경우가 {data['no_change_rate']:.1f}%로 높습니다."
            )
    
    if recommendations:
        strategy_analysis['recommendation'] = {
            'level': 'medium',
            'messages': recommendations,
            'confidence': '중간'
        }
    
    # 3번째까지 전환되지 않는 연속 세션 분석
    # 모든 세션의 3번째까지 전환 여부 확인 (순서 유지)
    all_converted_within_3 = analysis_results.get('converted_within_3_by_session', [])
    
    if all_converted_within_3:
        # 연속 실패 추적
        max_consecutive_failures = 0
        current_consecutive_failures = 0
        consecutive_failure_sequences = []
        current_sequence = 0
        
        for converted in all_converted_within_3:
            if not converted:  # 전환 실패
                current_consecutive_failures += 1
                current_sequence += 1
            else:  # 전환 성공
                if current_consecutive_failures > 0:
                    consecutive_failure_sequences.append(current_consecutive_failures)
                max_consecutive_failures = max(max_consecutive_failures, current_consecutive_failures)
                current_consecutive_failures = 0
                current_sequence = 0
        
        # 마지막 시퀀스 추가
        if current_consecutive_failures > 0:
            consecutive_failure_sequences.append(current_consecutive_failures)
            max_consecutive_failures = max(max_consecutive_failures, current_consecutive_failures)
        
        # 통계 계산
        total_failures = sum([1 for x in all_converted_within_3 if not x])
        total_successes = sum([1 for x in all_converted_within_3 if x])
        total_count = len(all_converted_within_3)
        success_rate = (total_successes / total_count * 100) if total_count > 0 else 0
        
        avg_consecutive_failures = (sum(consecutive_failure_sequences) / len(consecutive_failure_sequences)) if consecutive_failure_sequences else 0
        
        # 최대 연속 실패 세션 찾기 (max_consecutive_failures 계산 후)
        # 최대 연속 실패가 여러개인 경우, 가장 최근 것만 표시
        # 데이터는 ORDER BY created_at DESC로 정렬되어 있으므로 인덱스 0이 가장 최신
        max_consecutive_failure_sessions = []
        session_info_list = analysis_results.get('session_info_list', [])
        
        # 길이 3 이상인 세션만 필터링 (최대연속실패 분석용)
        # all_converted_within_3와 인덱스가 일치하도록 길이 3 이상인 것만 추출
        valid_session_info_list = [s for s in session_info_list if s.get('min_len', 0) >= 3]
        
        # all_converted_within_3와 valid_session_info_list는 인덱스가 일치해야 함
        if len(valid_session_info_list) == len(all_converted_within_3) and valid_session_info_list and all_converted_within_3 and max_consecutive_failures > 0:
            # 최대 연속 실패가 발생한 위치 찾기 (가장 최근 것부터 찾기)
            # 데이터가 ORDER BY created_at DESC로 정렬되어 있으므로
            # 인덱스 0이 가장 최신이고, 뒤로 갈수록 오래된 데이터
            # 가장 최근 발생한 최대 연속 실패를 찾기 위해 앞에서부터 순회하면서 첫 번째 발견 시점 저장
            max_start_idx = -1
            max_end_idx = -1
            current_failures = 0
            current_start_idx = -1
            
            for idx, converted in enumerate(all_converted_within_3):
                if not converted:  # 전환 실패
                    if current_failures == 0:
                        current_start_idx = idx
                    current_failures += 1
                    
                    # 최대 연속 실패를 발견한 경우 (가장 최근 것, 즉 첫 번째 발견만 저장)
                    if current_failures == max_consecutive_failures and max_start_idx == -1:
                        max_start_idx = current_start_idx
                        max_end_idx = idx
                        # 가장 최근 것을 찾았으므로 더 이상 갱신하지 않음
                else:  # 전환 성공
                    current_failures = 0
                    current_start_idx = -1
            
            # 최대 연속 실패 세션 정보 추출 (길이 3 이상인 세션만)
            # 인덱스가 일치하므로 valid_session_info_list에서 직접 추출
            if max_start_idx >= 0 and max_end_idx >= max_start_idx:
                for idx in range(max_start_idx, max_end_idx + 1):
                    if idx < len(valid_session_info_list):
                        max_consecutive_failure_sessions.append(valid_session_info_list[idx])
        
        strategy_analysis['conversion_within_3_analysis'] = {
            'total_sessions': total_count,
            'success_count': total_successes,
            'failure_count': total_failures,
            'success_rate': success_rate,
            'max_consecutive_failures': max_consecutive_failures,
            'avg_consecutive_failures': avg_consecutive_failures,
            'consecutive_failure_sequences': consecutive_failure_sequences,
            'failure_distribution': {i: consecutive_failure_sequences.count(i) for i in sorted(set(consecutive_failure_sequences))} if consecutive_failure_sequences else {},
            'max_consecutive_failure_sessions': max_consecutive_failure_sessions  # 최대 연속 실패 세션 정보
        }
    
    return strategy_analysis

def analyze_next_session_match_strategy(analysis_results):
    """
    새로운 전략 분석: 단일 세션 내에서 다른 문자가 나온 경우 다음 위치에서 같은 문자가 나오는지 확인
    1. 첫번째가 다른 경우 → 그 세션 내에서 다음 위치(두번째, 세번째 등)에서 같은 문자가 나오는지
    2. 첫번째가 같고 두번째가 다른 경우 → 그 세션 내에서 다음 위치(세번째 등)에서 같은 문자가 나오는지
    """
    strategy_analysis = {
        'strategy_name': '다음 위치 일치 전략',
        'description': '다른 문자가 나온 경우 다음 위치에서 같은 문자가 나올 것으로 예상',
        'first_different_next_match': None,  # 첫번째가 다른 경우
        'first_same_second_different_next_match': None,  # 첫번째가 같고 두번째가 다른 경우
    }
    
    session_info_list = analysis_results.get('session_info_list', [])
    
    if not session_info_list:
        return strategy_analysis
    
    # 1. 첫번째가 다른 경우 → 그 세션 내에서 다음 위치에서 같은 문자가 나오는지
    first_different_cases = []
    
    # 2. 첫번째가 같고 두번째가 다른 경우 → 그 세션 내에서 다음 위치에서 같은 문자가 나오는지
    first_same_second_different_cases = []
    
    # 각 세션 내에서 패턴 분석
    for session_info in session_info_list:
        seq1 = session_info.get('sequence_prediction_results', '')
        seq2 = session_info.get('reconstructed_sequence_prediction_results', '')
        
        if not seq1 or not seq2:
            continue
        
        min_len = min(len(seq1), len(seq2))
        if min_len < 2:
            continue
        
        # 1. 첫번째가 다른 경우
        if seq1[0] != seq2[0]:
            # 첫번째가 다른 상태에서, 다음 같은 문자가 처음 나오는 위치 찾기
            # 예: WWLWWLLW vs LWWLWWLW
            # - 첫번째: W != L (다름)
            # - 두번째: W == W (같음) → 다음 같은 문자가 처음 나오는 위치 = 2
            # - 최대 위치 = 2 (다음 같은 문자가 처음 나오는 위치)
            next_match_position = None
            next_match_found = False
            for i in range(1, min_len):
                if seq1[i] == seq2[i]:
                    next_match_found = True
                    next_match_position = i + 1  # 1-based index (다음 같은 문자가 처음 나오는 위치)
                    break
            
            # 최대 위치 = 다음 같은 문자가 처음 나오는 위치
            max_match_pos = next_match_position if next_match_position is not None else 0
            
            first_different_cases.append({
                'session': session_info,
                'next_match_found': next_match_found,
                'next_match_position': next_match_position,
                'max_position': max_match_pos,  # 다음 같은 문자가 처음 나오는 위치
                'current_pattern': '첫번째 다름'
            })
        
        # 2. 첫번째가 같고 두번째가 다른 경우
        elif min_len >= 2 and seq1[0] == seq2[0] and seq1[1] != seq2[1]:
            # 첫번째가 같고 두번째가 다른 상태에서, 다음 같은 문자가 처음 나오는 위치 찾기
            # 예: WLLLL vs WWWWL
            # - 첫번째: W == W (같음)
            # - 두번째: L != W (다름)
            # - 세번째: L != W (다름)
            # - 네번째: L != W (다름)
            # - 다섯번째: L == L (같음) → 다음 같은 문자가 처음 나오는 위치 = 5
            # - 최대 위치 = 5 (다음 같은 문자가 처음 나오는 위치)
            next_match_position = None
            next_match_found = False
            for i in range(2, min_len):  # 세번째 위치부터 확인
                if seq1[i] == seq2[i]:
                    next_match_found = True
                    next_match_position = i + 1  # 1-based index (다음 같은 문자가 처음 나오는 위치)
                    break
            
            # 최대 위치 = 다음 같은 문자가 처음 나오는 위치
            max_match_pos = next_match_position if next_match_position is not None else 0
            
            first_same_second_different_cases.append({
                'session': session_info,
                'next_match_found': next_match_found,
                'next_match_position': next_match_position,
                'max_position': max_match_pos,  # 다음 같은 문자가 처음 나오는 위치
                'current_pattern': '첫번째 같음, 두번째 다름'
            })
    
    # 1. 첫번째가 다른 경우 분석
    if first_different_cases:
        # 다음 일치 위치 통계 계산
        next_match_positions = [case['next_match_position'] for case in first_different_cases if case['next_match_found']]
        no_match_count = len([case for case in first_different_cases if not case['next_match_found']])
        
        # 통계 계산
        avg_position = sum(next_match_positions) / len(next_match_positions) if next_match_positions else 0
        min_position = min(next_match_positions) if next_match_positions else None
        max_position = max([case['max_position'] for case in first_different_cases], default=0)
        
        # 다음 일치 위치 분포 계산
        position_distribution = {}
        for pos in next_match_positions:
            position_distribution[pos] = position_distribution.get(pos, 0) + 1
        
        # 중앙값 계산
        sorted_positions = sorted(next_match_positions) if next_match_positions else []
        median_position = sorted_positions[len(sorted_positions) // 2] if sorted_positions else None
        
        # 최신 최대 위치 세션 찾기 (데이터는 최신순이므로 첫 번째가 최신)
        max_position_case = None
        for case in first_different_cases:
            if case['max_position'] == max_position:
                max_position_case = case
                break  # 첫 번째가 최신이므로 바로 찾으면 중단
        
        strategy_analysis['first_different_next_match'] = {
            'total_cases': len(first_different_cases),
            'max_position': max_position,  # 최대 위치
            'avg_position': avg_position,  # 평균 다음 일치 위치
            'min_position': min_position,  # 최소 다음 일치 위치
            'median_position': median_position,  # 중앙값 다음 일치 위치
            'no_match_count': no_match_count,  # 다음 일치가 없는 케이스 수
            'position_distribution': position_distribution,  # 다음 일치 위치 분포
            'max_position_case': max_position_case,  # 최신 최대 위치 세션
            'cases': first_different_cases
        }
    
    # 2. 첫번째가 같고 두번째가 다른 경우 분석
    if first_same_second_different_cases:
        # 다음 일치 위치 통계 계산
        next_match_positions = [case['next_match_position'] for case in first_same_second_different_cases if case['next_match_found']]
        no_match_count = len([case for case in first_same_second_different_cases if not case['next_match_found']])
        
        # 통계 계산
        avg_position = sum(next_match_positions) / len(next_match_positions) if next_match_positions else 0
        min_position = min(next_match_positions) if next_match_positions else None
        max_position = max([case['max_position'] for case in first_same_second_different_cases], default=0)
        
        # 다음 일치 위치 분포 계산
        position_distribution = {}
        for pos in next_match_positions:
            position_distribution[pos] = position_distribution.get(pos, 0) + 1
        
        # 중앙값 계산
        sorted_positions = sorted(next_match_positions) if next_match_positions else []
        median_position = sorted_positions[len(sorted_positions) // 2] if sorted_positions else None
        
        # 최신 최대 위치 세션 찾기 (데이터는 최신순이므로 첫 번째가 최신)
        max_position_case = None
        for case in first_same_second_different_cases:
            if case['max_position'] == max_position:
                max_position_case = case
                break  # 첫 번째가 최신이므로 바로 찾으면 중단
        
        strategy_analysis['first_same_second_different_next_match'] = {
            'total_cases': len(first_same_second_different_cases),
            'max_position': max_position,  # 최대 위치
            'avg_position': avg_position,  # 평균 다음 일치 위치
            'min_position': min_position,  # 최소 다음 일치 위치
            'median_position': median_position,  # 중앙값 다음 일치 위치
            'no_match_count': no_match_count,  # 다음 일치가 없는 케이스 수
            'position_distribution': position_distribution,  # 다음 일치 위치 분포
            'max_position_case': max_position_case,  # 최신 최대 위치 세션
            'cases': first_same_second_different_cases
        }
    
    return strategy_analysis

# 메인 앱
def main():
    st.title("Game Outcome Summary V2 Viewer")
    st.markdown("---")
    
    # 데이터 로드
    df_all = load_game_outcome_v2_data()
    
    if len(df_all) == 0:
        st.warning("⚠️ 데이터베이스에서 데이터를 불러올 수 없습니다.")
        return
    
    # 게임 테이블 목록 정의
    game_tables = {
        'K': ['K1', 'K2', 'K3', 'K5'],
        'C': ['C1', 'C2', 'C3'],
        'J': ['J1', 'J2', 'J3'],
        'T': ['T1', 'T2', 'T3'],
        'V': ['V1', 'V2'],
        'S': ['S1', 'S2', 'S3', 'S5', 'S6', 'S7', 'S8', 'S9', 'S10', 'S11', 'S12', 'S13', 'S15', 'S16', 'S17', 'S18']
    }
    
    # 모든 게임 테이블 목록 수집
    all_table_names = []
    for tables in game_tables.values():
        all_table_names.extend(tables)
    
    # 게임 테이블 체크리스트 섹션
    header_col1, header_col2 = st.columns([4, 1])
    with header_col1:
        st.markdown("### 🎮 게임 테이블 체크리스트")
        st.caption("게임이 완료된 테이블은 체크하세요.")
    with header_col2:
        st.markdown("<br>", unsafe_allow_html=True)  # 정렬을 위한 공간
        if st.button("🔄 리셋", use_container_width=True, key="reset_checkboxes"):
            # 모든 체크박스 키를 False로 리셋
            for table_name in all_table_names:
                checkbox_key = f"checkbox_{table_name}"
                st.session_state[checkbox_key] = False
            st.rerun()
    
    # 체크박스 스타일 적용 (전역) - 더 컴팩트하게
    st.markdown("""
        <style>
        .stCheckbox {
            margin-bottom: 0 !important;
            padding: 0 !important;
        }
        .stCheckbox label {
            padding-left: 0.5rem !important;
        }
        .stCheckbox label p {
            margin: 0 !important;
            font-size: 0.95em !important;
            line-height: 1.3 !important;
            white-space: nowrap !important;
        }
        .game-group-container {
            margin-bottom: 0.3rem !important;
        }
        .game-group-title {
            display: inline-block;
            margin-right: 0.5rem;
            font-size: 0.85em !important;
            font-weight: bold;
        }
        </style>
    """, unsafe_allow_html=True)
    
    # 모든 그룹을 수평으로 배치
    for group_name, tables in game_tables.items():
        # S 그룹은 3줄로 배치 (6, 6, 6개씩)
        if group_name == 'S':
            items_per_row = [6, 6, 6]
        else:
            # 다른 그룹은 한 줄에 모두 배치
            items_per_row = [len(tables)]
        
        row_start = 0
        for row_idx, items_in_row in enumerate(items_per_row):
            if row_start >= len(tables):
                break
            
            row_tables = tables[row_start:row_start + items_in_row]
            cols = st.columns([0.8] + [1] * len(row_tables))
            
            # 첫 번째 행에만 그룹 제목 표시
            if row_idx == 0:
                with cols[0]:
                    st.markdown(f"<div class='game-group-title'>{group_name}:</div>", unsafe_allow_html=True)
            else:
                with cols[0]:
                    st.markdown("", unsafe_allow_html=True)  # 빈 공간 (제목 위치)
            
            # 아이템들 표시
            for idx, table_name in enumerate(row_tables):
                with cols[idx + 1]:
                    checkbox_key = f"checkbox_{table_name}"
                    st.checkbox(
                        table_name,
                        value=st.session_state.get(checkbox_key, False),
                        key=checkbox_key
                    )
            
            row_start += items_in_row
    
    st.markdown("---")
    
    # 검색 섹션
    st.markdown("### 🔍 검색")
    
    search_col1, search_col2 = st.columns([3, 1])
    
    with search_col1:
        search_term = st.text_input(
            "검색어 입력",
            placeholder="session_id, id 등으로 검색할 수 있습니다",
            key="search_input"
        )
    
    with search_col2:
        search_button = st.button("검색", type="primary", use_container_width=True)
    
    # 검색 필터링
    if search_term:
        # 문자열 검색 (대소문자 무시)
        search_term_lower = search_term.lower()
        
        # 모든 컬럼에서 검색
        mask = pd.Series([False] * len(df_all))
        
        for col in df_all.columns:
            if df_all[col].dtype == 'object':  # 문자열 컬럼만 검색
                mask |= df_all[col].astype(str).str.lower().str.contains(search_term_lower, na=False)
            else:  # 숫자 컬럼
                mask |= df_all[col].astype(str).str.contains(search_term_lower, na=False)
        
        df_filtered = df_all[mask].copy()
    else:
        df_filtered = df_all.copy()
    
    # 필터링 후에도 최신 순서 유지 (created_at DESC)
    df_filtered = df_filtered.sort_values('created_at', ascending=False).reset_index(drop=True)
    
    # 검색 결과 표시
    if search_term:
        st.info(f"검색 결과: {len(df_filtered)}개 (전체: {len(df_all)}개)")
    
    st.markdown("---")
    
    # 예측 결과 비교 분석
    st.markdown("### 📊 예측 결과 비교 분석")
    
    # 분석 실행
    if len(df_filtered) > 0:
        analysis_results, valid_comparisons = analyze_prediction_comparison(df_filtered)
        stats = calculate_statistics(analysis_results, valid_comparisons)
        
        if valid_comparisons > 0:
            st.success(f"✅ 분석 완료: {valid_comparisons}개의 유효한 비교 데이터")
            
            # 1. 첫 번째로 같은 문자가 나오는 위치
            st.markdown("#### 1️⃣ 첫 번째로 같은 문자가 나오는 위치")
            if stats['first_match']:
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("평균 위치", f"{stats['first_match']['mean']:.2f}")
                with col2:
                    st.metric("최소 위치", stats['first_match']['min'])
                with col3:
                    st.metric("최대 위치", stats['first_match']['max'])
                with col4:
                    st.metric("분석 데이터 수", len(analysis_results['first_match_position']))
                
                # 분포 표시
                if stats['first_match']['distribution']:
                    st.markdown("**위치별 분포:**")
                    dist_df = pd.DataFrame([
                        {'위치': k, '횟수': v, '비율(%)': (v / len(analysis_results['first_match_position']) * 100)} 
                        for k, v in sorted(stats['first_match']['distribution'].items())
                    ])
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)
            else:
                st.warning("첫 번째로 같은 문자가 나오는 경우가 없습니다.")
            
            st.markdown("---")
            
            # 2. 최대 몇 번째 위치까지 같은 문자가 나왔는지
            st.markdown("#### 2️⃣ 최대 몇 번째 위치까지 같은 문자가 나왔는지")
            if stats['max_match']:
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("평균 최대 위치", f"{stats['max_match']['mean']:.2f}")
                with col2:
                    st.metric("최소 최대 위치", stats['max_match']['min'])
                with col3:
                    st.metric("최대 위치", stats['max_match']['max'])
                with col4:
                    st.metric("분석 데이터 수", len(analysis_results['max_match_position']))
                
                # 분포 표시
                if stats['max_match']['distribution']:
                    st.markdown("**위치별 분포:**")
                    dist_df = pd.DataFrame([
                        {'최대 위치': k, '횟수': v, '비율(%)': (v / len(analysis_results['max_match_position']) * 100)} 
                        for k, v in sorted(stats['max_match']['distribution'].items())
                    ])
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)
            else:
                st.warning("최대 위치 데이터가 없습니다.")
            
            st.markdown("---")
            
            # 3. 위치별로 같은 문자인지 비율
            st.markdown("#### 3️⃣ 위치별 문자 일치 비율")
            position_cols = st.columns(3)
            for idx, pos in enumerate([1, 2, 3]):
                with position_cols[idx]:
                    if stats['position_match_rate'][pos]:
                        rate_info = stats['position_match_rate'][pos]
                        st.metric(
                            f"{pos}번째 위치 일치율",
                            f"{rate_info['rate']:.2f}%",
                            f"{rate_info['match_count']}/{rate_info['total_count']}"
                        )
                    else:
                        st.metric(f"{pos}번째 위치 일치율", "N/A")
            
            st.markdown("---")
            
            # 4. 첫 문자가 다른 경우, 몇 번째에서 같은 문자가 나오는지
            st.markdown("#### 4️⃣ 첫 문자가 다른 경우, 같은 문자가 나오는 위치")
            if stats['first_different_match']:
                if 'mean' in stats['first_different_match']:
                    col1, col2, col3, col4 = st.columns(4)
                    with col1:
                        st.metric("평균 위치", f"{stats['first_different_match']['mean']:.2f}")
                    with col2:
                        st.metric("최소 위치", stats['first_different_match']['min'])
                    with col3:
                        st.metric("최대 위치", stats['first_different_match']['max'])
                    with col4:
                        no_match = stats['first_different_match'].get('no_match_count', 0)
                        total_first_different = len(analysis_results['first_different_match_position'])
                        st.metric("일치하지 않은 경우", f"{no_match}/{total_first_different}")
                    
                    # 분포 표시
                    if stats['first_different_match']['distribution']:
                        st.markdown("**위치별 분포:**")
                        dist_df = pd.DataFrame([
                            {'위치': k, '횟수': v, '비율(%)': (v / (total_first_different - no_match) * 100) if (total_first_different - no_match) > 0 else 0} 
                            for k, v in sorted(stats['first_different_match']['distribution'].items())
                        ])
                        st.dataframe(dist_df, use_container_width=True, hide_index=True)
                else:
                    st.info(f"첫 문자가 다른 경우: {stats['first_different_match'].get('no_match_count', 0)}개 (모두 일치하지 않음)")
            else:
                st.warning("첫 문자가 다른 경우의 데이터가 없습니다.")
            
            st.markdown("---")
            
            # 최대 위치 분석 인사이트
            st.markdown("#### 🔍 최대 위치 분석 인사이트")
            strategy_insights = analyze_max_position_strategy(stats, analysis_results)
            
            if strategy_insights['detailed_analysis']:
                detail = strategy_insights['detailed_analysis']
                
                # 핵심 지표 표시
                col1, col2, col3 = st.columns(3)
                with col1:
                    st.metric("평균 최대 위치", f"{detail['avg_max_position']:.2f}")
                with col2:
                    st.metric("최대 위치 3인 비율", f"{detail['pos3_ratio']:.1f}%")
                with col3:
                    st.metric("위치 2 이하 비율", f"{detail['pos2_or_below_ratio']:.1f}%")
                
                # 전략 유형 표시
                if strategy_insights['strategy_type']:
                    strategy_type_label = {
                        'same': '✅ 같은 문자 접근 전략',
                        'different': '⚠️ 다른 문자 접근 전략',
                        'mixed': '📊 혼합 전략'
                    }
                    confidence_label = {
                        'high': '🟢 높음',
                        'medium': '🟡 중간',
                        'low': '🔴 낮음'
                    }
                    
                    st.markdown(f"**권장 전략 유형**: {strategy_type_label.get(strategy_insights['strategy_type'], '미정')}")
                    if strategy_insights['confidence_level']:
                        st.markdown(f"**신뢰도**: {confidence_label.get(strategy_insights['confidence_level'], '미정')}")
                
                st.markdown("---")
                
                # 권장 접근 방법
                if strategy_insights['recommended_approach']:
                    st.markdown("**💡 권장 접근 방법:**")
                    for approach in strategy_insights['recommended_approach']:
                        st.markdown(approach)
                
                # 리스크 평가
                if strategy_insights['risk_assessment']:
                    st.markdown("**⚠️ 리스크 평가:**")
                    for risk in strategy_insights['risk_assessment']:
                        st.markdown(risk)
            
            st.markdown("---")
            
            # 교차 패턴 전략 분석
            st.markdown("#### 🔄 교차 패턴 전략 평가")
            cross_strategy = analyze_cross_pattern_strategy(analysis_results, stats, df_filtered)
            
            st.markdown(f"**전략 설명**: {cross_strategy['description']}")
            st.markdown("---")
            
            # 첫 번째가 같을 때 → 다음에 다른 문자가 나오는 위치 분석
            if cross_strategy['first_same_next_different']:
                data1 = cross_strategy['first_same_next_different']
                st.markdown("#### 첫 번째가 같을 때 → 다음에 다른 문자가 나오는 위치")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("평균 위치", f"{data1['mean_position']:.2f}" if data1['mean_position'] else "N/A")
                with col2:
                    st.metric("최소 위치", data1['min_position'] if data1['min_position'] else "N/A")
                with col3:
                    st.metric("최대 위치", data1['max_position'] if data1['max_position'] else "N/A")
                with col4:
                    # 2-3번째 위치에서 빠른 전환 비율
                    if data1['distribution'] and data1['found_count'] > 0:
                        quick_change_count = sum([data1['distribution'].get(i, 0) for i in [2, 3]])
                        quick_change_rate = (quick_change_count / data1['found_count'] * 100) if data1['found_count'] > 0 else 0
                        st.metric("빠른 전환 비율", f"{quick_change_rate:.1f}%", f"2-3번째: {quick_change_count}건")
                    else:
                        st.metric("빠른 전환 비율", "N/A")
                
                if data1['distribution']:
                    st.markdown("**위치별 분포:**")
                    dist_df = pd.DataFrame([
                        {'위치': k, '횟수': v, '비율(%)': (v / data1['found_count'] * 100) if data1['found_count'] > 0 else 0} 
                        for k, v in sorted(data1['distribution'].items())
                    ])
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)
                
                if data1['no_change_count'] > 0:
                    st.info(f"패턴이 변경되지 않은 경우: {data1['no_change_count']}건 ({data1['no_change_rate']:.1f}%)")
            
            # 첫 번째가 다를 때 → 다음에 같은 문자가 나오는 위치 분석
            if cross_strategy['first_different_next_same']:
                st.markdown("---")
                data2 = cross_strategy['first_different_next_same']
                st.markdown("#### 첫 번째가 다를 때 → 다음에 같은 문자가 나오는 위치")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("평균 위치", f"{data2['mean_position']:.2f}" if data2['mean_position'] else "N/A")
                with col2:
                    st.metric("최소 위치", data2['min_position'] if data2['min_position'] else "N/A")
                with col3:
                    st.metric("최대 위치", data2['max_position'] if data2['max_position'] else "N/A")
                with col4:
                    # 2-3번째 위치에서 빠른 전환 비율
                    if data2['distribution'] and data2['found_count'] > 0:
                        quick_change_count = sum([data2['distribution'].get(i, 0) for i in [2, 3]])
                        quick_change_rate = (quick_change_count / data2['found_count'] * 100) if data2['found_count'] > 0 else 0
                        st.metric("빠른 전환 비율", f"{quick_change_rate:.1f}%", f"2-3번째: {quick_change_count}건")
                    else:
                        st.metric("빠른 전환 비율", "N/A")
                
                if data2['distribution']:
                    st.markdown("**위치별 분포:**")
                    dist_df = pd.DataFrame([
                        {'위치': k, '횟수': v, '비율(%)': (v / data2['found_count'] * 100) if data2['found_count'] > 0 else 0} 
                        for k, v in sorted(data2['distribution'].items())
                    ])
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)
                
                if data2['no_change_count'] > 0:
                    st.info(f"패턴이 변경되지 않은 경우: {data2['no_change_count']}건 ({data2['no_change_rate']:.1f}%)")
            
            # 추천 평가
            if cross_strategy['recommendation']:
                st.markdown("---")
                st.markdown("**💡 전략 인사이트**")
                rec = cross_strategy['recommendation']
                
                for message in rec['messages']:
                    st.markdown(message)
            
            st.markdown("---")
            
            # 새로운 전략 분석: 다음 위치 일치 전략
            st.markdown("#### 🔮 다음 위치 일치 전략 평가")
            next_match_strategy = analyze_next_session_match_strategy(analysis_results)
            
            st.markdown(f"**전략 설명**: {next_match_strategy['description']}")
            st.markdown("---")
            
            # 1. 첫번째가 다른 경우 → 그 세션 내에서 다음 위치에서 같은 문자가 나오는지
            if next_match_strategy.get('first_different_next_match'):
                data = next_match_strategy['first_different_next_match']
                st.markdown("##### 1️⃣ 첫번째가 다른 경우")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("총 케이스", f"{data['total_cases']}건")
                with col2:
                    st.metric("최대 위치", f"{data.get('max_position', 0)}번째")
                with col3:
                    if data.get('avg_position'):
                        st.metric("평균 다음 일치 위치", f"{data['avg_position']:.1f}번째")
                    else:
                        st.metric("평균 다음 일치 위치", "N/A")
                with col4:
                    if data.get('min_position'):
                        st.metric("최소 다음 일치 위치", f"{data['min_position']}번째")
                    else:
                        st.metric("최소 다음 일치 위치", "N/A")
                
                # 추가 통계 표시
                if data.get('median_position'):
                    st.info(f"💡 중앙값 다음 일치 위치: {data['median_position']}번째")
                
                if data.get('no_match_count', 0) > 0:
                    st.warning(f"⚠️ 다음 일치가 없는 케이스: {data['no_match_count']}건")
                
                # 다음 일치 위치 분포 표시
                if data.get('position_distribution'):
                    st.markdown("**다음 일치 위치 분포:**")
                    dist_df = pd.DataFrame([
                        {'위치': k, '횟수': v, '비율(%)': (v / sum(data['position_distribution'].values()) * 100)} 
                        for k, v in sorted(data['position_distribution'].items())
                    ])
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)
                
                # 최대 위치 세션 테이블 표시
                if data.get('max_position_case') and data.get('max_position', 0) > 0:
                    st.markdown("**📊 최대 위치 세션 (최신 1개):**")
                    max_case = data['max_position_case']
                    session = max_case['session']
                    
                    max_session_df = pd.DataFrame([{
                        'ID': session.get('id', 'N/A'),
                        '세션 ID': session.get('session_id', 'N/A'),
                        '생성일시': session.get('created_at', 'N/A'),
                        'Sequence 예측': session.get('sequence_prediction_results', 'N/A'),
                        '재구성 Sequence': session.get('reconstructed_sequence_prediction_results', 'N/A'),
                        '최대 위치': f"{max_case['max_position']}번째"
                    }])
                    st.dataframe(max_session_df, use_container_width=True, hide_index=True)
                
                st.markdown("---")
            
            # 2. 첫번째가 같고 두번째가 다른 경우 → 그 세션 내에서 다음 위치에서 같은 문자가 나오는지
            if next_match_strategy.get('first_same_second_different_next_match'):
                data = next_match_strategy['first_same_second_different_next_match']
                st.markdown("##### 2️⃣ 첫번째가 같고 두번째가 다른 경우")
                
                col1, col2, col3, col4 = st.columns(4)
                with col1:
                    st.metric("총 케이스", f"{data['total_cases']}건")
                with col2:
                    st.metric("최대 위치", f"{data.get('max_position', 0)}번째")
                with col3:
                    if data.get('avg_position'):
                        st.metric("평균 다음 일치 위치", f"{data['avg_position']:.1f}번째")
                    else:
                        st.metric("평균 다음 일치 위치", "N/A")
                with col4:
                    if data.get('min_position'):
                        st.metric("최소 다음 일치 위치", f"{data['min_position']}번째")
                    else:
                        st.metric("최소 다음 일치 위치", "N/A")
                
                # 추가 통계 표시
                if data.get('median_position'):
                    st.info(f"💡 중앙값 다음 일치 위치: {data['median_position']}번째")
                
                if data.get('no_match_count', 0) > 0:
                    st.warning(f"⚠️ 다음 일치가 없는 케이스: {data['no_match_count']}건")
                
                # 다음 일치 위치 분포 표시
                if data.get('position_distribution'):
                    st.markdown("**다음 일치 위치 분포:**")
                    dist_df = pd.DataFrame([
                        {'위치': k, '횟수': v, '비율(%)': (v / sum(data['position_distribution'].values()) * 100)} 
                        for k, v in sorted(data['position_distribution'].items())
                    ])
                    st.dataframe(dist_df, use_container_width=True, hide_index=True)
                
                # 최대 위치 세션 테이블 표시
                if data.get('max_position_case') and data.get('max_position', 0) > 0:
                    st.markdown("**📊 최대 위치 세션 (최신 1개):**")
                    max_case = data['max_position_case']
                    session = max_case['session']
                    
                    max_session_df = pd.DataFrame([{
                        'ID': session.get('id', 'N/A'),
                        '세션 ID': session.get('session_id', 'N/A'),
                        '생성일시': session.get('created_at', 'N/A'),
                        'Sequence 예측': session.get('sequence_prediction_results', 'N/A'),
                        '재구성 Sequence': session.get('reconstructed_sequence_prediction_results', 'N/A'),
                        '최대 위치': f"{max_case['max_position']}번째"
                    }])
                    st.dataframe(max_session_df, use_container_width=True, hide_index=True)
                
                st.markdown("---")
            
            # 전략 제안
            st.markdown("#### 🎯 종합 승리 전략 제안")
            strategy_suggestions = []
            
            # 위치별 일치율 기반 전략
            if stats['position_match_rate'][1]:
                first_match_rate = stats['position_match_rate'][1]['rate']
                if first_match_rate >= 50:
                    strategy_suggestions.append(f"✅ 1번째 위치 일치율이 {first_match_rate:.1f}%로 높습니다. 첫 번째 문자에 집중하는 전략을 고려하세요.")
                else:
                    strategy_suggestions.append(f"⚠️ 1번째 위치 일치율이 {first_match_rate:.1f}%로 낮습니다. 첫 번째 문자만으로는 예측하기 어렵습니다.")
            
            if stats['position_match_rate'][2]:
                second_match_rate = stats['position_match_rate'][2]['rate']
                strategy_suggestions.append(f"📊 2번째 위치 일치율: {second_match_rate:.1f}%")
            
            if stats['position_match_rate'][3]:
                third_match_rate = stats['position_match_rate'][3]['rate']
                strategy_suggestions.append(f"📊 3번째 위치 일치율: {third_match_rate:.1f}%")
            
            # 최대 위치 기반 전략
            if stats['max_match']:
                avg_max_pos = stats['max_match']['mean']
                if avg_max_pos >= 2:
                    strategy_suggestions.append(f"💡 평균적으로 {avg_max_pos:.1f}번째 위치까지 일치합니다. 연속된 문자 매칭을 활용하세요.")
            
            # 첫 문자가 다른 경우 전략
            if stats['first_different_match'] and 'mean' in stats['first_different_match']:
                avg_match_pos = stats['first_different_match']['mean']
                strategy_suggestions.append(f"🔄 첫 문자가 다른 경우, 평균 {avg_match_pos:.1f}번째 위치에서 일치합니다. 첫 번째가 다르더라도 포기하지 마세요.")
            
            for suggestion in strategy_suggestions:
                st.write(suggestion)
            
        else:
            st.warning("⚠️ 비교할 수 있는 유효한 데이터가 없습니다.")
    else:
        st.info("분석할 데이터가 없습니다. 검색 조건을 변경해보세요.")
    
    st.markdown("---")
    
    # 테이블 표시
    st.markdown("### 📋 데이터 테이블")
    
    # 컬럼명 한글화
    display_df = df_filtered.copy()
    
    # 컬럼명 매핑
    column_mapping = {
        'id': 'ID',
        'session_id': '세션 ID',
        'converted_grid': '변환된 그리드',
        'reconstructed_grid': '재구성된 그리드',
        'sequence_prediction_results': 'Sequence 예측 결과',
        'reconstructed_sequence_prediction_results': '재구성 Sequence 예측 결과',
        'reconstructed_gap_results': '재구성 Gap 결과',
        'created_at': '생성일시'
    }
    
    # 컬럼명 변경
    display_df = display_df.rename(columns=column_mapping)
    
    # 컬럼 순서 지정 (예측 결과가 그리드보다 왼쪽에 오도록)
    column_order = [
        'ID',
        '세션 ID',
        'Sequence 예측 결과',
        '재구성 Sequence 예측 결과',
        '재구성 Gap 결과',
        '변환된 그리드',
        '재구성된 그리드',
        '생성일시'
    ]
    
    # 존재하는 컬럼만 선택
    available_columns = [col for col in column_order if col in display_df.columns]
    display_df = display_df[available_columns]
    
    # 데이터 표시
    if len(display_df) > 0:
        st.dataframe(display_df, use_container_width=True, height=600)
        
        # 데이터 새로고침 버튼
        if st.button("🔄 데이터 새로고침"):
            load_game_outcome_v2_data.clear()
            st.rerun()
    else:
        st.warning("⚠️ 표시할 데이터가 없습니다.")
        if search_term:
            st.info("검색어를 변경하거나 검색어를 지워서 전체 데이터를 확인해보세요.")

if __name__ == "__main__":
    main()

