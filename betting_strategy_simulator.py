"""
베팅 전략 시뮬레이션 앱
저장된 게임 기록을 기반으로 마틴게일, 다람베르, 피보나치 베팅 전략을 시뮬레이션하고
승률 정보와 함께 수익성을 비교 분석하는 Streamlit 앱
"""

import streamlit as st
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from typing import Dict, List, Tuple, Optional, Callable
from collections import defaultdict
import uuid
from scipy import stats

# 페이지 설정
st.set_page_config(
    page_title="Betting Strategy Simulator",
    page_icon="💰",
    layout="wide"
)

# 기존 앱의 함수들 import
from hypothesis_validation_app import get_db_connection, load_preprocessed_data
from interactive_multi_step_validation_app import (
    validate_interactive_multi_step_scenario_with_confidence_skip
)

# ============================================================================
# DB 테이블 생성 및 관리
# ============================================================================

def create_fibonacci_betting_tables():
    """피보나치 베팅 시뮬레이션 결과 저장을 위한 테이블 생성"""
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        # 1. 시뮬레이션 세션 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fibonacci_betting_simulation_sessions (
                simulation_id TEXT PRIMARY KEY,
                cutoff_grid_string_id INTEGER NOT NULL,
                window_size INTEGER NOT NULL,
                method TEXT NOT NULL,
                use_threshold BOOLEAN NOT NULL,
                threshold REAL,
                max_interval INTEGER NOT NULL,
                confidence_skip_threshold REAL NOT NULL,
                initial_bankroll REAL NOT NULL,
                main_base INTEGER NOT NULL,
                odds REAL NOT NULL,
                total_grid_strings INTEGER NOT NULL,
                total_predictions INTEGER NOT NULL,
                total_successes INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                avg_accuracy REAL NOT NULL,
                final_bankroll REAL NOT NULL,
                profit REAL NOT NULL,
                profit_rate REAL NOT NULL,
                max_cumulative_loss REAL NOT NULL,
                max_stage_reached INTEGER NOT NULL,
                insufficient_funds_count INTEGER NOT NULL,
                insufficient_funds_rate REAL NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours'))
            )
        ''')
        
        # 2. Grid String별 결과 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS fibonacci_betting_grid_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                simulation_id TEXT NOT NULL,
                grid_string_id INTEGER NOT NULL,
                first_prediction_result BOOLEAN,
                win_rate REAL NOT NULL,
                total_predictions INTEGER NOT NULL,
                total_successes INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                start_index INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (simulation_id) REFERENCES fibonacci_betting_simulation_sessions(simulation_id),
                FOREIGN KEY (grid_string_id) REFERENCES preprocessed_grid_strings(id),
                UNIQUE(simulation_id, grid_string_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_sessions_created_at 
            ON fibonacci_betting_simulation_sessions(created_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_simulation_sessions_cutoff 
            ON fibonacci_betting_simulation_sessions(cutoff_grid_string_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_results_simulation_id 
            ON fibonacci_betting_grid_results(simulation_id)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_results_first_result 
            ON fibonacci_betting_grid_results(first_prediction_result)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_results_simulation_first 
            ON fibonacci_betting_grid_results(simulation_id, first_prediction_result)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_grid_results_grid_string_id 
            ON fibonacci_betting_grid_results(grid_string_id)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"테이블 생성 오류: {str(e)}")
        return False
    finally:
        conn.close()


def save_fibonacci_betting_simulation_result(
    cutoff_grid_string_id: int,
    window_size: int,
    method: str,
    use_threshold: bool,
    threshold: float,
    max_interval: int,
    confidence_skip_threshold: float,
    initial_bankroll: float,
    main_base: int,
    odds: float,
    summary: Dict,
    results: List[Dict]
) -> Optional[str]:
    """
    피보나치 베팅 시뮬레이션 결과를 DB에 저장
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        confidence_skip_threshold: 신뢰도 스킵 임계값
        initial_bankroll: 초기 자본금
        main_base: 1단계 메인 베팅 금액
        odds: 배당률
        summary: 전체 통계 요약
        results: grid_string별 결과 리스트
    
    Returns:
        simulation_id: 저장된 시뮬레이션 ID (실패 시 None)
    """
    # 테이블 생성 확인
    if not create_fibonacci_betting_tables():
        return None
    
    simulation_id = str(uuid.uuid4())
    conn = get_db_connection()
    if conn is None:
        return None
    
    cursor = conn.cursor()
    
    try:
        # 시뮬레이션 세션 저장
        cursor.execute('''
            INSERT INTO fibonacci_betting_simulation_sessions (
                simulation_id, cutoff_grid_string_id, window_size, method,
                use_threshold, threshold, max_interval, confidence_skip_threshold,
                initial_bankroll, main_base, odds,
                total_grid_strings, total_predictions, total_successes, total_failures,
                avg_accuracy, final_bankroll, profit, profit_rate,
                max_cumulative_loss, max_stage_reached,
                insufficient_funds_count, insufficient_funds_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            simulation_id, cutoff_grid_string_id, window_size, method,
            use_threshold, threshold, max_interval, confidence_skip_threshold,
            initial_bankroll, main_base, odds,
            summary['total_grid_strings'], summary['total_predictions'],
            summary['total_successes'], summary['total_failures'],
            summary['avg_accuracy'], summary['final_bankroll'],
            summary['profit'], summary['profit_rate'],
            summary['max_cumulative_loss'], summary['max_stage_reached'],
            summary['total_insufficient_funds_count'], summary['insufficient_funds_rate']
        ))
        
        # Grid String별 결과 저장
        for r in results:
            cursor.execute('''
                INSERT INTO fibonacci_betting_grid_results (
                    simulation_id, grid_string_id, first_prediction_result,
                    win_rate, total_predictions, total_successes, total_failures, start_index
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                simulation_id, r['grid_string_id'], r.get('first_prediction_result'),
                r['win_rate'], r['total_predictions'],
                r['total_successes'], r['total_failures'], r.get('start_index', 0)
            ))
        
        conn.commit()
        return simulation_id
        
    except Exception as e:
        conn.rollback()
        st.error(f"시뮬레이션 결과 저장 오류: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()


def load_fibonacci_betting_simulations(limit: int = 50) -> pd.DataFrame:
    """저장된 피보나치 베팅 시뮬레이션 목록 조회"""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = '''
            SELECT 
                simulation_id,
                cutoff_grid_string_id,
                window_size,
                method,
                use_threshold,
                threshold,
                max_interval,
                confidence_skip_threshold,
                initial_bankroll,
                main_base,
                odds,
                total_grid_strings,
                total_predictions,
                avg_accuracy,
                final_bankroll,
                profit,
                profit_rate,
                max_cumulative_loss,
                max_stage_reached,
                created_at
            FROM fibonacci_betting_simulation_sessions
            ORDER BY created_at DESC
            LIMIT ?
        '''
        df = pd.read_sql_query(query, conn, params=[limit])
        return df
    except Exception as e:
        st.error(f"시뮬레이션 목록 조회 오류: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()


def load_fibonacci_betting_simulation_detail(simulation_id: str) -> Optional[Dict]:
    """특정 시뮬레이션의 상세 정보 조회"""
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 시뮬레이션 세션 정보
        session_query = '''
            SELECT * FROM fibonacci_betting_simulation_sessions
            WHERE simulation_id = ?
        '''
        session_df = pd.read_sql_query(session_query, conn, params=[simulation_id])
        
        if len(session_df) == 0:
            return None
        
        # Grid String별 결과
        results_query = '''
            SELECT * FROM fibonacci_betting_grid_results
            WHERE simulation_id = ?
            ORDER BY grid_string_id
        '''
        results_df = pd.read_sql_query(results_query, conn, params=[simulation_id])
        
        return {
            'session': session_df.iloc[0].to_dict(),
            'results': results_df.to_dict('records')
        }
    except Exception as e:
        st.error(f"시뮬레이션 상세 조회 오류: {str(e)}")
        return None
    finally:
        conn.close()


def analyze_first_game_result_correlation(simulation_id: str) -> Dict:
    """
    첫 게임 결과와 승률의 상관관계 분석
    
    Args:
        simulation_id: 시뮬레이션 ID
    
    Returns:
        분석 결과 딕셔너리
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        query = '''
            SELECT 
                first_prediction_result,
                win_rate,
                total_predictions,
                total_successes,
                total_failures
            FROM fibonacci_betting_grid_results
            WHERE simulation_id = ? AND first_prediction_result IS NOT NULL
        '''
        df = pd.read_sql_query(query, conn, params=[simulation_id])
        
        if len(df) == 0:
            return {
                'error': '분석할 데이터가 없습니다.'
            }
        
        # 첫 게임 승리 그룹
        win_start = df[df['first_prediction_result'] == True]
        # 첫 게임 패배 그룹
        loss_start = df[df['first_prediction_result'] == False]
        
        if len(win_start) == 0 or len(loss_start) == 0:
            return {
                'error': '첫 게임 승리 또는 패배 그룹 중 하나가 비어있습니다.'
            }
        
        # 통계 계산
        win_start_stats = {
            'count': len(win_start),
            'mean_win_rate': win_start['win_rate'].mean(),
            'std_win_rate': win_start['win_rate'].std(),
            'median_win_rate': win_start['win_rate'].median(),
            'min_win_rate': win_start['win_rate'].min(),
            'max_win_rate': win_start['win_rate'].max()
        }
        
        loss_start_stats = {
            'count': len(loss_start),
            'mean_win_rate': loss_start['win_rate'].mean(),
            'std_win_rate': loss_start['win_rate'].std(),
            'median_win_rate': loss_start['win_rate'].median(),
            'min_win_rate': loss_start['win_rate'].min(),
            'max_win_rate': loss_start['win_rate'].max()
        }
        
        # t-test (통계적 유의성 검정)
        t_stat, p_value = stats.ttest_ind(win_start['win_rate'], loss_start['win_rate'])
        
        # 효과 크기 (Cohen's d)
        pooled_std = np.sqrt((win_start['win_rate'].std()**2 + loss_start['win_rate'].std()**2) / 2)
        cohens_d = (win_start['win_rate'].mean() - loss_start['win_rate'].mean()) / pooled_std if pooled_std > 0 else 0
        
        # 효과 크기 해석
        if abs(cohens_d) < 0.2:
            effect_size_interpretation = "작은 효과"
        elif abs(cohens_d) < 0.5:
            effect_size_interpretation = "중간 효과"
        elif abs(cohens_d) < 0.8:
            effect_size_interpretation = "큰 효과"
        else:
            effect_size_interpretation = "매우 큰 효과"
        
        return {
            'win_start_stats': win_start_stats,
            'loss_start_stats': loss_start_stats,
            't_stat': t_stat,
            'p_value': p_value,
            'cohens_d': cohens_d,
            'effect_size_interpretation': effect_size_interpretation,
            'win_start_data': win_start['win_rate'].tolist(),
            'loss_start_data': loss_start['win_rate'].tolist(),
            'difference': win_start_stats['mean_win_rate'] - loss_start_stats['mean_win_rate']
        }
        
    except Exception as e:
        st.error(f"상관관계 분석 오류: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()


# ============================================================================
# 베팅 전략 함수 구현
# ============================================================================

def martingale_bet(current_bet: float, base_unit: float, lost: bool) -> float:
    """
    마틴게일 전략 베팅 금액 계산
    
    Args:
        current_bet: 현재 베팅 금액
        base_unit: 기본 베팅 단위
        lost: 이전 베팅에서 패배했는지 여부
    
    Returns:
        다음 베팅 금액
    """
    if lost:
        # 패배 시: 이전 베팅 금액의 2배
        return current_bet * 2
    else:
        # 승리 시: 초기 베팅 단위로 리셋
        return base_unit


def dalembert_bet(current_bet: float, base_unit: float, won: bool) -> float:
    """
    다람베르 전략 베팅 금액 계산
    
    Args:
        current_bet: 현재 베팅 금액
        base_unit: 기본 베팅 단위
        won: 이전 베팅에서 승리했는지 여부
    
    Returns:
        다음 베팅 금액
    """
    if won:
        # 승리 시: 베팅 단위만큼 감소
        new_bet = current_bet - base_unit
        # 최소 베팅 단위 유지
        return max(new_bet, base_unit)
    else:
        # 패배 시: 베팅 단위만큼 증가
        return current_bet + base_unit


def fibonacci_bet(sequence_index: int, base_unit: float, won: bool) -> Tuple[int, float]:
    """
    피보나치 전략 베팅 금액 계산
    
    Args:
        sequence_index: 현재 피보나치 수열 인덱스
        base_unit: 기본 베팅 단위
        won: 이전 베팅에서 승리했는지 여부
    
    Returns:
        (다음 인덱스, 다음 베팅 금액)
    """
    # 피보나치 수열: 1, 1, 2, 3, 5, 8, 13, 21, ...
    def fib(n):
        if n <= 1:
            return 1
        a, b = 1, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b
    
    if won:
        # 승리 시: 2단계 전으로 이동
        if sequence_index <= 2:
            # 초기 상태로 리셋
            return (0, base_unit)
        else:
            # 2단계 전으로 이동
            new_index = sequence_index - 2
            return (new_index, fib(new_index) * base_unit)
    else:
        # 패배 시: 다음 피보나치 수로 증가
        new_index = sequence_index + 1
        return (new_index, fib(new_index) * base_unit)


def create_fibonacci_betting_table(max_stages: int = 15, main_base: int = 11) -> pd.DataFrame:
    """
    피보나치 기반 단계별 금액 테이블 생성
    
    Args:
        max_stages: 최대 단계 수
        main_base: 1단계 메인 베팅 금액 (기본값: 11)
    
    Returns:
        단계별 금액 테이블 데이터프레임
    """
    table_data = []
    cumulative_losses = []  # 각 단계까지의 누적 손실
    main_bets_history = []  # 각 단계의 메인 베팅 금액
    tie_bets_history = []   # 각 단계의 타이 베팅 금액
    
    # main_base에 따라 2단계와 3단계 계산
    # main_base=11일 때: 1단계 11, 2단계 14, 3단계 32
    # main_base=6일 때: 1단계 6, 2단계 8 (고정), 3단계는 비율 계산
    
    for stage in range(1, max_stages + 1):
        if stage == 1:
            # 1단계: 메인 main_base, 타이 1
            main_bet = main_base
            tie_bet = 1
            cumulative_loss = main_bet + tie_bet
            cumulative_losses.append(cumulative_loss)
            main_bets_history.append(main_bet)
            tie_bets_history.append(tie_bet)
            win_move = "세션 종료 (+10 수익)"
            
        elif stage == 2:
            # 2단계: main_base=6일 때 메인 8로 고정, 타이는 비율 계산
            if main_base == 6:
                main_bet = 8
                # 원래 비율: 타이 2/메인 14 = 0.143
                # 메인 8일 때 타이 = 8 * 0.143 ≈ 1.14 → 1
                tie_bet = max(1, int(8 * (2 / 14)))
            else:
                # main_base=11일 때는 원래대로
                stage2_ratio = 14 / 11
                tie_ratio = 2 / 11
                main_bet = int(main_base * stage2_ratio)
                tie_bet = max(1, int(main_base * tie_ratio))
            
            cumulative_loss = cumulative_losses[0] + main_bet + tie_bet
            cumulative_losses.append(cumulative_loss)
            main_bets_history.append(main_bet)
            tie_bets_history.append(tie_bet)
            win_move = "1단계로 이동 (복구 완료)"
            
        elif stage == 3:
            # 3단계: main_base에 따라 계산
            if main_base == 6:
                # main_base=6일 때: 1단계 6, 2단계 8이므로
                # 원래 비율: 11:14:32 = 1:1.27:2.91
                # 6:8:? → 3단계는 2단계의 약 4배 (32/14 ≈ 2.29)
                # 또는 1단계의 약 5.33배 (32/6 ≈ 5.33)
                # 더 정확하게는 2단계 메인 8의 4배 = 32, 하지만 1단계 6 기준으로는 32/11*6 ≈ 17.45
                # 원래 3단계 메인 32는 1단계 11의 약 2.91배, 2단계 14의 약 2.29배
                # 2단계 메인 8의 2.29배 = 18.32 ≈ 18
                stage3_from_stage2_ratio = 32 / 14  # 약 2.29
                main_bet = int(8 * stage3_from_stage2_ratio)  # 8 * 2.29 ≈ 18
                # 타이는 원래 비율: 4/32 = 0.125
                tie_bet = max(1, int(main_bet * (4 / 32)))
            else:
                # main_base=11일 때는 원래대로
                stage3_ratio = 32 / 11
                tie_ratio = 4 / 11
                main_bet = int(main_base * stage3_ratio)
                tie_bet = max(1, int(main_base * tie_ratio * 2))
            cumulative_loss = cumulative_losses[1] + main_bet + tie_bet  # 28 + 32 + 4 = 64
            cumulative_losses.append(cumulative_loss)
            main_bets_history.append(main_bet)
            tie_bets_history.append(tie_bet)
            win_move = "1단계로 이동"
            
        else:
            # 4단계부터: 메인 = 앞 2개 단계의 전체 베팅 금액 합 + 타이 베팅
            # 타이 베팅 = 전체 누적 손실 / 8
            prev_cumulative = cumulative_losses[-1]  # 이전 단계까지의 누적 손실
            tie_bet = int(prev_cumulative / 8)
            
            # 앞 2개 단계의 전체 베팅 금액 계산
            # (stage-2)단계의 전체 베팅 + (stage-1)단계의 전체 베팅
            prev_stage_2_main = main_bets_history[stage-3]  # stage-2의 메인
            prev_stage_2_tie = tie_bets_history[stage-3]    # stage-2의 타이
            prev_stage_1_main = main_bets_history[stage-2]  # stage-1의 메인
            prev_stage_1_tie = tie_bets_history[stage-2]    # stage-1의 타이
            
            two_stages_total_bet = (prev_stage_2_main + prev_stage_2_tie) + (prev_stage_1_main + prev_stage_1_tie)
            
            main_bet = two_stages_total_bet + tie_bet
            
            cumulative_loss = prev_cumulative + main_bet + tie_bet
            cumulative_losses.append(cumulative_loss)
            main_bets_history.append(main_bet)
            tie_bets_history.append(tie_bet)
            win_move = f"{stage-2}단계"
        
        total_bet = main_bet + tie_bet
        
        table_data.append({
            '단계': stage,
            '메인(Main)': main_bet,
            '타이(Tie)': tie_bet,
            '누적 손실': cumulative_losses[stage-1] if stage <= len(cumulative_losses) else 0,
            '승리 시 이동 (리커버리 규칙)': win_move,
            '타이 승리 시 이동': '1단계',
            '패배 시 이동': f'{stage+1}단계'
        })
    
    return pd.DataFrame(table_data)


# ============================================================================
# 데이터 로딩 함수
# ============================================================================

def load_game_sessions() -> pd.DataFrame:
    """
    DB에서 게임 세션 목록 로드
    
    Returns:
        게임 세션 데이터프레임
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                session_id,
                grid_string,
                window_size,
                method,
                use_threshold,
                threshold,
                max_interval,
                confidence_skip_threshold,
                total_steps,
                total_predictions,
                total_failures,
                total_forced_predictions,
                total_skipped_predictions,
                max_consecutive_failures,
                accuracy,
                started_at,
                completed_at,
                auto_executed
            FROM live_game_sessions
            ORDER BY session_id DESC
        """
        df = pd.read_sql_query(query, conn)
        return df
    except Exception as e:
        st.error(f"게임 세션 로드 오류: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()


def load_game_steps(session_id: int) -> pd.DataFrame:
    """
    특정 세션의 게임 스텝 로드
    
    Args:
        session_id: 세션 ID
    
    Returns:
        게임 스텝 데이터프레임
    """
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        query = """
            SELECT 
                id,
                session_id,
                step,
                prefix,
                predicted_value,
                actual_value,
                confidence,
                b_ratio,
                p_ratio,
                is_forced,
                strategy_name,
                current_interval,
                has_prediction,
                validated,
                skipped,
                is_correct
            FROM live_game_steps
            WHERE session_id = ?
            ORDER BY step ASC
        """
        df = pd.read_sql_query(query, conn, params=(session_id,))
        return df
    except Exception as e:
        st.error(f"게임 스텝 로드 오류: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()


def calculate_overall_win_rate(sessions_data: pd.DataFrame) -> Dict:
    """
    전체 평균 승률 계산
    
    Args:
        sessions_data: 게임 세션 데이터프레임
    
    Returns:
        전체 승률 통계 딕셔너리
    """
    if len(sessions_data) == 0:
        return {
            'overall_win_rate': 0.0,
            'total_predictions': 0,
            'total_successes': 0,
            'total_failures': 0
        }
    
    total_predictions = sessions_data['total_predictions'].sum()
    total_failures = sessions_data['total_failures'].sum()
    total_successes = total_predictions - total_failures
    
    overall_win_rate = (total_successes / total_predictions * 100) if total_predictions > 0 else 0.0
    
    return {
        'overall_win_rate': overall_win_rate,
        'total_predictions': int(total_predictions),
        'total_successes': int(total_successes),
        'total_failures': int(total_failures)
    }


def calculate_session_win_rate(steps_data: pd.DataFrame) -> Dict:
    """
    세션별 승률 계산
    
    Args:
        steps_data: 게임 스텝 데이터프레임
    
    Returns:
        세션 승률 통계 딕셔너리
    """
    # 검증된 예측만 고려
    validated_steps = steps_data[
        (steps_data['has_prediction'] == True) & 
        (steps_data['validated'] == True) &
        (steps_data['skipped'] == False)
    ]
    
    if len(validated_steps) == 0:
        return {
            'win_rate': 0.0,
            'total_predictions': 0,
            'total_successes': 0,
            'total_failures': 0
        }
    
    total_predictions = len(validated_steps)
    total_successes = validated_steps['is_correct'].sum()
    total_failures = total_predictions - total_successes
    
    win_rate = (total_successes / total_predictions * 100) if total_predictions > 0 else 0.0
    
    return {
        'win_rate': win_rate,
        'total_predictions': int(total_predictions),
        'total_successes': int(total_successes),
        'total_failures': int(total_failures)
    }


# ============================================================================
# 시뮬레이션 엔진
# ============================================================================

def simulate_martingale_strategy(
    game_steps: pd.DataFrame,
    initial_bankroll: float,
    base_unit: float,
    odds: float
) -> Dict:
    """
    마틴게일 전략 시뮬레이션
    
    Args:
        game_steps: 게임 스텝 데이터프레임
        initial_bankroll: 초기 자본금
        base_unit: 기본 베팅 단위
        odds: 배당률
    
    Returns:
        시뮬레이션 결과 딕셔너리
    """
    bankroll = initial_bankroll
    current_bet = base_unit
    total_bet_amount = 0.0
    total_wins = 0
    total_losses = 0
    max_consecutive_losses = 0
    current_consecutive_losses = 0
    max_bet_amount = base_unit
    bankroll_history = [bankroll]
    went_bankrupt = False
    
    # 검증된 예측만 고려
    validated_steps = game_steps[
        (game_steps['has_prediction'] == True) & 
        (game_steps['validated'] == True) &
        (game_steps['skipped'] == False)
    ].copy()
    
    for idx, step in validated_steps.iterrows():
        # 자본금 확인
        if bankroll < current_bet:
            went_bankrupt = True
            break
        
        # 베팅
        bankroll -= current_bet
        total_bet_amount += current_bet
        max_bet_amount = max(max_bet_amount, current_bet)
        
        # 결과 확인
        is_correct = step['is_correct']
        
        if is_correct:
            # 승리: 배당금 획득
            winnings = current_bet * odds
            bankroll += winnings
            total_wins += 1
            current_consecutive_losses = 0
            # 마틴게일: 초기 베팅 단위로 리셋
            current_bet = base_unit
        else:
            # 패배
            total_losses += 1
            current_consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
            # 마틴게일: 베팅 금액 2배
            current_bet = current_bet * 2
        
        bankroll_history.append(bankroll)
    
    final_bankroll = bankroll
    profit = final_bankroll - initial_bankroll
    profit_rate = (profit / initial_bankroll * 100) if initial_bankroll > 0 else 0.0
    
    return {
        'strategy_name': '마틴게일',
        'final_bankroll': final_bankroll,
        'initial_bankroll': initial_bankroll,
        'profit': profit,
        'profit_rate': profit_rate,
        'total_bet_amount': total_bet_amount,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'max_consecutive_losses': max_consecutive_losses,
        'max_bet_amount': max_bet_amount,
        'went_bankrupt': went_bankrupt,
        'bankroll_history': bankroll_history
    }


def simulate_dalembert_strategy(
    game_steps: pd.DataFrame,
    initial_bankroll: float,
    base_unit: float,
    odds: float
) -> Dict:
    """
    다람베르 전략 시뮬레이션
    
    Args:
        game_steps: 게임 스텝 데이터프레임
        initial_bankroll: 초기 자본금
        base_unit: 기본 베팅 단위
        odds: 배당률
    
    Returns:
        시뮬레이션 결과 딕셔너리
    """
    bankroll = initial_bankroll
    current_bet = base_unit
    total_bet_amount = 0.0
    total_wins = 0
    total_losses = 0
    max_consecutive_losses = 0
    current_consecutive_losses = 0
    max_bet_amount = base_unit
    bankroll_history = [bankroll]
    went_bankrupt = False
    
    # 검증된 예측만 고려
    validated_steps = game_steps[
        (game_steps['has_prediction'] == True) & 
        (game_steps['validated'] == True) &
        (game_steps['skipped'] == False)
    ].copy()
    
    for idx, step in validated_steps.iterrows():
        # 자본금 확인
        if bankroll < current_bet:
            went_bankrupt = True
            break
        
        # 베팅
        bankroll -= current_bet
        total_bet_amount += current_bet
        max_bet_amount = max(max_bet_amount, current_bet)
        
        # 결과 확인
        is_correct = step['is_correct']
        
        if is_correct:
            # 승리: 배당금 획득
            winnings = current_bet * odds
            bankroll += winnings
            total_wins += 1
            current_consecutive_losses = 0
            # 다람베르: 베팅 단위만큼 감소
            current_bet = max(current_bet - base_unit, base_unit)
        else:
            # 패배
            total_losses += 1
            current_consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
            # 다람베르: 베팅 단위만큼 증가
            current_bet = current_bet + base_unit
        
        bankroll_history.append(bankroll)
    
    final_bankroll = bankroll
    profit = final_bankroll - initial_bankroll
    profit_rate = (profit / initial_bankroll * 100) if initial_bankroll > 0 else 0.0
    
    return {
        'strategy_name': '다람베르',
        'final_bankroll': final_bankroll,
        'initial_bankroll': initial_bankroll,
        'profit': profit,
        'profit_rate': profit_rate,
        'total_bet_amount': total_bet_amount,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'max_consecutive_losses': max_consecutive_losses,
        'max_bet_amount': max_bet_amount,
        'went_bankrupt': went_bankrupt,
        'bankroll_history': bankroll_history
    }


def simulate_fibonacci_strategy(
    game_steps: pd.DataFrame,
    initial_bankroll: float,
    base_unit: float,
    odds: float
) -> Dict:
    """
    피보나치 전략 시뮬레이션
    
    Args:
        game_steps: 게임 스텝 데이터프레임
        initial_bankroll: 초기 자본금
        base_unit: 기본 베팅 단위
        odds: 배당률
    
    Returns:
        시뮬레이션 결과 딕셔너리
    """
    bankroll = initial_bankroll
    sequence_index = 0  # 피보나치 수열 인덱스
    total_bet_amount = 0.0
    total_wins = 0
    total_losses = 0
    max_consecutive_losses = 0
    current_consecutive_losses = 0
    max_bet_amount = base_unit
    bankroll_history = [bankroll]
    went_bankrupt = False
    
    # 피보나치 수열 계산 함수
    def fib(n):
        if n <= 1:
            return 1
        a, b = 1, 1
        for _ in range(2, n + 1):
            a, b = b, a + b
        return b
    
    # 검증된 예측만 고려
    validated_steps = game_steps[
        (game_steps['has_prediction'] == True) & 
        (game_steps['validated'] == True) &
        (game_steps['skipped'] == False)
    ].copy()
    
    for idx, step in validated_steps.iterrows():
        # 현재 베팅 금액 계산
        current_bet = fib(sequence_index) * base_unit
        
        # 자본금 확인
        if bankroll < current_bet:
            went_bankrupt = True
            break
        
        # 베팅
        bankroll -= current_bet
        total_bet_amount += current_bet
        max_bet_amount = max(max_bet_amount, current_bet)
        
        # 결과 확인
        is_correct = step['is_correct']
        
        if is_correct:
            # 승리: 배당금 획득
            winnings = current_bet * odds
            bankroll += winnings
            total_wins += 1
            current_consecutive_losses = 0
            # 피보나치: 2단계 전으로 이동
            if sequence_index <= 2:
                sequence_index = 0
            else:
                sequence_index = sequence_index - 2
        else:
            # 패배
            total_losses += 1
            current_consecutive_losses += 1
            max_consecutive_losses = max(max_consecutive_losses, current_consecutive_losses)
            # 피보나치: 다음 수열로 증가
            sequence_index = sequence_index + 1
        
        bankroll_history.append(bankroll)
    
    final_bankroll = bankroll
    profit = final_bankroll - initial_bankroll
    profit_rate = (profit / initial_bankroll * 100) if initial_bankroll > 0 else 0.0
    
    return {
        'strategy_name': '피보나치',
        'final_bankroll': final_bankroll,
        'initial_bankroll': initial_bankroll,
        'profit': profit,
        'profit_rate': profit_rate,
        'total_bet_amount': total_bet_amount,
        'total_wins': total_wins,
        'total_losses': total_losses,
        'max_consecutive_losses': max_consecutive_losses,
        'max_bet_amount': max_bet_amount,
        'went_bankrupt': went_bankrupt,
        'bankroll_history': bankroll_history
    }


# ============================================================================
# 결과 분석 및 통계 계산
# ============================================================================

def analyze_all_sessions(
    sessions_data: pd.DataFrame,
    initial_bankroll: float,
    base_unit: float,
    odds: float,
    selected_session_ids: Optional[List[int]] = None
) -> Dict:
    """
    모든 세션에 대해 전략별 시뮬레이션 실행 및 통계 계산
    
    Args:
        sessions_data: 게임 세션 데이터프레임
        initial_bankroll: 초기 자본금
        base_unit: 기본 베팅 단위
        odds: 배당률
        selected_session_ids: 선택된 세션 ID 리스트 (None이면 전체)
    
    Returns:
        분석 결과 딕셔너리
    """
    if selected_session_ids is not None:
        sessions_data = sessions_data[sessions_data['session_id'].isin(selected_session_ids)]
    
    if len(sessions_data) == 0:
        return {
            'session_results': [],
            'overall_stats': {},
            'strategy_comparison': {}
        }
    
    session_results = []
    strategy_stats = {
        '마틴게일': {'profits': [], 'profit_rates': [], 'bankrupt_count': 0, 'final_bankrolls': []},
        '다람베르': {'profits': [], 'profit_rates': [], 'bankrupt_count': 0, 'final_bankrolls': []},
        '피보나치': {'profits': [], 'profit_rates': [], 'bankrupt_count': 0, 'final_bankrolls': []}
    }
    
    for _, session in sessions_data.iterrows():
        session_id = session['session_id']
        steps_data = load_game_steps(session_id)
        
        if len(steps_data) == 0:
            continue
        
        # 세션 승률 계산
        session_win_rate = calculate_session_win_rate(steps_data)
        
        # 각 전략별 시뮬레이션
        martingale_result = simulate_martingale_strategy(steps_data, initial_bankroll, base_unit, odds)
        dalembert_result = simulate_dalembert_strategy(steps_data, initial_bankroll, base_unit, odds)
        fibonacci_result = simulate_fibonacci_strategy(steps_data, initial_bankroll, base_unit, odds)
        
        # 세션 결과 저장
        session_result = {
            'session_id': session_id,
            'win_rate': session_win_rate['win_rate'],
            'total_predictions': session_win_rate['total_predictions'],
            'total_successes': session_win_rate['total_successes'],
            'total_failures': session_win_rate['total_failures'],
            'martingale': martingale_result,
            'dalembert': dalembert_result,
            'fibonacci': fibonacci_result
        }
        session_results.append(session_result)
        
        # 전략별 통계 수집
        for strategy_name in ['마틴게일', '다람베르', '피보나치']:
            result = martingale_result if strategy_name == '마틴게일' else (dalembert_result if strategy_name == '다람베르' else fibonacci_result)
            strategy_stats[strategy_name]['profits'].append(result['profit'])
            strategy_stats[strategy_name]['profit_rates'].append(result['profit_rate'])
            strategy_stats[strategy_name]['final_bankrolls'].append(result['final_bankroll'])
            if result['went_bankrupt']:
                strategy_stats[strategy_name]['bankrupt_count'] += 1
    
    # 전체 통계 계산
    overall_stats = {}
    for strategy_name, stats in strategy_stats.items():
        if len(stats['profits']) > 0:
            overall_stats[strategy_name] = {
                'avg_profit': np.mean(stats['profits']),
                'avg_profit_rate': np.mean(stats['profit_rates']),
                'avg_final_bankroll': np.mean(stats['final_bankrolls']),
                'total_bankrupt_count': stats['bankrupt_count'],
                'bankrupt_rate': (stats['bankrupt_count'] / len(stats['profits']) * 100) if len(stats['profits']) > 0 else 0.0
            }
        else:
            overall_stats[strategy_name] = {
                'avg_profit': 0.0,
                'avg_profit_rate': 0.0,
                'avg_final_bankroll': initial_bankroll,
                'total_bankrupt_count': 0,
                'bankrupt_rate': 0.0
            }
    
    return {
        'session_results': session_results,
        'overall_stats': overall_stats,
        'strategy_comparison': strategy_stats
    }


# ============================================================================
# Streamlit UI
# ============================================================================

def main():
    # 테이블 생성 (앱 시작 시)
    create_fibonacci_betting_tables()
    
    st.title("💰 베팅 전략 시뮬레이션")
    st.markdown("**저장된 게임 기록을 기반으로 마틴게일, 다람베르, 피보나치 전략의 수익성을 분석합니다**")
    
    # 피보나치 금액 테이블 표시
    st.markdown("---")
    st.markdown("### 📊 피보나치 단계별 금액 테이블")
    
    with st.expander("💰 금액 테이블 보기", expanded=True):
        # 1단계 메인 11 테이블
        st.markdown("#### 1단계 메인 베팅: 11원")
        betting_table_11 = create_fibonacci_betting_table(max_stages=15, main_base=11)
        st.dataframe(betting_table_11, use_container_width=True, hide_index=True)
        
        st.markdown("---")
        
        # 1단계 메인 6 테이블
        st.markdown("#### 1단계 메인 베팅: 6원")
        betting_table_6 = create_fibonacci_betting_table(max_stages=15, main_base=6)
        st.dataframe(betting_table_6, use_container_width=True, hide_index=True)
        
        st.markdown("""
        **핵심 운용 규칙:**
        
        1. **메인 승리 시 (2단계 하향 규칙)**
           - 4단계 이상에서 승리하면 무조건 왼쪽으로 두 칸 이동
           - 예: 5단계 승리 → 3단계 배팅 → 3단계 승리 → 1단계 복귀(수익 확정)
           - 예외: 2, 3단계에서 승리하면 즉시 1단계로 돌아가 수익을 확정
        
        2. **타이(Tie) 승리 시 (치트키 규칙)**
           - 어떤 단계에서든 타이가 적중하면 즉시 1단계로 복귀
           - 타이는 그동안의 모든 메인/타이 손실을 복구해 주는 '비상탈출구'
        
        3. **패배 시 (전진 규칙)**
           - 메인과 타이 모두 낙첨될 경우 다음 단계(아래)로 이동하여 배팅
        """)
    
    # 설정 섹션
    st.markdown("---")
    st.markdown("### ⚙️ 시뮬레이션 설정")
    
    col1, col2, col3 = st.columns(3)
    with col1:
        base_unit = st.number_input(
            "베팅 단위 (원)",
            min_value=1,
            value=100,
            step=10,
            key="base_unit"
        )
    with col2:
        initial_bankroll = st.number_input(
            "초기 자본금 (원)",
            min_value=1,
            value=10000,
            step=1000,
            key="initial_bankroll"
        )
    with col3:
        odds = st.number_input(
            "배당률 (배)",
            min_value=1.0,
            value=2.0,
            step=0.1,
            key="odds"
        )
    
    # 게임 세션 로드
    st.markdown("---")
    st.markdown("### 📊 게임 세션 선택")
    
    sessions_data = load_game_sessions()
    
    if len(sessions_data) == 0:
        st.warning("⚠️ 저장된 게임 세션이 없습니다. 먼저 라이브 게임을 실행하고 결과를 저장해주세요.")
        return
    
    # 세션 선택
    session_options = ['전체 세션'] + [f"세션 {sid} (승률: {acc:.1f}%)" for sid, acc in zip(sessions_data['session_id'], sessions_data['accuracy'])]
    selected_session = st.selectbox(
        "분석할 세션 선택",
        options=session_options,
        key="selected_session"
    )
    
    if selected_session == '전체 세션':
        selected_session_ids = None
    else:
        selected_session_id = int(selected_session.split()[1])
        selected_session_ids = [selected_session_id]
    
    # 시뮬레이션 실행 버튼
    if st.button("🚀 시뮬레이션 실행", type="primary", use_container_width=True):
        with st.spinner("시뮬레이션 실행 중..."):
            # 전체 승률 계산
            overall_win_rate = calculate_overall_win_rate(sessions_data)
            
            # 선택된 세션에 대한 분석
            if selected_session_ids is not None:
                analysis_sessions = sessions_data[sessions_data['session_id'].isin(selected_session_ids)]
            else:
                analysis_sessions = sessions_data
            
            analysis_result = analyze_all_sessions(
                sessions_data,
                initial_bankroll,
                base_unit,
                odds,
                selected_session_ids
            )
            
            # 결과 저장
            st.session_state['overall_win_rate'] = overall_win_rate
            st.session_state['analysis_result'] = analysis_result
            st.session_state['analysis_sessions'] = analysis_sessions
            st.rerun()
    
    # 결과 표시
    if 'analysis_result' in st.session_state and 'overall_win_rate' in st.session_state:
        overall_win_rate = st.session_state['overall_win_rate']
        analysis_result = st.session_state['analysis_result']
        analysis_sessions = st.session_state.get('analysis_sessions', sessions_data)
        
        # 승률 정보 섹션 (최상단)
        st.markdown("---")
        st.markdown("### 📈 승률 정보")
        
        col_win1, col_win2, col_win3, col_win4 = st.columns(4)
        with col_win1:
            st.metric(
                "전체 평균 승률",
                f"{overall_win_rate['overall_win_rate']:.2f}%",
                delta=None
            )
        with col_win2:
            st.metric(
                "총 예측 횟수",
                f"{overall_win_rate['total_predictions']:,}"
            )
        with col_win3:
            st.metric(
                "총 성공 횟수",
                f"{overall_win_rate['total_successes']:,}"
            )
        with col_win4:
            st.metric(
                "총 실패 횟수",
                f"{overall_win_rate['total_failures']:,}"
            )
        
        # 승률 분포 히스토그램
        if len(analysis_sessions) > 0:
            st.markdown("#### 승률 분포")
            fig, ax = plt.subplots(figsize=(10, 4))
            ax.hist(analysis_sessions['accuracy'].dropna(), bins=20, edgecolor='black', alpha=0.7)
            ax.set_xlabel('승률 (%)')
            ax.set_ylabel('세션 수')
            ax.set_title('세션별 승률 분포')
            ax.grid(True, alpha=0.3)
            st.pyplot(fig)
            plt.close()
            
            # 승률 통계
            col_stat1, col_stat2, col_stat3 = st.columns(3)
            with col_stat1:
                st.metric("최고 승률", f"{analysis_sessions['accuracy'].max():.2f}%")
            with col_stat2:
                st.metric("최저 승률", f"{analysis_sessions['accuracy'].min():.2f}%")
            with col_stat3:
                st.metric("평균 승률", f"{analysis_sessions['accuracy'].mean():.2f}%")
        
        # 전략별 비교 섹션
        st.markdown("---")
        st.markdown("### 🎯 전략별 비교")
        
        if len(analysis_result['overall_stats']) > 0:
            # 전략별 평균 수익률 비교 테이블
            comparison_data = []
            for strategy_name, stats in analysis_result['overall_stats'].items():
                comparison_data.append({
                    '전략': strategy_name,
                    '평균 수익률 (%)': f"{stats['avg_profit_rate']:.2f}",
                    '평균 최종 자본금 (원)': f"{stats['avg_final_bankroll']:,.0f}",
                    '평균 수익 (원)': f"{stats['avg_profit']:,.0f}",
                    '파산 발생 횟수': stats['total_bankrupt_count'],
                    '파산률 (%)': f"{stats['bankrupt_rate']:.2f}"
                })
            
            comparison_df = pd.DataFrame(comparison_data)
            st.dataframe(comparison_df, use_container_width=True, hide_index=True)
            
            # 전략별 수익률 비교 막대 그래프
            st.markdown("#### 전략별 평균 수익률 비교")
            fig, ax = plt.subplots(figsize=(10, 6))
            strategies = list(analysis_result['overall_stats'].keys())
            profit_rates = [analysis_result['overall_stats'][s]['avg_profit_rate'] for s in strategies]
            colors = ['#FF6B6B', '#4ECDC4', '#95E1D3']
            bars = ax.bar(strategies, profit_rates, color=colors, alpha=0.7, edgecolor='black')
            ax.set_ylabel('평균 수익률 (%)')
            ax.set_title('전략별 평균 수익률 비교')
            ax.grid(True, alpha=0.3, axis='y')
            ax.axhline(y=0, color='black', linestyle='-', linewidth=0.5)
            
            # 값 표시
            for bar, rate in zip(bars, profit_rates):
                height = bar.get_height()
                ax.text(bar.get_x() + bar.get_width()/2., height,
                       f'{rate:.2f}%',
                       ha='center', va='bottom' if height >= 0 else 'top')
            
            st.pyplot(fig)
            plt.close()
        
        # 세션별 상세 결과 섹션
        st.markdown("---")
        st.markdown("### 📋 세션별 상세 결과")
        
        if len(analysis_result['session_results']) > 0:
            # 세션별 결과 테이블
            session_detail_data = []
            for session_result in analysis_result['session_results']:
                session_detail_data.append({
                    '세션 ID': session_result['session_id'],
                    '승률 (%)': f"{session_result['win_rate']:.2f}",
                    '예측 횟수': session_result['total_predictions'],
                    '성공 횟수': session_result['total_successes'],
                    '실패 횟수': session_result['total_failures'],
                    '마틴게일 수익률 (%)': f"{session_result['martingale']['profit_rate']:.2f}",
                    '마틴게일 최종자본 (원)': f"{session_result['martingale']['final_bankroll']:,.0f}",
                    '다람베르 수익률 (%)': f"{session_result['dalembert']['profit_rate']:.2f}",
                    '다람베르 최종자본 (원)': f"{session_result['dalembert']['final_bankroll']:,.0f}",
                    '피보나치 수익률 (%)': f"{session_result['fibonacci']['profit_rate']:.2f}",
                    '피보나치 최종자본 (원)': f"{session_result['fibonacci']['final_bankroll']:,.0f}"
                })
            
            session_detail_df = pd.DataFrame(session_detail_data)
            st.dataframe(session_detail_df, use_container_width=True, hide_index=True)
            
            # 세션별 승률 vs 전략별 수익률 산점도
            st.markdown("#### 세션별 승률 vs 전략별 수익률")
            
            fig, axes = plt.subplots(1, 3, figsize=(18, 5))
            strategies = ['마틴게일', '다람베르', '피보나치']
            colors = ['#FF6B6B', '#4ECDC4', '#95E1D3']
            
            for idx, (strategy, color) in enumerate(zip(strategies, colors)):
                win_rates = [sr['win_rate'] for sr in analysis_result['session_results']]
                profit_rates = [sr[strategy.lower()]['profit_rate'] for sr in analysis_result['session_results']]
                
                axes[idx].scatter(win_rates, profit_rates, alpha=0.6, color=color, s=100, edgecolors='black')
                axes[idx].set_xlabel('세션 승률 (%)')
                axes[idx].set_ylabel('수익률 (%)')
                axes[idx].set_title(f'{strategy} 전략')
                axes[idx].grid(True, alpha=0.3)
                axes[idx].axhline(y=0, color='red', linestyle='--', linewidth=1)
                axes[idx].axvline(x=50, color='gray', linestyle='--', linewidth=1, alpha=0.5)
            
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()
            
            # 샘플 세션의 자본금 변화 추이
            if len(analysis_result['session_results']) > 0:
                st.markdown("#### 자본금 변화 추이 (첫 번째 세션)")
                sample_result = analysis_result['session_results'][0]
                
                fig, ax = plt.subplots(figsize=(14, 6))
                ax.plot(sample_result['martingale']['bankroll_history'], label='마틴게일', color='#FF6B6B', linewidth=2)
                ax.plot(sample_result['dalembert']['bankroll_history'], label='다람베르', color='#4ECDC4', linewidth=2)
                ax.plot(sample_result['fibonacci']['bankroll_history'], label='피보나치', color='#95E1D3', linewidth=2)
                ax.axhline(y=initial_bankroll, color='gray', linestyle='--', linewidth=1, alpha=0.5, label='초기 자본금')
                ax.set_xlabel('베팅 횟수')
                ax.set_ylabel('자본금 (원)')
                ax.set_title(f'세션 {sample_result["session_id"]} - 전략별 자본금 변화 추이')
                ax.legend()
                ax.grid(True, alpha=0.3)
                st.pyplot(fig)
                plt.close()
    
    # 증분 데이터 피보나치 베팅 시뮬레이션 섹션
    st.markdown("---")
    st.markdown("# 🎯 증분 데이터 피보나치 베팅 시뮬레이션")
    st.markdown("**신뢰도 기반 스킵 전략 검증과 동일한 방식으로 cutoff 이후 증분 데이터에 대해 피보나치 베팅 시뮬레이션을 수행합니다**")
    
    # 설정 섹션
    st.markdown("---")
    st.markdown("### ⚙️ 시뮬레이션 설정")
    
    # 데이터 새로고침 기능
    col_refresh1, col_refresh2 = st.columns([3, 1])
    with col_refresh1:
        st.markdown("")
    with col_refresh2:
        if st.button("🔄 데이터 새로고침", key="fib_refresh_data", use_container_width=True):
            # 세션 상태 초기화
            if 'fibonacci_betting_result' in st.session_state:
                del st.session_state['fibonacci_betting_result']
            st.rerun()
    
    col_fib1, col_fib2, col_fib3 = st.columns(3)
    with col_fib1:
        # 기준 Grid String ID 선택 (신뢰도 기반 스킵 전략 검증과 동일한 방식)
        df_all_strings = load_preprocessed_data()
        if len(df_all_strings) > 0:
            grid_string_options = []
            for _, row in df_all_strings.iterrows():
                grid_string_options.append((row['id'], row['created_at']))
            
            grid_string_options.sort(key=lambda x: x[0], reverse=True)
            
            current_selected = st.session_state.get('fib_cutoff_id', None)
            default_index = 0
            if current_selected is not None:
                option_ids = [None] + [opt[0] for opt in grid_string_options]
                if current_selected in option_ids:
                    default_index = option_ids.index(current_selected)
            
            selected_cutoff_id = st.selectbox(
                "기준 Grid String ID (이 ID 이후의 데이터 검증)",
                options=[None] + [opt[0] for opt in grid_string_options],
                format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {opt[1]}" for opt in grid_string_options if opt[0] == x), f"ID {x} 이후"),
                index=default_index,
                key="fib_cutoff_id_select"
            )
            
            if selected_cutoff_id is not None:
                st.session_state.fib_cutoff_id = selected_cutoff_id
                selected_info = df_all_strings[df_all_strings['id'] == selected_cutoff_id].iloc[0]
                st.caption(f"선택된 기준: ID {selected_cutoff_id} (길이: {selected_info['string_length']}, 생성일: {selected_info['created_at']})")
                
                # 이후 데이터 개수 확인
                conn = get_db_connection()
                if conn is not None:
                    try:
                        count_query = "SELECT COUNT(*) as count FROM preprocessed_grid_strings WHERE id > ?"
                        count_df = pd.read_sql_query(count_query, conn, params=[selected_cutoff_id])
                        after_count = count_df.iloc[0]['count']
                        st.caption(f"시뮬레이션 대상: {after_count}개의 grid_string")
                    except:
                        pass
                    finally:
                        conn.close()
            else:
                selected_cutoff_id = None
                st.caption("전체 데이터를 사용합니다")
        else:
            selected_cutoff_id = None
            st.warning("⚠️ 저장된 grid_string이 없습니다.")
        
        cutoff_grid_string_id = selected_cutoff_id
    
    with col_fib2:
        window_size = st.number_input(
            "윈도우 크기",
            min_value=3,
            max_value=20,
            value=5,
            step=1,
            key="fib_window_size"
        )
    
    with col_fib3:
        method = st.selectbox(
            "예측 방법",
            options=["빈도 기반", "가중치 기반"],
            index=0,
            key="fib_method"
        )
    
    col_fib4, col_fib5, col_fib6 = st.columns(3)
    with col_fib4:
        use_threshold = st.checkbox(
            "임계값 전략 사용",
            value=True,
            key="fib_use_threshold"
        )
        threshold = st.number_input(
            "임계값 (%)",
            min_value=0.0,
            max_value=100.0,
            value=56.0,
            step=1.0,
            key="fib_threshold",
            disabled=not use_threshold
        )
    
    with col_fib5:
        max_interval = st.number_input(
            "최대 예측 없음 간격",
            min_value=1,
            max_value=20,
            value=4,
            step=1,
            key="fib_max_interval"
        )
        confidence_skip_threshold = st.number_input(
            "신뢰도 스킵 임계값 (%)",
            min_value=0.0,
            max_value=100.0,
            value=51.5,
            step=0.1,
            key="fib_confidence_skip"
        )
    
    with col_fib6:
        main_base = st.selectbox(
            "1단계 메인 베팅 금액",
            options=[11, 6],
            index=0,
            key="fib_main_base"
        )
        initial_bankroll = st.number_input(
            "초기 자본금 (원)",
            min_value=1,
            value=1000,
            step=1000,
            key="fib_initial_bankroll"
        )
        odds = st.number_input(
            "배당률 (배)",
            min_value=1.0,
            value=2.0,
            step=0.1,
            key="fib_odds"
        )
    
    # 시뮬레이션 실행 버튼
    if cutoff_grid_string_id is None:
        st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
    
    if st.button("🚀 증분 데이터 피보나치 베팅 시뮬레이션 실행", type="primary", use_container_width=True, key="fib_run_button", disabled=(cutoff_grid_string_id is None)):
        if cutoff_grid_string_id is None:
            st.warning("⚠️ 기준 Grid String ID를 선택해주세요.")
        else:
            with st.spinner("시뮬레이션 실행 중... (시간이 소요될 수 있습니다)"):
                result = batch_simulate_fibonacci_betting_with_confidence_skip(
                    cutoff_grid_string_id=cutoff_grid_string_id,
                    window_size=window_size,
                    method=method,
                    use_threshold=use_threshold,
                    threshold=threshold,
                    max_interval=max_interval,
                    confidence_skip_threshold=confidence_skip_threshold,
                    initial_bankroll=initial_bankroll,
                    main_base=main_base,
                    odds=odds
                )
                
                if result is not None:
                    st.session_state['fibonacci_betting_result'] = result
                    st.session_state['fibonacci_betting_settings'] = {
                        'cutoff_grid_string_id': cutoff_grid_string_id,
                        'window_size': window_size,
                        'method': method,
                        'use_threshold': use_threshold,
                        'threshold': threshold,
                        'max_interval': max_interval,
                        'confidence_skip_threshold': confidence_skip_threshold,
                        'initial_bankroll': initial_bankroll,
                        'main_base': main_base,
                        'odds': odds
                    }
                    st.rerun()
                else:
                    st.error("시뮬레이션 실행 중 오류가 발생했습니다.")
    
    # 결과 표시
    if 'fibonacci_betting_result' in st.session_state:
        result = st.session_state['fibonacci_betting_result']
        settings = st.session_state.get('fibonacci_betting_settings', {})
        
        # 결과 저장 버튼
        st.markdown("---")
        col_save1, col_save2 = st.columns([3, 1])
        with col_save1:
            st.markdown("")
        with col_save2:
            if st.button("💾 결과 저장", type="primary", use_container_width=True, key="fib_save_button"):
                with st.spinner("결과 저장 중..."):
                    simulation_id = save_fibonacci_betting_simulation_result(
                        cutoff_grid_string_id=settings.get('cutoff_grid_string_id'),
                        window_size=settings.get('window_size'),
                        method=settings.get('method'),
                        use_threshold=settings.get('use_threshold'),
                        threshold=settings.get('threshold'),
                        max_interval=settings.get('max_interval'),
                        confidence_skip_threshold=settings.get('confidence_skip_threshold'),
                        initial_bankroll=settings.get('initial_bankroll'),
                        main_base=settings.get('main_base'),
                        odds=settings.get('odds'),
                        summary=result['summary'],
                        results=result['results']
                    )
                    
                    if simulation_id:
                        st.success(f"✅ 결과가 저장되었습니다! (Simulation ID: {simulation_id[:8]}...)")
                        st.session_state['last_saved_simulation_id'] = simulation_id
                    else:
                        st.error("❌ 결과 저장에 실패했습니다.")
        
        result = st.session_state['fibonacci_betting_result']
        result = st.session_state['fibonacci_betting_result']
        summary = result['summary']
        results = result['results']
        
        # 승률 정보 섹션 (최상단)
        st.markdown("---")
        st.markdown("### 📈 승률 정보")
        
        col_win1, col_win2, col_win3, col_win4 = st.columns(4)
        with col_win1:
            st.metric(
                "전체 평균 승률",
                f"{summary['avg_accuracy']:.2f}%",
                delta=None
            )
        with col_win2:
            st.metric(
                "총 예측 횟수",
                f"{summary['total_predictions']:,}"
            )
        with col_win3:
            st.metric(
                "총 성공 횟수",
                f"{summary['total_successes']:,}"
            )
        with col_win4:
            st.metric(
                "총 실패 횟수",
                f"{summary['total_failures']:,}"
            )
        
        # 피보나치 베팅 시뮬레이션 결과 섹션
        st.markdown("---")
        st.markdown("### 💰 피보나치 베팅 시뮬레이션 결과")
        
        # 전체 통계
        st.markdown("#### 전체 통계 (연속 게임 결과)")
        st.info("💡 모든 grid_string이 하나의 연속 게임으로 처리되었습니다. 자본금과 피보나치 단계가 grid_string 간에 연속적으로 유지됩니다.")
        col_stat1, col_stat2, col_stat3, col_stat4, col_stat5 = st.columns(5)
        with col_stat1:
            st.metric(
                "최종 자본금",
                f"{summary['final_bankroll']:,.0f}원",
                delta=f"{summary['profit']:,.0f}원"
            )
        with col_stat2:
            st.metric(
                "수익률",
                f"{summary['profit_rate']:.2f}%"
            )
        with col_stat3:
            st.metric(
                "최대 누적 손실",
                f"{summary['max_cumulative_loss']:,.0f}원"
            )
        with col_stat4:
            st.metric(
                "최대 도달 단계",
                f"{summary['max_stage_reached']}"
            )
        with col_stat5:
            st.metric(
                "자본금 부족 발생률",
                f"{summary['insufficient_funds_rate']:.2f}%"
            )
        
        # 첫 번째 게임 결과와 승률 상관관계 분석
        st.markdown("---")
        st.markdown("### 📊 첫 번째 게임 결과와 승률 상관관계 분석")
        
        first_result_analysis = []
        for r in results:
            first_result = r.get('first_prediction_result')
            if first_result is not None:
                first_result_analysis.append({
                    'grid_string_id': r['grid_string_id'],
                    '첫_게임_결과': '승리' if first_result else '패배',
                    '승률': r['win_rate'],
                    '예측_횟수': r['total_predictions']
                })
        
        if len(first_result_analysis) > 0:
            analysis_df = pd.DataFrame(first_result_analysis)
            
            # 통계 요약
            col_analysis1, col_analysis2 = st.columns(2)
            with col_analysis1:
                st.markdown("#### 첫 게임 승리 그룹")
                win_start = analysis_df[analysis_df['첫_게임_결과'] == '승리']
                if len(win_start) > 0:
                    st.metric("평균 승률", f"{win_start['승률'].mean():.2f}%")
                    st.metric("Grid String 수", len(win_start))
                    st.metric("평균 예측 횟수", f"{win_start['예측_횟수'].mean():.1f}")
                else:
                    st.info("첫 게임 승리로 시작한 grid_string이 없습니다.")
            
            with col_analysis2:
                st.markdown("#### 첫 게임 패배 그룹")
                loss_start = analysis_df[analysis_df['첫_게임_결과'] == '패배']
                if len(loss_start) > 0:
                    st.metric("평균 승률", f"{loss_start['승률'].mean():.2f}%")
                    st.metric("Grid String 수", len(loss_start))
                    st.metric("평균 예측 횟수", f"{loss_start['예측_횟수'].mean():.1f}")
                else:
                    st.info("첫 게임 패배로 시작한 grid_string이 없습니다.")
            
            # 비교 차트
            st.markdown("#### 승률 비교")
            fig, ax = plt.subplots(figsize=(10, 6))
            if len(win_start) > 0 and len(loss_start) > 0:
                data_to_plot = [win_start['승률'].tolist(), loss_start['승률'].tolist()]
                labels = ['첫 게임 승리', '첫 게임 패배']
                bp = ax.boxplot(data_to_plot, labels=labels, patch_artist=True)
                colors = ['#4ECDC4', '#FF6B6B']
                for patch, color in zip(bp['boxes'], colors):
                    patch.set_facecolor(color)
                    patch.set_alpha(0.7)
                ax.set_ylabel('승률 (%)')
                ax.set_title('첫 게임 결과별 승률 분포')
                ax.grid(True, alpha=0.3, axis='y')
                st.pyplot(fig)
                plt.close()
            
            # 상세 테이블
            st.markdown("#### 상세 데이터")
            st.dataframe(analysis_df, use_container_width=True, hide_index=True)
        
        # Grid String별 정보 테이블
        st.markdown("---")
        st.markdown("### 📋 Grid String별 정보")
        st.caption("⚠️ 베팅 결과는 모든 grid_string을 하나의 연속 게임으로 처리한 전체 결과입니다.")
        detail_data = []
        for r in results:
            detail_data.append({
                'Grid String ID': r['grid_string_id'],
                '첫 게임 결과': '승리' if r.get('first_prediction_result') == True else ('패배' if r.get('first_prediction_result') == False else 'N/A'),
                '승률 (%)': f"{r['win_rate']:.2f}",
                '예측 횟수': r['total_predictions'],
                '성공 횟수': r['total_successes'],
                '실패 횟수': r['total_failures']
            })
        
        detail_df = pd.DataFrame(detail_data)
        st.dataframe(detail_df, use_container_width=True, hide_index=True)
        
        # 개별 Grid String 상세 히스토리
        if len(results) > 0:
            st.markdown("---")
            st.markdown("### 🔍 개별 Grid String 상세 히스토리")
            
            selected_grid_string_id = st.selectbox(
                "상세 히스토리를 확인할 Grid String 선택",
                options=[r['grid_string_id'] for r in results],
                format_func=lambda x: f"Grid String ID: {x} (승률: {next((r['win_rate'] for r in results if r['grid_string_id'] == x), 0):.2f}%)",
                key="fib_detail_select"
            )
            
            # 선택된 grid_string의 히스토리 필터링
            all_combined_history = result.get('all_combined_history', [])
            selected_history = [h for h in all_combined_history if h.get('grid_string_id') == selected_grid_string_id]
            
            if len(selected_history) > 0:
                selected_info = next((r for r in results if r['grid_string_id'] == selected_grid_string_id), None)
                
                st.info(f"Grid String ID: {selected_grid_string_id} | 승률: {selected_info['win_rate']:.2f}% | 예측 횟수: {selected_info['total_predictions']} | 첫 게임 결과: {'승리' if selected_info.get('first_prediction_result') == True else ('패배' if selected_info.get('first_prediction_result') == False else 'N/A')}")
                
                # 히스토리 테이블
                history_data = []
                for idx, h in enumerate(selected_history, 1):
                    history_data.append({
                        '순서': idx,
                        'Step': h.get('step', ''),
                        'Prefix': h.get('prefix', ''),
                        '예측값': h.get('predicted', ''),
                        '실제값': h.get('actual', ''),
                        '결과': '✅ 승리' if h.get('is_correct') == True else ('❌ 패배' if h.get('is_correct') == False else '⚪'),
                        '신뢰도 (%)': f"{h.get('confidence', 0):.2f}",
                        '강제 예측': '⚡' if h.get('is_forced', False) else ''
                    })
                
                history_df = pd.DataFrame(history_data)
                st.dataframe(history_df, use_container_width=True, hide_index=True)
            else:
                st.warning(f"Grid String ID {selected_grid_string_id}의 히스토리를 찾을 수 없습니다.")
    
    # 저장된 시뮬레이션 조회 및 인사이트 분석 섹션
    st.markdown("---")
    st.markdown("# 📊 저장된 시뮬레이션 조회 및 인사이트 분석")
    
    # 저장된 시뮬레이션 목록
    st.markdown("### 📋 저장된 시뮬레이션 목록")
    
    simulations_df = load_fibonacci_betting_simulations(limit=100)
    
    if len(simulations_df) == 0:
        st.info("저장된 시뮬레이션이 없습니다. 시뮬레이션을 실행하고 결과를 저장해주세요.")
    else:
        # 시뮬레이션 선택
        simulation_options = []
        for _, row in simulations_df.iterrows():
            sim_id = row['simulation_id']
            created_at = pd.to_datetime(row['created_at']).strftime('%Y-%m-%d %H:%M:%S')
            cutoff_id = row['cutoff_grid_string_id']
            profit_rate = row['profit_rate']
            avg_accuracy = row['avg_accuracy']
            display_text = f"ID: {sim_id[:8]}... | Cutoff: {cutoff_id} | 승률: {avg_accuracy:.2f}% | 수익률: {profit_rate:.2f}% | {created_at}"
            simulation_options.append((sim_id, display_text))
        
        selected_simulation_id = st.selectbox(
            "분석할 시뮬레이션 선택",
            options=[opt[0] for opt in simulation_options],
            format_func=lambda x: next((opt[1] for opt in simulation_options if opt[0] == x), x),
            key="fib_insight_select"
        )
        
        if selected_simulation_id:
            # 시뮬레이션 상세 정보
            detail = load_fibonacci_betting_simulation_detail(selected_simulation_id)
            
            if detail:
                session = detail['session']
                results = detail['results']
                
                st.markdown("---")
                st.markdown("### 📈 시뮬레이션 상세 정보")
                
                col_detail1, col_detail2, col_detail3 = st.columns(3)
                with col_detail1:
                    st.metric("총 Grid String 수", f"{session['total_grid_strings']}")
                    st.metric("총 예측 횟수", f"{session['total_predictions']:,}")
                    st.metric("평균 승률", f"{session['avg_accuracy']:.2f}%")
                with col_detail2:
                    st.metric("최종 자본금", f"{session['final_bankroll']:,.0f}원")
                    st.metric("수익/손실", f"{session['profit']:,.0f}원")
                    st.metric("수익률", f"{session['profit_rate']:.2f}%")
                with col_detail3:
                    st.metric("최대 누적 손실", f"{session['max_cumulative_loss']:,.0f}원")
                    st.metric("최대 도달 단계", f"{session['max_stage_reached']}")
                    st.metric("자본금 부족 발생률", f"{session['insufficient_funds_rate']:.2f}%")
                
                # 인사이트 분석
                st.markdown("---")
                st.markdown("### 🔍 첫 게임 결과와 승률 상관관계 분석")
                
                if st.button("📊 인사이트 분석 실행", type="primary", key="fib_insight_analyze"):
                    with st.spinner("인사이트 분석 중..."):
                        analysis_result = analyze_first_game_result_correlation(selected_simulation_id)
                        
                        if analysis_result and 'error' not in analysis_result:
                            # 통계 요약
                            st.markdown("#### 통계 요약")
                            
                            col_insight1, col_insight2 = st.columns(2)
                            
                            with col_insight1:
                                st.markdown("##### 첫 게임 승리 그룹")
                                win_stats = analysis_result['win_start_stats']
                                st.metric("Grid String 수", win_stats['count'])
                                st.metric("평균 승률", f"{win_stats['mean_win_rate']:.2f}%")
                                st.metric("표준 편차", f"{win_stats['std_win_rate']:.2f}%")
                                st.metric("중앙값 승률", f"{win_stats['median_win_rate']:.2f}%")
                                st.metric("최소 승률", f"{win_stats['min_win_rate']:.2f}%")
                                st.metric("최대 승률", f"{win_stats['max_win_rate']:.2f}%")
                            
                            with col_insight2:
                                st.markdown("##### 첫 게임 패배 그룹")
                                loss_stats = analysis_result['loss_start_stats']
                                st.metric("Grid String 수", loss_stats['count'])
                                st.metric("평균 승률", f"{loss_stats['mean_win_rate']:.2f}%")
                                st.metric("표준 편차", f"{loss_stats['std_win_rate']:.2f}%")
                                st.metric("중앙값 승률", f"{loss_stats['median_win_rate']:.2f}%")
                                st.metric("최소 승률", f"{loss_stats['min_win_rate']:.2f}%")
                                st.metric("최대 승률", f"{loss_stats['max_win_rate']:.2f}%")
                            
                            # 통계적 검정 결과
                            st.markdown("#### 통계적 검정 결과")
                            col_test1, col_test2, col_test3 = st.columns(3)
                            with col_test1:
                                st.metric("평균 차이", f"{analysis_result['difference']:.2f}%")
                            with col_test2:
                                st.metric("t-통계량", f"{analysis_result['t_stat']:.4f}")
                            with col_test3:
                                p_value = analysis_result['p_value']
                                significance = "유의함" if p_value < 0.05 else "유의하지 않음"
                                st.metric("p-value", f"{p_value:.4f}", delta=significance)
                            
                            # 효과 크기
                            st.markdown("#### 효과 크기")
                            col_effect1, col_effect2 = st.columns(2)
                            with col_effect1:
                                st.metric("Cohen's d", f"{analysis_result['cohens_d']:.4f}")
                            with col_effect2:
                                st.metric("효과 크기 해석", analysis_result['effect_size_interpretation'])
                            
                            # 인사이트 요약
                            st.markdown("#### 💡 인사이트 요약")
                            difference = analysis_result['difference']
                            if difference > 0:
                                insight_text = f"""
                                **첫 게임 승리로 시작한 grid_string은 평균 승률이 {win_stats['mean_win_rate']:.2f}%로, 
                                첫 게임 패배로 시작한 grid_string의 평균 승률 {loss_stats['mean_win_rate']:.2f}%보다 
                                {abs(difference):.2f}% 높습니다.**
                                
                                - 통계적 유의성: {'유의함' if p_value < 0.05 else '유의하지 않음'} (p-value: {p_value:.4f})
                                - 효과 크기: {analysis_result['effect_size_interpretation']} (Cohen's d: {analysis_result['cohens_d']:.4f})
                                """
                            else:
                                insight_text = f"""
                                **첫 게임 패배로 시작한 grid_string은 평균 승률이 {loss_stats['mean_win_rate']:.2f}%로, 
                                첫 게임 승리로 시작한 grid_string의 평균 승률 {win_stats['mean_win_rate']:.2f}%보다 
                                {abs(difference):.2f}% 높습니다.**
                                
                                - 통계적 유의성: {'유의함' if p_value < 0.05 else '유의하지 않음'} (p-value: {p_value:.4f})
                                - 효과 크기: {analysis_result['effect_size_interpretation']} (Cohen's d: {analysis_result['cohens_d']:.4f})
                                """
                            st.info(insight_text)
                            
                            # 시각화
                            st.markdown("#### 시각화")
                            
                            # 박스플롯
                            fig, axes = plt.subplots(1, 2, figsize=(14, 6))
                            
                            # 박스플롯
                            data_to_plot = [analysis_result['win_start_data'], analysis_result['loss_start_data']]
                            labels = ['첫 게임 승리', '첫 게임 패배']
                            bp = axes[0].boxplot(data_to_plot, labels=labels, patch_artist=True)
                            colors = ['#4ECDC4', '#FF6B6B']
                            for patch, color in zip(bp['boxes'], colors):
                                patch.set_facecolor(color)
                                patch.set_alpha(0.7)
                            axes[0].set_ylabel('승률 (%)')
                            axes[0].set_title('첫 게임 결과별 승률 분포 (Box Plot)')
                            axes[0].grid(True, alpha=0.3, axis='y')
                            
                            # 히스토그램
                            axes[1].hist(analysis_result['win_start_data'], bins=20, alpha=0.7, label='첫 게임 승리', color='#4ECDC4', edgecolor='black')
                            axes[1].hist(analysis_result['loss_start_data'], bins=20, alpha=0.7, label='첫 게임 패배', color='#FF6B6B', edgecolor='black')
                            axes[1].set_xlabel('승률 (%)')
                            axes[1].set_ylabel('빈도')
                            axes[1].set_title('첫 게임 결과별 승률 분포 (Histogram)')
                            axes[1].legend()
                            axes[1].grid(True, alpha=0.3, axis='y')
                            
                            plt.tight_layout()
                            st.pyplot(fig)
                            plt.close()
                            
                            # 상세 데이터 테이블
                            st.markdown("#### 상세 데이터")
                            results_df = pd.DataFrame(results)
                            results_df['첫_게임_결과'] = results_df['first_prediction_result'].apply(
                                lambda x: '승리' if x == True else ('패배' if x == False else 'N/A')
                            )
                            display_df = results_df[['grid_string_id', '첫_게임_결과', 'win_rate', 'total_predictions', 'total_successes', 'total_failures']].copy()
                            display_df.columns = ['Grid String ID', '첫 게임 결과', '승률 (%)', '예측 횟수', '성공 횟수', '실패 횟수']
                            display_df['승률 (%)'] = display_df['승률 (%)'].apply(lambda x: f"{x:.2f}")
                            st.dataframe(display_df, use_container_width=True, hide_index=True)
                            
                            # 분석 결과를 세션에 저장
                            st.session_state['last_insight_analysis'] = analysis_result
                        elif analysis_result and 'error' in analysis_result:
                            st.warning(f"⚠️ {analysis_result['error']}")
                        else:
                            st.error("인사이트 분석 중 오류가 발생했습니다.")


# ============================================================================
# 증분 데이터 피보나치 베팅 시뮬레이션 함수
# ============================================================================

def get_fibonacci_bet_amounts(stage: int, main_base: int = 11) -> Dict:
    """
    단계별 메인/타이 베팅 금액 반환
    
    Args:
        stage: 현재 단계 (1부터 시작)
        main_base: 1단계 메인 베팅 금액 (기본값: 11)
    
    Returns:
        {'main': 메인 베팅 금액, 'tie': 타이 베팅 금액}
    """
    # 피보나치 테이블 생성 (15단계까지)
    table = create_fibonacci_betting_table(max_stages=15, main_base=main_base)
    
    if stage < 1 or stage > len(table):
        return {'main': 0, 'tie': 0}
    
    row = table.iloc[stage - 1]
    return {
        'main': int(row['메인(Main)']),
        'tie': int(row['타이(Tie)'])
    }


def simulate_fibonacci_betting_for_history(
    history: List[Dict],
    initial_bankroll: float,
    main_base: int,
    odds: float
) -> Dict:
    """
    검증 히스토리를 기반으로 피보나치 베팅 시뮬레이션
    
    Args:
        history: 검증 히스토리 리스트 (validated=True, skipped=False인 항목만)
        initial_bankroll: 초기 자본금
        main_base: 1단계 메인 베팅 금액
        odds: 배당률
    
    Returns:
        {
            'final_bankroll': 최종 자본금,
            'profit': 총 수익/손실,
            'profit_rate': 수익률,
            'max_cumulative_loss': 최대 누적 손실,
            'max_stage_reached': 최대 도달 단계,
            'bankroll_history': 자본금 변화 시계열,
            'cumulative_loss_history': 누적 손실 시계열,
            'stage_history': 단계 변화 시계열,
            'bet_amount_history': 베팅 금액 시계열 (메인+타이),
            'insufficient_funds_count': 자본금 부족 발생 횟수
        }
    """
    # 검증된 예측만 필터링
    validated_history = [
        h for h in history 
        if h.get('validated', False) and not h.get('skipped', False)
    ]
    
    if len(validated_history) == 0:
        return {
            'final_bankroll': initial_bankroll,
            'profit': 0.0,
            'profit_rate': 0.0,
            'max_cumulative_loss': 0.0,
            'max_stage_reached': 0,
            'bankroll_history': [initial_bankroll],
            'cumulative_loss_history': [0.0],
            'stage_history': [0],
            'bet_amount_history': [0.0],
            'insufficient_funds_count': 0
        }
    
    bankroll = initial_bankroll
    current_stage = 1  # 현재 단계 (1부터 시작)
    max_stage_reached = 1
    
    # 시계열 추적
    bankroll_history = [initial_bankroll]
    cumulative_loss_history = [0.0]
    stage_history = [0]  # 시작 시점
    bet_amount_history = [0.0]
    
    max_cumulative_loss = 0.0
    insufficient_funds_count = 0
    
    # 각 검증된 예측에 대해 베팅 수행
    for h in validated_history:
        # 현재 단계의 베팅 금액 계산
        bet_amounts = get_fibonacci_bet_amounts(current_stage, main_base)
        main_bet = bet_amounts['main']
        tie_bet = bet_amounts['tie']
        total_bet = main_bet + tie_bet
        
        # 자본금 부족 확인 (카운트만 증가, 시뮬레이션은 계속 진행)
        if bankroll < total_bet:
            insufficient_funds_count += 1
        
        # 베팅 수행 (음수 자본금 허용)
        bankroll -= total_bet
        
        # 누적 손실 계산 (초기 자본금 대비 손실)
        cumulative_loss = initial_bankroll - bankroll
        
        # 시계열 기록 (베팅 후)
        bankroll_history.append(bankroll)
        cumulative_loss_history.append(cumulative_loss)
        stage_history.append(current_stage)
        bet_amount_history.append(total_bet)
        
        # 최대 누적 손실 업데이트
        if cumulative_loss > max_cumulative_loss:
            max_cumulative_loss = cumulative_loss
        
        # 결과 확인 (모든 승리는 메인 베팅 승리로 간주)
        is_correct = h.get('is_correct', False)
        
        if is_correct:
            # 승리: 메인 베팅 승리로 처리
            # 배당금 획득 (메인 베팅만)
            bankroll += main_bet * odds  # 배당금 획득
            
            # 누적 손실 재계산 (초기 자본금 대비 손실)
            cumulative_loss = initial_bankroll - bankroll
            
            # 피보나치 규칙에 따라 단계 이동
            if current_stage == 1:
                # 1단계 승리: 세션 계속 진행 (단계 유지)
                pass  # 단계는 1에 유지
            elif current_stage <= 3:
                # 2-3단계 승리: 1단계로 복귀
                current_stage = 1
            else:
                # 4단계 이상 승리: 2단계 하향
                current_stage = max(1, current_stage - 2)
        else:
            # 패배: 다음 단계로 이동
            current_stage += 1
            max_stage_reached = max(max_stage_reached, current_stage)
        
        # 시계열 기록 (결과 반영 후 - 승리/패배 모두)
        bankroll_history[-1] = bankroll
        cumulative_loss_history[-1] = cumulative_loss
        stage_history[-1] = current_stage
        
        # 최대 누적 손실 업데이트 (승리/패배 후 모두 확인)
        if cumulative_loss > max_cumulative_loss:
            max_cumulative_loss = cumulative_loss
    
    final_bankroll = bankroll
    profit = final_bankroll - initial_bankroll
    profit_rate = (profit / initial_bankroll * 100) if initial_bankroll > 0 else 0.0
    
    return {
        'final_bankroll': final_bankroll,
        'profit': profit,
        'profit_rate': profit_rate,
        'max_cumulative_loss': max_cumulative_loss,
        'max_stage_reached': max_stage_reached,
        'bankroll_history': bankroll_history,
        'cumulative_loss_history': cumulative_loss_history,
        'stage_history': stage_history,
        'bet_amount_history': bet_amount_history,
        'insufficient_funds_count': insufficient_funds_count
    }


def batch_simulate_fibonacci_betting_with_confidence_skip(
    cutoff_grid_string_id: int,
    window_size: int = 7,
    method: str = "빈도 기반",
    use_threshold: bool = True,
    threshold: float = 60,
    max_interval: int = 6,
    confidence_skip_threshold: float = 51,
    initial_bankroll: float = 10000,
    main_base: int = 11,
    odds: float = 2.0
) -> Dict:
    """
    cutoff 이후 모든 grid_string에 대해 피보나치 베팅 시뮬레이션
    
    Args:
        cutoff_grid_string_id: 기준 grid_string ID
        window_size: 윈도우 크기
        method: 예측 방법
        use_threshold: 임계값 전략 사용 여부
        threshold: 임계값
        max_interval: 최대 예측 없음 간격
        confidence_skip_threshold: 스킵할 신뢰도 임계값
        initial_bankroll: 초기 자본금
        main_base: 1단계 메인 베팅 금액
        odds: 배당률
    
    Returns:
        {
            'results': 각 grid_string별 결과 리스트,
            'summary': 전체 통계
        }
    """
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # cutoff_grid_string_id 이후의 모든 grid_string 로드
        query = "SELECT id FROM preprocessed_grid_strings WHERE id > ? ORDER BY id"
        df = pd.read_sql_query(query, conn, params=[cutoff_grid_string_id])
        
        if len(df) == 0:
            return {
                'results': [],
                'summary': {
                    'total_grid_strings': 0,
                    'avg_accuracy': 0.0,
                    'avg_final_bankroll': initial_bankroll,
                    'avg_profit': 0.0,
                    'avg_profit_rate': 0.0,
                    'avg_max_cumulative_loss': 0.0,
                    'max_max_cumulative_loss': 0.0,
                    'avg_max_stage_reached': 0,
                    'total_insufficient_funds_count': 0,
                    'insufficient_funds_rate': 0.0
                }
            }
        
        grid_string_ids = df['id'].tolist()
        all_combined_history = []  # 모든 grid_string의 history를 합친 리스트
        grid_string_boundaries = []  # 각 grid_string의 시작 인덱스 (시계열 추적용)
        grid_string_info = []  # 각 grid_string의 정보
        
        # 각 grid_string에 대해 검증 수행 (베팅은 나중에 연속으로 실행)
        for grid_string_id in grid_string_ids:
            # 검증 수행
            validation_result = validate_interactive_multi_step_scenario_with_confidence_skip(
                grid_string_id,
                cutoff_grid_string_id,
                window_size=window_size,
                method=method,
                use_threshold=use_threshold,
                threshold=threshold,
                max_interval=max_interval,
                reverse_forced_prediction=False,
                confidence_skip_threshold=confidence_skip_threshold
            )
            
            if validation_result is None:
                continue
            
            history = validation_result.get('history', [])
            if len(history) == 0:
                continue
            
            # 검증된 예측만 필터링
            validated_predictions = [
                h for h in history 
                if h.get('validated', False) and not h.get('skipped', False)
            ]
            
            if len(validated_predictions) == 0:
                continue
            
            # 승률 계산
            correct_count = sum(1 for h in validated_predictions if h.get('is_correct', False))
            win_rate = (correct_count / len(validated_predictions) * 100) if len(validated_predictions) > 0 else 0.0
            
            # 각 예측에 grid_string_id 추가
            for pred in validated_predictions:
                pred['grid_string_id'] = grid_string_id
            
            # grid_string 정보 저장
            first_prediction_result = validated_predictions[0].get('is_correct', False) if len(validated_predictions) > 0 else None
            grid_string_info.append({
                'grid_string_id': grid_string_id,
                'win_rate': win_rate,
                'total_predictions': len(validated_predictions),
                'total_successes': correct_count,
                'total_failures': len(validated_predictions) - correct_count,
                'start_index': len(all_combined_history),  # 이 grid_string의 시작 인덱스
                'first_prediction_result': first_prediction_result  # 첫 번째 예측 결과 (True=승리, False=패배)
            })
            
            # grid_string 경계 기록
            grid_string_boundaries.append(len(all_combined_history))
            
            # 모든 history를 하나로 합치기 (연속 게임)
            all_combined_history.extend(validated_predictions)
        
        # 모든 grid_string을 하나의 연속 게임으로 시뮬레이션
        if len(all_combined_history) == 0:
            return {
                'results': [],
                'summary': {
                    'total_grid_strings': 0,
                    'avg_accuracy': 0.0,
                    'avg_final_bankroll': initial_bankroll,
                    'avg_profit': 0.0,
                    'avg_profit_rate': 0.0,
                    'avg_max_cumulative_loss': 0.0,
                    'max_max_cumulative_loss': 0.0,
                    'avg_max_stage_reached': 0,
                    'total_insufficient_funds_count': 0,
                    'insufficient_funds_rate': 0.0
                }
            }
        
        # 하나의 연속 게임으로 피보나치 베팅 시뮬레이션 실행
        betting_result = simulate_fibonacci_betting_for_history(
            all_combined_history,
            initial_bankroll,
            main_base,
            odds
        )
        
        # grid_string별 정보에 베팅 결과 추가 (참고용, 실제로는 전체 연속 게임 결과)
        results = []
        for info in grid_string_info:
            results.append({
                'grid_string_id': info['grid_string_id'],
                'win_rate': info['win_rate'],
                'total_predictions': info['total_predictions'],
                'total_successes': info['total_successes'],
                'total_failures': info['total_failures'],
                'first_prediction_result': info['first_prediction_result'],
                'start_index': info['start_index'],
                'betting_result': betting_result  # 전체 연속 게임 결과 (모든 grid_string 동일)
            })
        
        # 전체 히스토리와 grid_string 정보를 결과에 포함
        return_data = {
            'results': results,
            'summary': None,  # 나중에 계산
            'all_combined_history': all_combined_history,  # 전체 히스토리 (grid_string_id 포함)
            'grid_string_info': grid_string_info  # grid_string 정보
        }
        
        # 요약 통계 계산 (전체 연속 게임 결과 사용)
        if len(results) > 0:
            total_grid_strings = len(results)
            total_predictions = sum(r['total_predictions'] for r in results)
            total_successes = sum(r['total_successes'] for r in results)
            avg_accuracy = (total_successes / total_predictions * 100) if total_predictions > 0 else 0.0
            
            # 전체 연속 게임 결과 (모든 grid_string이 하나의 게임이므로 동일)
            final_bankroll = betting_result['final_bankroll']
            profit = betting_result['profit']
            profit_rate = betting_result['profit_rate']
            max_cumulative_loss = betting_result['max_cumulative_loss']
            max_stage_reached = betting_result['max_stage_reached']
            insufficient_funds_count = betting_result['insufficient_funds_count']
            insufficient_funds_rate = (insufficient_funds_count / total_predictions * 100) if total_predictions > 0 else 0.0
            
            summary = {
                'total_grid_strings': total_grid_strings,
                'avg_accuracy': avg_accuracy,
                'total_predictions': total_predictions,
                'total_successes': total_successes,
                'total_failures': total_predictions - total_successes,
                'final_bankroll': final_bankroll,
                'profit': profit,
                'profit_rate': profit_rate,
                'max_cumulative_loss': max_cumulative_loss,
                'max_stage_reached': max_stage_reached,
                'total_insufficient_funds_count': insufficient_funds_count,
                'insufficient_funds_rate': insufficient_funds_rate
            }
        else:
            summary = {
                'total_grid_strings': 0,
                'avg_accuracy': 0.0,
                'total_predictions': 0,
                'total_successes': 0,
                'total_failures': 0,
                'final_bankroll': initial_bankroll,
                'profit': 0.0,
                'profit_rate': 0.0,
                'max_cumulative_loss': 0.0,
                'max_stage_reached': 0,
                'total_insufficient_funds_count': 0,
                'insufficient_funds_rate': 0.0
            }
        
        return_data['summary'] = summary
        return return_data
        
    except Exception as e:
        st.error(f"배치 시뮬레이션 중 오류 발생: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    main()
