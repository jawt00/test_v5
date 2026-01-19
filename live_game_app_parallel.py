"""
신뢰도 스킵 전략 라이브 게임 앱
스텝별로 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임
"""

import streamlit as st

# 페이지 설정 (모든 import 전에 실행되어야 함)
st.set_page_config(
    page_title="Live Game (Parallel)",
    page_icon="🎮",
    layout="wide"
)

import pandas as pd

# 기존 앱의 함수들 import
from hypothesis_validation_app import (
    get_db_connection,
    load_preprocessed_data,
    load_ngram_chunks,
    build_frequency_model,
    build_weighted_model,
    predict_for_prefix,
    predict_with_fallback_interval,
    get_next_prefix
)

# ============================================================================
# 데이터베이스 테이블 생성 및 저장 함수
# ============================================================================

def create_live_game_tables():
    """
    라이브 게임 데이터 저장을 위한 테이블 생성
    """
    conn = get_db_connection()
    if conn is None:
        return False
    
    cursor = conn.cursor()
    
    try:
        # 게임 세션 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_game_sessions (
                session_id INTEGER PRIMARY KEY AUTOINCREMENT,
                grid_string TEXT NOT NULL,
                window_size INTEGER NOT NULL,
                method TEXT NOT NULL,
                use_threshold BOOLEAN NOT NULL,
                threshold REAL,
                max_interval INTEGER,
                confidence_skip_threshold REAL,
                total_steps INTEGER,
                total_predictions INTEGER,
                total_failures INTEGER,
                total_forced_predictions INTEGER,
                total_skipped_predictions INTEGER,
                max_consecutive_failures INTEGER,
                accuracy REAL,
                started_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                completed_at TIMESTAMP,
                auto_executed BOOLEAN DEFAULT 0
            )
        ''')
        
        # 게임 스텝 상세 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_game_steps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                step INTEGER NOT NULL,
                prefix TEXT NOT NULL,
                predicted_value TEXT,
                actual_value TEXT NOT NULL,
                confidence REAL,
                b_ratio REAL,
                p_ratio REAL,
                is_forced BOOLEAN DEFAULT 0,
                strategy_name TEXT,
                current_interval INTEGER,
                has_prediction BOOLEAN DEFAULT 0,
                validated BOOLEAN DEFAULT 0,
                skipped BOOLEAN DEFAULT 0,
                is_correct BOOLEAN,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (session_id) REFERENCES live_game_sessions(session_id)
            )
        ''')
        
        # 첫 번째 예측 결과 테이블
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS live_game_first_predictions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id INTEGER NOT NULL,
                first_prediction_step INTEGER NOT NULL,
                first_prediction_prefix TEXT NOT NULL,
                first_prediction_value TEXT NOT NULL,
                first_prediction_confidence REAL,
                first_prediction_is_forced BOOLEAN DEFAULT 0,
                first_prediction_actual_value TEXT NOT NULL,
                first_prediction_is_correct BOOLEAN,
                first_success_step INTEGER,
                first_success_prefix TEXT,
                first_failure_step INTEGER,
                first_failure_prefix TEXT,
                first_forced_step INTEGER,
                first_forced_prefix TEXT,
                created_at TIMESTAMP DEFAULT (datetime('now', '+9 hours')),
                FOREIGN KEY (session_id) REFERENCES live_game_sessions(session_id)
            )
        ''')
        
        # 인덱스 생성
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_sessions_created 
            ON live_game_sessions(started_at)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_steps_session 
            ON live_game_steps(session_id, step)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_steps_prefix 
            ON live_game_steps(prefix)
        ''')
        
        cursor.execute('''
            CREATE INDEX IF NOT EXISTS idx_steps_validated 
            ON live_game_steps(session_id, validated, is_correct)
        ''')
        
        conn.commit()
        return True
        
    except Exception as e:
        conn.rollback()
        st.error(f"라이브 게임 테이블 생성 오류: {str(e)}")
        return False
    finally:
        conn.close()

def save_live_game_session(game_state):
    """
    라이브 게임 세션 전체를 DB에 저장
    
    Args:
        game_state: 게임 상태 딕셔너리
    
    Returns:
        session_id: 저장된 세션 ID (실패 시 None)
    """
    # 테이블 생성 확인
    if not create_live_game_tables():
        return None
    
    conn = get_db_connection()
    if conn is None:
        return None
    
    cursor = conn.cursor()
    
    try:
        # 1. 게임 세션 저장
        accuracy = ((game_state['total_predictions'] - game_state['total_failures']) / 
                   game_state['total_predictions'] * 100) if game_state['total_predictions'] > 0 else 0.0
        
        cursor.execute('''
            INSERT INTO live_game_sessions (
                grid_string, window_size, method, use_threshold, threshold,
                max_interval, confidence_skip_threshold,
                total_steps, total_predictions, total_failures,
                total_forced_predictions, total_skipped_predictions,
                max_consecutive_failures, accuracy, auto_executed
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            game_state['grid_string'],
            game_state['window_size'],
            game_state['method'],
            game_state['use_threshold'],
            game_state.get('threshold'),
            game_state.get('max_interval'),
            game_state.get('confidence_skip_threshold'),
            game_state['current_step'],
            game_state['total_predictions'],
            game_state['total_failures'],
            game_state.get('total_forced_predictions', 0),
            game_state.get('total_skipped_predictions', 0),
            game_state['max_consecutive_failures'],
            accuracy,
            game_state.get('auto_executed', False)
        ))
        
        session_id = cursor.lastrowid
        
        # 2. 각 스텝 저장 및 첫 번째 예측 정보 추적
        first_prediction_info = None
        first_success_info = None
        first_failure_info = None
        first_forced_info = None
        
        for entry in game_state['history']:
            cursor.execute('''
                INSERT INTO live_game_steps (
                    session_id, step, prefix, predicted_value, actual_value,
                    confidence, b_ratio, p_ratio, is_forced, strategy_name,
                    current_interval, has_prediction, validated, skipped, is_correct
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                entry.get('step', 0),
                entry.get('prefix', ''),
                entry.get('predicted'),
                entry.get('actual', ''),
                entry.get('confidence', 0.0),
                entry.get('b_ratio'),
                entry.get('p_ratio'),
                entry.get('is_forced', False),
                entry.get('strategy_name'),
                entry.get('current_interval', 0),
                entry.get('has_prediction', False),
                entry.get('validated', False),
                entry.get('skipped', False),
                entry.get('is_correct')
            ))
            
            # 첫 번째 예측 정보 추적
            if first_prediction_info is None and entry.get('has_prediction'):
                first_prediction_info = entry
            
            # 첫 번째 성공/실패 정보 추적
            if entry.get('validated') and entry.get('is_correct') is not None:
                if entry.get('is_correct') and first_success_info is None:
                    first_success_info = entry
                elif not entry.get('is_correct') and first_failure_info is None:
                    first_failure_info = entry
            
            # 첫 번째 강제 예측 정보 추적
            if first_forced_info is None and entry.get('is_forced'):
                first_forced_info = entry
        
        # 3. 첫 번째 예측 결과 저장
        if first_prediction_info:
            cursor.execute('''
                INSERT INTO live_game_first_predictions (
                    session_id, first_prediction_step, first_prediction_prefix,
                    first_prediction_value, first_prediction_confidence,
                    first_prediction_is_forced, first_prediction_actual_value,
                    first_prediction_is_correct,
                    first_success_step, first_success_prefix,
                    first_failure_step, first_failure_prefix,
                    first_forced_step, first_forced_prefix
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                session_id,
                first_prediction_info.get('step'),
                first_prediction_info.get('prefix'),
                first_prediction_info.get('predicted'),
                first_prediction_info.get('confidence'),
                first_prediction_info.get('is_forced', False),
                first_prediction_info.get('actual'),
                first_prediction_info.get('is_correct'),
                first_success_info.get('step') if first_success_info else None,
                first_success_info.get('prefix') if first_success_info else None,
                first_failure_info.get('step') if first_failure_info else None,
                first_failure_info.get('prefix') if first_failure_info else None,
                first_forced_info.get('step') if first_forced_info else None,
                first_forced_info.get('prefix') if first_forced_info else None
            ))
        
        conn.commit()
        return session_id
        
    except Exception as e:
        conn.rollback()
        st.error(f"게임 세션 저장 오류: {str(e)}")
        import traceback
        st.error(f"상세 오류: {traceback.format_exc()}")
        return None
    finally:
        conn.close()

def render_live_game_play(game_state):
    """
    라이브 게임 진행 UI 렌더링
    게임 상태가 있을 때만 호출되어야 함
    """
    # 자동 실행 완료 메시지 제거 (성능 개선)
    
    # 현재 스텝 정보
    st.markdown("---")
    st.markdown("### 📍 현재 스텝")
    
    # 예측 수행
    current_prefix = game_state['current_prefix']
    current_interval = game_state['current_interval']
    model = game_state['model']
    
    if game_state['use_threshold']:
        prediction_result = predict_with_fallback_interval(
            model,
            current_prefix,
            method=game_state['method'],
            threshold=game_state['threshold'],
            max_interval=game_state['max_interval'],
            current_interval=current_interval
        )
    else:
        prediction_result = predict_for_prefix(model, current_prefix, game_state['method'])
        if 'is_forced' not in prediction_result:
            prediction_result['is_forced'] = False
    
    predicted_value = prediction_result.get('predicted')
    confidence = prediction_result.get('confidence', 0.0)
    is_forced = prediction_result.get('is_forced', False)
    has_prediction = predicted_value is not None
    ratios = prediction_result.get('ratios', {})
    strategy_name = prediction_result.get('strategy_name', '')
    b_ratio = ratios.get('b', 0.0) if ratios else 0.0
    p_ratio = ratios.get('p', 0.0) if ratios else 0.0
    
    # 스킵 규칙 체크
    # 신뢰도가 임계값 미만일 때만 스킵 (예: 임계값 52이면 51.9 이하만 스킵, 52.0은 실행)
    # 반올림된 값으로 비교하여 표시와 동작의 일관성 보장 (소수점 1자리)
    should_skip = False
    if game_state['use_threshold'] and has_prediction and is_forced:
        # 소수점 1자리로 반올림하여 비교 (표시와 일치하도록)
        rounded_confidence = round(confidence, 1)
        rounded_threshold = round(game_state['confidence_skip_threshold'], 1)
        if rounded_confidence < rounded_threshold:
            should_skip = True
    
    # 현재 스텝 정보 표시 (컴팩트하게)
    col_info1, col_info2, col_info3, col_info4 = st.columns(4)
    with col_info1:
        st.caption("Prefix")
        st.markdown(f"<div style='font-size: 24px; font-weight: bold;'>{current_prefix}</div>", unsafe_allow_html=True)
    with col_info2:
        if has_prediction:
            forced_mark = "⚡" if is_forced else ""
            skip_mark = "⏭️" if should_skip else ""
            st.caption("예측값")
            st.text(f"{predicted_value}{forced_mark}{skip_mark}")
        else:
            st.caption("예측값")
            st.text("없음")
    with col_info3:
        if has_prediction:
            st.caption("신뢰도")
            st.text(f"{confidence:.1f}%")
        else:
            st.caption("신뢰도")
            st.text("-")
    with col_info4:
        st.caption("간격")
        st.text(f"{current_interval}/{game_state['max_interval']}")
    
    # 다음 스텝 경로 미리보기
    st.markdown("---")
    st.markdown('<p style="font-size: 1.2em; color: #666; margin-top: -10px;"><strong>다음 스텝 경로 미리보기:</strong></p>', unsafe_allow_html=True)
    
    # 다음 prefix 생성 (b와 p 두 경우 모두)
    next_prefix_b = get_next_prefix(current_prefix, 'b', game_state['window_size'])
    next_prefix_p = get_next_prefix(current_prefix, 'p', game_state['window_size'])
    
    # 다음 prefix에 대한 예측 (모델이 있는 경우)
    if model is not None:
        next_pred_b = None
        next_pred_p = None
        next_conf_b = 0.0
        next_conf_p = 0.0
        next_forced_b = False
        next_forced_p = False
        
        try:
            if game_state['use_threshold']:
                # 다음 스텝 예측용 간격 계산
                # 현재 스텝에서 검증된 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                # 현재 스텝에서 예측이 없었거나 스킵되었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                if has_prediction and not should_skip:
                    # 현재 스텝에서 검증된 예측이 있었으면, 다음 스텝으로 넘어가면 간격이 0으로 리셋
                    next_interval = 0
                else:
                    # 현재 스텝에서 예측이 없었거나 스킵되었으면, 다음 스텝으로 넘어가면 간격이 1 증가
                    next_interval = current_interval + 1
                
                # 간격을 고려하여 예측
                next_result_b = predict_with_fallback_interval(
                    model,
                    next_prefix_b,
                    game_state['method'],
                    threshold=game_state['threshold'],
                    max_interval=game_state['max_interval'],
                    current_interval=next_interval
                )
                next_result_p = predict_with_fallback_interval(
                    model,
                    next_prefix_p,
                    game_state['method'],
                    threshold=game_state['threshold'],
                    max_interval=game_state['max_interval'],
                    current_interval=next_interval
                )
                
                # 결과가 None이 아닌 경우에만 처리
                if next_result_b is not None:
                    next_forced_b = next_result_b.get('is_forced', False)
                    next_pred_b = next_result_b.get('predicted')
                    next_conf_b = next_result_b.get('confidence', 0.0)
                else:
                    next_forced_b = False
                    next_pred_b = None
                    next_conf_b = 0.0
                
                if next_result_p is not None:
                    next_forced_p = next_result_p.get('is_forced', False)
                    next_pred_p = next_result_p.get('predicted')
                    next_conf_p = next_result_p.get('confidence', 0.0)
                else:
                    next_forced_p = False
                    next_pred_p = None
                    next_conf_p = 0.0
            else:
                next_result_b = predict_for_prefix(model, next_prefix_b, game_state['method'])
                next_result_p = predict_for_prefix(model, next_prefix_p, game_state['method'])
                next_forced_b = False
                next_forced_p = False
                
                if next_result_b is not None:
                    next_pred_b = next_result_b.get('predicted')
                    next_conf_b = next_result_b.get('confidence', 0.0)
                else:
                    next_pred_b = None
                    next_conf_b = 0.0
                
                if next_result_p is not None:
                    next_pred_p = next_result_p.get('predicted')
                    next_conf_p = next_result_p.get('confidence', 0.0)
                else:
                    next_pred_p = None
                    next_conf_p = 0.0
        except Exception as e:
            pass
        
        # 다음 스텝 스킵 여부 계산 (반올림된 값으로 비교)
        next_skip_b = False
        next_skip_p = False
        if game_state['use_threshold']:
            rounded_threshold = round(game_state['confidence_skip_threshold'], 1)
            if next_pred_b is not None and next_forced_b:
                rounded_conf_b = round(next_conf_b, 1)
                if rounded_conf_b < rounded_threshold:
                    next_skip_b = True
            if next_pred_p is not None and next_forced_p:
                rounded_conf_p = round(next_conf_p, 1)
                if rounded_conf_p < rounded_threshold:
                    next_skip_p = True
        
        # 경로 표시
        col_path1, col_path2 = st.columns(2)
        with col_path1:
            if next_pred_b is not None and str(next_pred_b).strip() != '':
                forced_marker = " ⚡" if next_forced_b else ""
                skip_marker = " ⏭️" if next_skip_b else ""
                st.markdown(f'<p style="font-size: 1.1em; color: #333;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>{next_pred_b}{forced_marker}{skip_marker}</code> ({next_conf_b:.1f}%)</p>', unsafe_allow_html=True)
            else:
                st.markdown(f'<p style="font-size: 1.1em; color: #666;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code> → 예측: <code>-</code></p>', unsafe_allow_html=True)
        
        with col_path2:
            if next_pred_p is not None and str(next_pred_p).strip() != '':
                forced_marker = " ⚡" if next_forced_p else ""
                skip_marker = " ⏭️" if next_skip_p else ""
                st.markdown(f'<p style="font-size: 1.1em; color: #333;">실제값 <strong>p</strong> → 다음 prefix: <code>{next_prefix_p}</code> → 예측: <code>{next_pred_p}{forced_marker}{skip_marker}</code> ({next_conf_p:.1f}%)</p>', unsafe_allow_html=True)
            else:
                st.markdown(f'<p style="font-size: 1.1em; color: #666;">실제값 <strong>p</strong> → 다음 prefix: <code>{next_prefix_p}</code> → 예측: <code>-</code></p>', unsafe_allow_html=True)
    else:
        # 모델이 없는 경우 prefix만 표시
        col_path1, col_path2 = st.columns(2)
        with col_path1:
            st.markdown(f'<p style="font-size: 1.1em; color: #666;">실제값 <strong>b</strong> → 다음 prefix: <code>{next_prefix_b}</code></p>', unsafe_allow_html=True)
        with col_path2:
            st.markdown(f'<p style="font-size: 1.1em; color: #666;">실제값 <strong>p</strong> → 다음 prefix: <code>{next_prefix_p}</code></p>', unsafe_allow_html=True)
    
    # 실제값 입력 (버튼식)
    if has_prediction and not should_skip:
        st.markdown("---")
        st.markdown("#### 실제값 선택")
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn1:
            if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_b_{game_state['current_step']}"):
                actual_value = 'b'
                
                # 검증 수행
                is_correct = predicted_value == actual_value
                
                if not is_correct:
                    game_state['consecutive_failures'] += 1
                    game_state['total_failures'] += 1
                    if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                        game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                else:
                    game_state['consecutive_failures'] = 0
                
                game_state['total_predictions'] += 1
                if is_forced:
                    game_state['total_forced_predictions'] += 1
                
                # 간격 리셋
                game_state['current_interval'] = 0
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'b_ratio': b_ratio,
                    'p_ratio': p_ratio,
                    'strategy_name': strategy_name,
                    'current_interval': 0,
                    'has_prediction': True,
                    'validated': True,
                    'skipped': False
                })
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                # prefix 업데이트 (인터랙티브 모드에서는 항상 업데이트)
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn2:
            if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_p_{game_state['current_step']}"):
                actual_value = 'p'
                
                # 검증 수행
                is_correct = predicted_value == actual_value
                
                if not is_correct:
                    game_state['consecutive_failures'] += 1
                    game_state['total_failures'] += 1
                    if game_state['consecutive_failures'] > game_state['max_consecutive_failures']:
                        game_state['max_consecutive_failures'] = game_state['consecutive_failures']
                else:
                    game_state['consecutive_failures'] = 0
                
                game_state['total_predictions'] += 1
                if is_forced:
                    game_state['total_forced_predictions'] += 1
                
                # 간격 리셋
                game_state['current_interval'] = 0
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': is_correct,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'b_ratio': b_ratio,
                    'p_ratio': p_ratio,
                    'strategy_name': strategy_name,
                    'current_interval': 0,
                    'has_prediction': True,
                    'validated': True,
                    'skipped': False
                })
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                # prefix 업데이트 (인터랙티브 모드에서는 항상 업데이트)
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn3:
            if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_cancel_{game_state['current_step']}", disabled=len(game_state['history']) == 0):
                if len(game_state['history']) > 0:
                    # 마지막 히스토리 항목 제거
                    last_entry = game_state['history'].pop()
                    
                    # 이전 prefix로 복원
                    st.session_state.live_game_state['current_prefix'] = last_entry['prefix']
                    
                    # 스텝 번호 감소
                    st.session_state.live_game_state['current_step'] = game_state['current_step'] - 1
                    st.session_state.live_game_state['current_index'] = game_state['current_index'] - 1
                    
                    # 통계 복원
                    if last_entry.get('validated', False) and last_entry.get('is_correct') is not None:
                        # 검증된 항목이었으면 통계 복원
                        st.session_state.live_game_state['total_predictions'] = max(0, game_state['total_predictions'] - 1)
                        if last_entry.get('is_correct') is False:
                            st.session_state.live_game_state['total_failures'] = max(0, game_state['total_failures'] - 1)
                            st.session_state.live_game_state['consecutive_failures'] = max(0, game_state['consecutive_failures'] - 1)
                        else:
                            # 정답이었으면 consecutive_failures는 0이어야 함
                            st.session_state.live_game_state['consecutive_failures'] = 0
                        if last_entry.get('is_forced', False):
                            st.session_state.live_game_state['total_forced_predictions'] = max(0, game_state['total_forced_predictions'] - 1)
                    elif last_entry.get('skipped', False):
                        st.session_state.live_game_state['total_skipped_predictions'] = max(0, game_state.get('total_skipped_predictions', 0) - 1)
                    
                    # 간격 복원: history를 역순으로 순회하여 마지막 예측이 있었던 스텝을 찾고, 그 이후의 예측 없음 스텝 수를 계산
                    interval = 0
                    for entry in reversed(game_state['history']):
                        if entry.get('has_prediction', False):
                            # 예측이 있었던 스텝을 찾으면 중단
                            break
                        interval += 1
                    st.session_state.live_game_state['current_interval'] = interval
                    
                    st.rerun()
                else:
                    st.error("⚠️ 취소할 이전 상태가 없습니다.")
    elif has_prediction and should_skip:
        # 스킵 상태
        st.markdown("---")
        st.markdown("#### 실제값 선택 (스킵 모드)")
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn1:
            if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_skip_b_{game_state['current_step']}"):
                actual_value = 'b'
                
                # 히스토리 기록 (스킵)
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'b_ratio': b_ratio,
                    'p_ratio': p_ratio,
                    'strategy_name': strategy_name,
                    'current_interval': current_interval,
                    'has_prediction': True,
                    'validated': False,
                    'skipped': True
                })
                
                game_state['total_skipped_predictions'] += 1
                
                # 다음 스텝으로 진행 (간격은 증가하지 않음)
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn2:
            if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_skip_p_{game_state['current_step']}"):
                actual_value = 'p'
                
                # 히스토리 기록 (스킵)
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': predicted_value,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': is_forced,
                    'b_ratio': b_ratio,
                    'p_ratio': p_ratio,
                    'strategy_name': strategy_name,
                    'current_interval': current_interval,
                    'has_prediction': True,
                    'validated': False,
                    'skipped': True
                })
                
                game_state['total_skipped_predictions'] += 1
                
                # 다음 스텝으로 진행 (간격은 증가하지 않음)
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn3:
            if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_skip_cancel_{game_state['current_step']}", disabled=len(game_state['history']) == 0):
                if len(game_state['history']) > 0:
                    # 마지막 히스토리 항목 제거
                    last_entry = game_state['history'].pop()
                    
                    # 이전 prefix로 복원
                    st.session_state.live_game_state['current_prefix'] = last_entry['prefix']
                    
                    # 스텝 번호 감소
                    st.session_state.live_game_state['current_step'] = game_state['current_step'] - 1
                    st.session_state.live_game_state['current_index'] = game_state['current_index'] - 1
                    
                    # 통계 복원
                    if last_entry.get('skipped', False):
                        st.session_state.live_game_state['total_skipped_predictions'] = max(0, game_state.get('total_skipped_predictions', 0) - 1)
                    
                    # 간격 복원: history를 역순으로 순회하여 마지막 예측이 있었던 스텝을 찾고, 그 이후의 예측 없음 스텝 수를 계산
                    interval = 0
                    for entry in reversed(game_state['history']):
                        if entry.get('has_prediction', False):
                            # 예측이 있었던 스텝을 찾으면 중단
                            break
                        interval += 1
                    st.session_state.live_game_state['current_interval'] = interval
                    
                    st.rerun()
                else:
                    st.error("⚠️ 취소할 이전 상태가 없습니다.")
    else:
        # 예측값이 없음
        st.markdown("---")
        st.markdown("#### 실제값 선택 (예측값 없음)")
        
        col_btn1, col_btn2, col_btn3 = st.columns([1, 1, 1])
        with col_btn1:
            if st.button("🔴 B", use_container_width=True, key=f"live_game_btn_no_pred_b_{game_state['current_step']}"):
                actual_value = 'b'
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': False,
                    'b_ratio': None,
                    'p_ratio': None,
                    'strategy_name': None,
                    'current_interval': current_interval,
                    'has_prediction': False,
                    'validated': False,
                    'skipped': False
                })
                
                # 간격 증가
                game_state['current_interval'] += 1
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn2:
            if st.button("🔵 P", use_container_width=True, key=f"live_game_btn_no_pred_p_{game_state['current_step']}"):
                actual_value = 'p'
                
                # 히스토리 기록
                game_state['history'].append({
                    'step': game_state['current_step'] + 1,
                    'prefix': current_prefix,
                    'predicted': None,
                    'actual': actual_value,
                    'is_correct': None,
                    'confidence': confidence,
                    'is_forced': False,
                    'b_ratio': None,
                    'p_ratio': None,
                    'strategy_name': None,
                    'current_interval': current_interval,
                    'has_prediction': False,
                    'validated': False,
                    'skipped': False
                })
                
                # 간격 증가
                game_state['current_interval'] += 1
                
                # 다음 스텝으로 진행
                game_state['current_step'] += 1
                game_state['current_index'] += 1
                game_state['current_prefix'] = get_next_prefix(
                    current_prefix,
                    actual_value,
                    game_state['window_size']
                )
                
                st.rerun()
        
        with col_btn3:
            if st.button("↩️ 취소", use_container_width=True, key=f"live_game_btn_no_pred_cancel_{game_state['current_step']}", disabled=len(game_state['history']) == 0):
                if len(game_state['history']) > 0:
                    # 마지막 히스토리 항목 제거
                    last_entry = game_state['history'].pop()
                    
                    # 이전 prefix로 복원
                    st.session_state.live_game_state['current_prefix'] = last_entry['prefix']
                    
                    # 스텝 번호 감소
                    st.session_state.live_game_state['current_step'] = game_state['current_step'] - 1
                    st.session_state.live_game_state['current_index'] = game_state['current_index'] - 1
                    
                    # 간격 복원: history를 역순으로 순회하여 마지막 예측이 있었던 스텝을 찾고, 그 이후의 예측 없음 스텝 수를 계산
                    interval = 0
                    for entry in reversed(game_state['history']):
                        if entry.get('has_prediction', False):
                            # 예측이 있었던 스텝을 찾으면 중단
                            break
                        interval += 1
                    st.session_state.live_game_state['current_interval'] = interval
                    
                    st.rerun()
                else:
                    st.error("⚠️ 취소할 이전 상태가 없습니다.")
    
    # 상세 히스토리 표시
    if len(game_state['history']) > 0:
        st.markdown("---")
        with st.expander("📊 상세 히스토리", expanded=True):
            history_data = []
            history_sorted = sorted(game_state['history'], key=lambda x: x.get('step', 0), reverse=True)
            
            for entry in history_sorted:  # 전체 히스토리 표시
                is_correct = entry.get('is_correct')
                match_status = '✅' if is_correct else ('❌' if is_correct is False else '-')
                has_prediction = entry.get('has_prediction', False)
                is_forced = entry.get('is_forced', False)
                validated = entry.get('validated', False)
                skipped = entry.get('skipped', False)
                
                forced_mark = '⚡' if is_forced else ''
                skipped_mark = '⏭️' if skipped else ''
                validated_mark = '✓' if validated else ''
                
                history_data.append({
                    'Step': entry.get('step', 0),
                    'Prefix': entry.get('prefix', ''),
                    '예측': f"{entry.get('predicted', '-')}{forced_mark}{skipped_mark}",
                    '실제값': entry.get('actual', '-'),
                    '일치': match_status,
                    '검증': validated_mark,
                    '신뢰도': f"{entry.get('confidence', 0):.1f}%" if has_prediction else '-',
                    '간격': entry.get('current_interval', 0) if not has_prediction else 0
                })
            
            if len(history_data) > 0:
                history_df = pd.DataFrame(history_data)
                st.dataframe(history_df, use_container_width=True, hide_index=True)
                st.caption(f"💡 전체 {len(game_state['history'])}개 히스토리가 표시됩니다.")
    
    # 디버깅 정보 표시
    st.markdown("---")
    with st.expander("🔍 디버깅 정보", expanded=False):
        col_debug1, col_debug2 = st.columns(2)
        
        with col_debug1:
            st.markdown("**현재 게임 상태**")
            st.json({
                'current_step': game_state['current_step'],
                'current_index': game_state['current_index'],
                'current_prefix': game_state['current_prefix'],
                'current_interval': game_state['current_interval'],
                'history_count': len(game_state['history']),
                'total_predictions': game_state['total_predictions'],
                'total_failures': game_state['total_failures'],
                'total_skipped_predictions': game_state.get('total_skipped_predictions', 0)
            })
            
            if len(game_state['history']) > 0:
                st.markdown("**최신 히스토리 (마지막 3개)**")
                for entry in game_state['history'][-3:]:
                    st.text(f"Step {entry.get('step', 0)}: {entry.get('prefix', '')} → 예측:{entry.get('predicted', '-')} 실제:{entry.get('actual', '-')} 검증:{entry.get('validated', False)}")
        
        with col_debug2:
            st.markdown("**히스토리 정보**")
            st.json({
                'history_count': len(game_state['history']),
                'can_cancel': len(game_state['history']) > 0
            })
            
            if len(game_state['history']) > 0:
                st.markdown("**마지막 히스토리 항목 (취소 시 제거될 항목)**")
                last_entry = game_state['history'][-1]
                st.json({
                    'step': last_entry.get('step', 'N/A'),
                    'prefix': last_entry.get('prefix', 'N/A'),
                    'predicted': last_entry.get('predicted', 'N/A'),
                    'actual': last_entry.get('actual', 'N/A'),
                    'validated': last_entry.get('validated', False),
                    'skipped': last_entry.get('skipped', False)
                })
            
            st.markdown("**상태 동기화 확인**")
            if 'live_game_state' in st.session_state:
                session_state = st.session_state.live_game_state
                is_synced = (
                    session_state.get('current_step') == game_state['current_step'] and
                    session_state.get('current_index') == game_state['current_index'] and
                    len(session_state.get('history', [])) == len(game_state['history'])
                )
                if is_synced:
                    st.success("✅ game_state와 session_state 동기화됨")
                else:
                    st.error("❌ game_state와 session_state 불일치!")
                    st.text(f"Session Step: {session_state.get('current_step')} vs Game Step: {game_state['current_step']}")
                    st.text(f"Session Index: {session_state.get('current_index')} vs Game Index: {game_state['current_index']}")
                    st.text(f"Session History: {len(session_state.get('history', []))} vs Game History: {len(game_state['history'])}")
    
    # 저장 버튼 (게임 진행 중에도 저장 가능)
    st.markdown("---")
    col_save1, col_save2 = st.columns([1, 4])
    with col_save1:
        if st.button("💾 게임 결과 저장", type="primary", use_container_width=True):
            session_id = save_live_game_session(game_state)
            if session_id:
                st.success(f"✅ 게임 결과가 저장되었습니다. (Session ID: {session_id})")
            else:
                st.error("❌ 저장 중 오류가 발생했습니다.")
    
    # 게임 완료 체크 (메시지 제거 - 성능 개선)
    if game_state['current_index'] >= len(game_state['grid_string']):
        st.markdown("---")
        
        accuracy = ((game_state['total_predictions'] - game_state['total_failures']) / game_state['total_predictions'] * 100) if game_state['total_predictions'] > 0 else 0.0
        
        col_final1, col_final2, col_final3, col_final4 = st.columns(4)
        with col_final1:
            st.metric("총 스텝", game_state['current_step'])
        with col_final2:
            st.metric("총 예측", game_state['total_predictions'])
        with col_final3:
            st.metric("최대 연속 실패", game_state['max_consecutive_failures'])
        with col_final4:
            st.metric("정확도", f"{accuracy:.2f}%")

def main():
    st.title("🎮 신뢰도 스킵 전략 라이브 게임")
    st.markdown("**스텝별로 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임**")
    
    # 게임 설정 초기화
    if 'live_game_settings' not in st.session_state:
        st.session_state.live_game_settings = None
    
    # 시뮬레이션 세션 불러오기 기능 (다차원 최적화 결과만)
    from optimal_threshold_finder_app_parallel import load_simulation_sessions, load_simulation_session
    
    st.markdown("---")
    st.markdown("### 📥 시뮬레이션 세션 불러오기 (권장)")
    
    col_refresh1, col_refresh2 = st.columns([3, 1])
    with col_refresh1:
        st.markdown("시뮬레이션에서 저장한 결과를 불러와 자동으로 설정을 적용합니다.")
    with col_refresh2:
        if st.button("🔄 새로고침", use_container_width=True, key="refresh_simulation_sessions"):
            # 세션 상태 초기화하여 새로고침
            if 'live_game_simulation_session_select' in st.session_state:
                del st.session_state.live_game_simulation_session_select
            st.rerun()
    
    # 저장된 다차원 최적화 시뮬레이션 세션 목록 로드
    simulation_sessions_df = load_simulation_sessions()
    
    # 다차원 최적화 결과만 필터링
    if len(simulation_sessions_df) > 0:
        # search_method가 'multi_dimensional'인 것만 필터링
        if 'search_method' in simulation_sessions_df.columns:
            multi_dimensional_df = simulation_sessions_df[simulation_sessions_df['search_method'] == 'multi_dimensional']
        else:
            # search_method 컬럼이 없으면 빈 DataFrame
            multi_dimensional_df = pd.DataFrame()
    else:
        multi_dimensional_df = pd.DataFrame()
    
    if len(multi_dimensional_df) > 0:
        # 세션 선택
        session_options = []
        for _, row in multi_dimensional_df.iterrows():
            optimal_info = ""
            if pd.notna(row.get('optimal_threshold')):
                optimal_info = f" | 최적 임계값: {row['optimal_threshold']:.1f}%"
            
            # 다차원 최적화는 window_size 범위를 표시
            window_info = f"{row.get('window_size', 'N/A')}윈도우"
            if pd.notna(row.get('window_size_min')) and pd.notna(row.get('window_size_max')):
                if row['window_size_min'] != row['window_size_max']:
                    window_info = f"{int(row['window_size_min'])}-{int(row['window_size_max'])}윈도우"
            
            display_text = f"ID {row['validation_id'][:8]}... | Cutoff: {row['cutoff_grid_string_id']} | {window_info} | {row['method']}{optimal_info} | {row['created_at']}"
            session_options.append((row['validation_id'], display_text))
        
        selected_session_id = st.selectbox(
            "시뮬레이션 세션 선택",
            options=[None] + [opt[0] for opt in session_options],
            format_func=lambda x: "선택 안 함" if x is None else next((opt[1] for opt in session_options if opt[0] == x), x),
            key="live_game_simulation_session_select",
            help="시뮬레이션에서 저장한 세션을 선택하면 모든 설정이 자동으로 적용됩니다."
        )
        
        if selected_session_id:
            session_info = load_simulation_session(selected_session_id)
            if session_info:
                st.success(f"✅ 다차원 최적화 시뮬레이션 세션 불러오기 성공!")
                
                # 다차원 최적화 결과에서 최적 조합 정보 가져오기
                conn = get_db_connection()
                optimal_combo = None
                if conn is not None:
                    try:
                        # 최적 조합 찾기 (max_consecutive_failures <= 5 중 가장 좋은 것)
                        optimal_query = """
                            SELECT 
                                confidence_skip_threshold,
                                window_size,
                                max_interval,
                                max_consecutive_failures,
                                below_5_ratio,
                                avg_accuracy,
                                total_skipped_predictions
                            FROM optimal_threshold_simulation_results
                            WHERE validation_id = ?
                            ORDER BY max_consecutive_failures ASC, total_skipped_predictions ASC, avg_accuracy DESC
                            LIMIT 1
                        """
                        optimal_df = pd.read_sql_query(optimal_query, conn, params=[selected_session_id])
                        if len(optimal_df) > 0:
                            optimal_combo = optimal_df.iloc[0].to_dict()
                    except Exception as e:
                        pass
                    finally:
                        conn.close()
                
                col_info1, col_info2 = st.columns(2)
                with col_info1:
                    st.markdown(f"""
                    **학습 데이터 설정:**
                    - 기준 ID: {session_info['cutoff_grid_string_id']}
                    - 윈도우 크기 범위: {session_info.get('window_size_min', 'N/A')}-{session_info.get('window_size_max', 'N/A')}
                    - 예측 방법: {session_info['method']}
                    """)
                with col_info2:
                    st.markdown(f"""
                    **예측 전략 설정:**
                    - 임계값 전략: {'사용' if session_info['use_threshold'] else '미사용'}
                    - 임계값: {session_info.get('threshold', 'N/A')}
                    - 최대 간격 범위: {session_info.get('max_interval_min', 'N/A')}-{session_info.get('max_interval_max', 'N/A')}
                    """)
                
                # 최적 조합 정보 표시
                if optimal_combo:
                    st.markdown("---")
                    st.markdown("### 🎯 최적 조합 (다차원 최적화 결과)")
                    col_opt1, col_opt2, col_opt3, col_opt4 = st.columns(4)
                    with col_opt1:
                        st.metric("윈도우 크기", optimal_combo.get('window_size', 'N/A'))
                    with col_opt2:
                        st.metric("최대 간격", optimal_combo.get('max_interval', 'N/A'))
                    with col_opt3:
                        st.metric("스킵 임계값", f"{optimal_combo.get('confidence_skip_threshold', 0):.1f}%")
                    with col_opt4:
                        st.metric("최대 연속 실패", optimal_combo.get('max_consecutive_failures', 'N/A'))
                    
                    st.markdown(f"""
                    **성능 지표:**
                    - 5 이하 비율: {optimal_combo.get('below_5_ratio', 0):.1f}%
                    - 평균 정확도: {optimal_combo.get('avg_accuracy', 0):.1f}%
                    - 총 스킵 예측: {optimal_combo.get('total_skipped_predictions', 0)}
                    """)
                
                # 불러오기 버튼
                if st.button("⚙️ 최적 조합으로 게임 설정 적용", type="primary", use_container_width=True):
                    if optimal_combo:
                        # 최적 조합의 window_size와 max_interval 사용
                        optimal_window_size = optimal_combo.get('window_size')
                        optimal_max_interval = optimal_combo.get('max_interval')
                        optimal_confidence_skip = optimal_combo.get('confidence_skip_threshold', 51.5)
                    else:
                        # 최적 조합이 없으면 세션의 기본값 사용
                        optimal_window_size = session_info.get('window_size')
                        optimal_max_interval = session_info.get('max_interval')
                        optimal_confidence_skip = session_info.get('optimal_confidence_skip_threshold', 51.5)
                    
                    st.session_state.live_game_settings = {
                        'window_size': optimal_window_size if optimal_window_size else session_info.get('window_size_min', 7),
                        'method': session_info['method'],
                        'use_threshold': bool(session_info['use_threshold']),
                        'threshold': session_info.get('threshold') if session_info.get('use_threshold') else None,
                        'max_interval': optimal_max_interval if optimal_max_interval else session_info.get('max_interval_min', 4),
                        'confidence_skip_threshold': optimal_confidence_skip if optimal_confidence_skip else 51.5,
                        'cutoff_id': session_info['cutoff_grid_string_id']
                    }
                    st.session_state.live_game_cutoff_id = session_info['cutoff_grid_string_id']
                    st.session_state.live_game_simulation_validation_id = selected_session_id
                    st.success("✅ 게임 설정이 적용되었습니다!")
                    st.rerun()
                elif not optimal_combo:
                    st.warning("⚠️ 최적 조합 정보를 찾을 수 없습니다. 세션 정보만 사용합니다.")
    else:
        st.info("💡 저장된 다차원 최적화 시뮬레이션 세션이 없습니다. 먼저 optimal_threshold_finder_app_parallel에서 다차원 최적화 시뮬레이션을 실행하고 결과를 저장하세요.")
    
    # 게임 설정
    with st.expander("⚙️ 게임 설정 (수동 설정)", expanded=False):
        st.markdown("### 설정값")
        st.caption("💡 시뮬레이션 세션을 불러오면 이 설정은 자동으로 채워집니다.")
        
        # 시뮬레이션 세션에서 불러온 설정이 있으면 기본값으로 사용
        if st.session_state.live_game_settings and 'cutoff_id' in st.session_state.live_game_settings:
            default_window_size = int(st.session_state.live_game_settings['window_size']) if st.session_state.live_game_settings.get('window_size') is not None else 7
            default_method = st.session_state.live_game_settings['method']
            default_use_threshold = st.session_state.live_game_settings['use_threshold']
            default_threshold = st.session_state.live_game_settings.get('threshold', 56)
            default_max_interval = int(st.session_state.live_game_settings['max_interval']) if st.session_state.live_game_settings.get('max_interval') is not None else 4
            default_confidence_skip_threshold = float(st.session_state.live_game_settings.get('confidence_skip_threshold', 51.5))
            
            # 인덱스 계산
            window_size_options = [5, 6, 7, 8, 9]
            method_options = ["빈도 기반", "가중치 기반", "안전 우선"]
            window_size_index = window_size_options.index(default_window_size) if default_window_size in window_size_options else 0
            method_index = method_options.index(default_method) if default_method in method_options else 0
        else:
            default_window_size = 7
            default_method = "빈도 기반"
            default_use_threshold = True
            default_threshold = 56
            default_max_interval = 4
            default_confidence_skip_threshold = 51.5
            window_size_index = 2  # 7의 인덱스
            method_index = 0
        
        col_game1, col_game2 = st.columns(2)
        
        with col_game1:
            live_window_size = st.selectbox(
                "윈도우 크기",
                options=[5, 6, 7, 8, 9],
                index=window_size_index,
                key="live_game_window_size"
            )
            
            live_method = st.selectbox(
                "예측 방법",
                options=["빈도 기반", "가중치 기반", "안전 우선"],
                index=method_index,
                key="live_game_method"
            )
        
        with col_game2:
            live_use_threshold = st.checkbox(
                "임계값 전략 사용",
                value=default_use_threshold,
                key="live_game_use_threshold"
            )
            
            live_threshold = st.number_input(
                "임계값 (%)",
                min_value=0,
                max_value=100,
                value=int(default_threshold) if default_threshold else 56,
                step=1,
                key="live_game_threshold",
                disabled=not live_use_threshold
            )
            
            live_max_interval = st.number_input(
                "최대 간격",
                min_value=1,
                max_value=20,
                value=int(default_max_interval) if default_max_interval is not None else 4,
                step=1,
                key="live_game_max_interval"
            )
            
            live_confidence_skip_threshold = st.number_input(
                "신뢰도 스킵 임계값 (%)",
                min_value=0.0,
                max_value=100.0,
                value=default_confidence_skip_threshold,
                step=0.1,
                key="live_game_confidence_skip_threshold",
                help="임계값 미만일 때만 스킵합니다. 예: 50.9를 설정하면 50.9 미만만 스킵하고, 50.9 이상은 게임을 실행합니다. (0.1 단위로 설정 가능: 50.9, 51.9, 52.9...)"
            )
        
        # 기준 Grid String ID 선택 (학습 데이터 범위 지정)
        st.markdown("---")
        st.markdown("### 학습 데이터 범위 설정")
        df_all_strings = load_preprocessed_data()
        if len(df_all_strings) > 0:
            grid_string_options = []
            for _, row in df_all_strings.iterrows():
                grid_string_options.append((row['id'], row['created_at']))
            
            grid_string_options.sort(key=lambda x: x[0], reverse=True)
            
            # 시뮬레이션 세션에서 불러온 cutoff_id가 있으면 기본값으로 사용
            current_cutoff = st.session_state.get('live_game_cutoff_id', None)
            if current_cutoff is None and st.session_state.live_game_settings and 'cutoff_id' in st.session_state.live_game_settings:
                current_cutoff = st.session_state.live_game_settings['cutoff_id']
            
            default_index = 0
            if current_cutoff is not None:
                option_ids = [None] + [opt[0] for opt in grid_string_options]
                if current_cutoff in option_ids:
                    default_index = option_ids.index(current_cutoff)
            
            live_cutoff_id = st.selectbox(
                "기준 Grid String ID (이 ID 이하를 학습 데이터로 사용)",
                options=[None] + [opt[0] for opt in grid_string_options],
                format_func=lambda x: "전체 데이터" if x is None else next((f"ID {opt[0]} - {opt[1]}" for opt in grid_string_options if opt[0] == x), f"ID {x} 이하"),
                index=default_index,
                key="live_game_cutoff_id_select",
                help="시뮬레이션과 동일한 학습 데이터를 사용하려면 동일한 cutoff_id를 선택하세요."
            )
            
            if live_cutoff_id is not None:
                selected_info = df_all_strings[df_all_strings['id'] == live_cutoff_id].iloc[0]
                st.info(f"선택된 기준: ID {live_cutoff_id} (길이: {selected_info['string_length']}, 생성일: {selected_info['created_at']})")
                
                # 이후 데이터 개수 확인
                conn = get_db_connection()
                if conn is not None:
                    try:
                        count_query = "SELECT COUNT(*) as count FROM preprocessed_grid_strings WHERE id > ?"
                        count_df = pd.read_sql_query(count_query, conn, params=[live_cutoff_id])
                        after_count = count_df.iloc[0]['count']
                        st.caption(f"검증 대상: {after_count}개의 grid_string (이 ID 이후)")
                    except:
                        pass
                    finally:
                        conn.close()
        else:
            live_cutoff_id = None
            st.warning("⚠️ 저장된 grid_string이 없습니다.")
        
        # 설정 저장 버튼
        col_save1, col_save2 = st.columns([1, 4])
        with col_save1:
                if st.button("💾 설정 저장", type="primary", use_container_width=True):
                    st.session_state.live_game_settings = {
                        'window_size': live_window_size,
                        'method': live_method,
                        'use_threshold': live_use_threshold,
                        'threshold': live_threshold,
                        'max_interval': live_max_interval,
                        'confidence_skip_threshold': live_confidence_skip_threshold,
                        'cutoff_id': live_cutoff_id
                    }
                    st.session_state.live_game_cutoff_id = live_cutoff_id
                    st.rerun()
        
        with col_save2:
            pass  # 메시지 제거
    
    # Grid String 입력 섹션
    st.markdown("---")
    st.markdown("### Grid String 입력")
    live_grid_string = st.text_area(
        "Grid String",
        value="",
        height=80,
        key="live_game_grid_string",
        help="라이브 게임에서 사용할 grid_string을 입력하세요. 이 grid_string이 DB에 있으면 학습 데이터에서 자동으로 제외됩니다.",
        disabled=st.session_state.live_game_settings is None
    )
    
    if st.session_state.live_game_settings is None:
        st.warning("⚠️ 먼저 게임 설정을 저장해주세요.")
    
    # 게임 초기화
    if 'live_game_state' not in st.session_state:
        st.session_state.live_game_state = None
    
    # 게임 시작/재시작 버튼
    col_start1, col_start2 = st.columns([1, 4])
    with col_start1:
        if st.button("🎮 게임 시작", type="primary", use_container_width=True):
            if st.session_state.live_game_settings is None:
                st.error("게임 설정을 먼저 저장해주세요.")
            elif not live_grid_string or not live_grid_string.strip():
                st.error("Grid String을 입력해주세요.")
            else:
                grid_string = live_grid_string.strip()
                settings = st.session_state.live_game_settings
                
                if len(grid_string) < settings['window_size']:
                    st.error(f"Grid String이 너무 짧습니다. (길이: {len(grid_string)}, 최소 필요: {settings['window_size']})")
                else:
                    # 게임 초기화
                    conn = get_db_connection()
                    if conn is None:
                        st.error("데이터베이스 연결 실패")
                    else:
                        try:
                                # 입력한 grid_string이 DB에 있는지 확인
                                check_query = "SELECT id FROM preprocessed_grid_strings WHERE grid_string = ?"
                                check_df = pd.read_sql_query(check_query, conn, params=[grid_string])
                                existing_grid_string_id = check_df.iloc[0]['id'] if len(check_df) > 0 else None
                                
                                # cutoff_id 가져오기
                                cutoff_id = settings.get('cutoff_id')
                                
                                # 모델 캐싱 키 생성 (cutoff_id 포함)
                                if cutoff_id is not None:
                                    model_cache_key = f"live_game_model_{settings['window_size']}_{settings['method']}_cutoff_{cutoff_id}"
                                else:
                                    model_cache_key = f"live_game_model_{settings['window_size']}_{settings['method']}_all"
                                
                                # 입력한 grid_string이 DB에 있으면 캐시 키에 포함
                                if existing_grid_string_id is not None:
                                    model_cache_key += f"_exclude_{existing_grid_string_id}"
                                
                                if model_cache_key in st.session_state:
                                    # 캐시된 모델 재사용
                                    model = st.session_state[model_cache_key]
                                else:
                                    # 학습 데이터 구축
                                    if cutoff_id is not None:
                                        # cutoff_id 이하의 데이터만 사용
                                        if existing_grid_string_id is not None and existing_grid_string_id <= cutoff_id:
                                            # 입력한 grid_string이 cutoff_id 이하에 있으면 학습 데이터에서 제외
                                            train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? AND id < ? ORDER BY id"
                                            train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_id, existing_grid_string_id])
                                        else:
                                            # 입력한 grid_string이 cutoff_id 초과에 있거나 없으면 cutoff_id 이하만 사용
                                            train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
                                            train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[cutoff_id])
                                    else:
                                        # cutoff_id가 없으면 모든 데이터 사용 (입력한 grid_string 제외)
                                        if existing_grid_string_id is not None:
                                            train_ids_query = "SELECT id FROM preprocessed_grid_strings WHERE id < ? ORDER BY id"
                                            train_ids_df = pd.read_sql_query(train_ids_query, conn, params=[existing_grid_string_id])
                                        else:
                                            train_ids_query = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
                                            train_ids_df = pd.read_sql_query(train_ids_query, conn)
                                    
                                    train_ids = train_ids_df['id'].tolist() if len(train_ids_df) > 0 else []
                                    
                                    # N-gram 로드
                                    train_ngrams = load_ngram_chunks(window_size=settings['window_size'], grid_string_ids=train_ids)
                                    
                                    if len(train_ngrams) == 0:
                                        st.warning("⚠️ 학습 데이터가 없습니다. 빈 모델로 시작합니다.")
                                        train_ngrams = []
                                    
                                    # 모델 구축
                                    if settings['method'] == "빈도 기반":
                                        model = build_frequency_model(train_ngrams)
                                    elif settings['method'] == "가중치 기반":
                                        model = build_weighted_model(train_ngrams)
                                    else:
                                        model = build_frequency_model(train_ngrams)
                                    
                                    # 모델 캐싱
                                    st.session_state[model_cache_key] = model
                                
                                # 입력한 grid_string이 DB에 있는 경우 경고
                                if existing_grid_string_id is not None:
                                    st.info(f"💡 입력한 grid_string이 DB에 있습니다 (ID: {existing_grid_string_id}). 학습 데이터에서 제외되었습니다.")
                                
                                # 게임 상태 초기화
                                prefix_length = settings['window_size'] - 1
                                initial_prefix = grid_string[:prefix_length]
                                
                                # 입력된 grid_string 길이만큼 자동 실행
                                history = []
                                consecutive_failures = 0
                                max_consecutive_failures = 0
                                total_predictions = 0
                                total_failures = 0
                                total_forced_predictions = 0
                                total_skipped_predictions = 0
                                current_interval = 0
                                current_index = prefix_length
                                current_prefix = initial_prefix
                                current_step = 0
                                
                                # grid_string의 마지막까지 자동 실행
                                while current_index < len(grid_string):
                                        # 예측 수행
                                        if settings['use_threshold']:
                                            prediction_result = predict_with_fallback_interval(
                                                model,
                                                current_prefix,
                                                method=settings['method'],
                                                threshold=settings['threshold'],
                                                max_interval=settings['max_interval'],
                                                current_interval=current_interval
                                            )
                                        else:
                                            prediction_result = predict_for_prefix(model, current_prefix, settings['method'])
                                            if 'is_forced' not in prediction_result:
                                                prediction_result['is_forced'] = False
                                        
                                        predicted_value = prediction_result.get('predicted')
                                        confidence = prediction_result.get('confidence', 0.0)
                                        is_forced = prediction_result.get('is_forced', False)
                                        has_prediction = predicted_value is not None
                                        ratios = prediction_result.get('ratios', {})
                                        strategy_name = prediction_result.get('strategy_name', '')
                                        b_ratio = ratios.get('b', 0.0) if ratios else 0.0
                                        p_ratio = ratios.get('p', 0.0) if ratios else 0.0
                                        
                                        # 스킵 규칙 체크
                                        # 신뢰도가 임계값 미만일 때만 스킵 (예: 임계값 52이면 51.9 이하만 스킵, 52.0은 실행)
                                        # 반올림된 값으로 비교하여 표시와 동작의 일관성 보장 (소수점 1자리)
                                        should_skip = False
                                        if settings['use_threshold'] and has_prediction and is_forced:
                                            # 소수점 1자리로 반올림하여 비교 (표시와 일치하도록)
                                            rounded_confidence = round(confidence, 1)
                                            rounded_threshold = round(settings['confidence_skip_threshold'], 1)
                                            if rounded_confidence < rounded_threshold:
                                                should_skip = True
                                        
                                        if should_skip:
                                            total_skipped_predictions += 1
                                        
                                        # 실제값 가져오기 (grid_string에서)
                                        actual_value = grid_string[current_index]
                                        
                                        # 검증 수행 (예측값이 있고 스킵하지 않는 경우)
                                        if has_prediction and not should_skip:
                                            is_correct = predicted_value == actual_value
                                            
                                            if not is_correct:
                                                consecutive_failures += 1
                                                total_failures += 1
                                                if consecutive_failures > max_consecutive_failures:
                                                    max_consecutive_failures = consecutive_failures
                                            else:
                                                consecutive_failures = 0
                                            
                                            total_predictions += 1
                                            if is_forced:
                                                total_forced_predictions += 1
                                            
                                            # 간격 리셋
                                            current_interval = 0
                                            
                                            # 히스토리 기록
                                            history.append({
                                                'step': current_step + 1,
                                                'prefix': current_prefix,
                                                'predicted': predicted_value,
                                                'actual': actual_value,
                                                'is_correct': is_correct,
                                                'confidence': confidence,
                                                'is_forced': is_forced,
                                                'b_ratio': b_ratio,
                                                'p_ratio': p_ratio,
                                                'strategy_name': strategy_name,
                                                'current_interval': 0,
                                                'has_prediction': True,
                                                'validated': True,
                                                'skipped': False
                                            })
                                        elif has_prediction and should_skip:
                                            # 스킵된 경우 히스토리 기록
                                            history.append({
                                                'step': current_step + 1,
                                                'prefix': current_prefix,
                                                'predicted': predicted_value,
                                                'actual': actual_value,
                                                'is_correct': None,
                                                'confidence': confidence,
                                                'is_forced': is_forced,
                                                'b_ratio': b_ratio,
                                                'p_ratio': p_ratio,
                                                'strategy_name': strategy_name,
                                                'current_interval': current_interval,
                                                'has_prediction': True,
                                                'validated': False,
                                                'skipped': True
                                            })
                                            # 스킵 상태에서 간격 계산은 멈춤 (증가하지 않음)
                                        else:
                                            # 예측값이 없는 경우 히스토리 기록 (전체 스텝 표시를 위해)
                                            history.append({
                                                'step': current_step + 1,
                                                'prefix': current_prefix,
                                                'predicted': None,
                                                'actual': actual_value,
                                                'is_correct': None,
                                                'confidence': 0.0,
                                                'is_forced': False,
                                                'b_ratio': None,
                                                'p_ratio': None,
                                                'strategy_name': None,
                                                'current_interval': current_interval,
                                                'has_prediction': False,
                                                'validated': False,
                                                'skipped': False
                                            })
                                            # 예측값이 없는 경우 간격 증가
                                            if settings['use_threshold']:
                                                current_interval += 1
                                        
                                        # 다음 스텝으로 진행
                                        current_step += 1
                                        current_index += 1
                                        
                                        # prefix 업데이트 (인터랙티브 모드 전환을 위해 항상 업데이트)
                                        current_prefix = get_next_prefix(
                                            current_prefix,
                                            actual_value,
                                            settings['window_size']
                                        )
                                
                                # 게임 상태 저장 (다음 스텝부터 인터랙티브로 진행)
                                st.session_state.live_game_state = {
                                    'grid_string': grid_string,
                                    'model': model,
                                    'current_step': current_step,
                                    'current_index': current_index,
                                    'current_prefix': current_prefix,
                                    'current_interval': current_interval,
                                    'history': history,
                                    'consecutive_failures': consecutive_failures,
                                    'max_consecutive_failures': max_consecutive_failures,
                                    'total_predictions': total_predictions,
                                    'total_failures': total_failures,
                                    'total_forced_predictions': total_forced_predictions,
                                    'total_skipped_predictions': total_skipped_predictions,
                                    'window_size': settings['window_size'],
                                    'method': settings['method'],
                                    'use_threshold': settings['use_threshold'],
                                    'threshold': settings['threshold'],
                                    'max_interval': settings['max_interval'],
                                    'confidence_skip_threshold': settings['confidence_skip_threshold'],
                                    'auto_executed': True  # 자동 실행 완료 플래그
                                }
                                
                                st.rerun()
                        except Exception as e:
                            st.error(f"게임 초기화 중 오류: {str(e)}")
                            import traceback
                            st.error(f"상세 오류: {traceback.format_exc()}")
                        finally:
                            conn.close()
    
    with col_start2:
        if st.session_state.live_game_state is not None:
            if st.button("🔄 게임 재시작", use_container_width=True):
                st.session_state.live_game_state = None
                st.rerun()
    
    # 게임 진행 (게임 상태가 있을 때만 함수 호출 - 성능 개선)
    if st.session_state.live_game_state is not None:
        render_live_game_play(st.session_state.live_game_state)

if __name__ == "__main__":
    main()

