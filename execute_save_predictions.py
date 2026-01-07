#!/usr/bin/env python3
"""
예측값 저장 실행 스크립트
Streamlit 없이 직접 실행
"""
import sqlite3
import pandas as pd
import os
import sys
from collections import Counter, defaultdict

# DB 경로
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hypothesis_validation.db')

def get_db_connection():
    """데이터베이스 연결"""
    try:
        if not os.path.exists(DB_PATH):
            print(f"❌ 데이터베이스 파일을 찾을 수 없습니다: {DB_PATH}")
            return None
        return sqlite3.connect(DB_PATH)
    except Exception as e:
        print(f"❌ 데이터베이스 연결 오류: {str(e)}")
        return None

def load_ngram_chunks(window_size, grid_string_ids=None):
    """ngram_chunks 로드"""
    conn = get_db_connection()
    if conn is None:
        return pd.DataFrame()
    
    try:
        if grid_string_ids is None:
            query = """
                SELECT grid_string_id, prefix, suffix
                FROM ngram_chunks
                WHERE window_size = ?
            """
            params = [window_size]
        else:
            placeholders = ','.join(['?'] * len(grid_string_ids))
            query = f"""
                SELECT grid_string_id, prefix, suffix
                FROM ngram_chunks
                WHERE window_size = ? AND grid_string_id IN ({placeholders})
            """
            params = [window_size] + grid_string_ids
        
        df = pd.read_sql_query(query, conn, params=params)
        return df
    except Exception as e:
        print(f"❌ ngram_chunks 로드 오류: {str(e)}")
        return pd.DataFrame()
    finally:
        conn.close()

def build_frequency_model(ngrams_df):
    """빈도 기반 모델 구축"""
    model = defaultdict(lambda: Counter())
    for _, row in ngrams_df.iterrows():
        prefix = row['prefix']
        suffix = row['suffix']
        model[prefix][suffix] += 1
    return dict(model)

def predict_frequency(model, prefix):
    """빈도 기반 예측"""
    if prefix not in model:
        return None, {}
    
    counter = model[prefix]
    total = sum(counter.values())
    if total == 0:
        return None, {}
    
    # 가장 빈도가 높은 suffix 선택
    predicted = counter.most_common(1)[0][0]
    
    # 비율 계산
    ratios = {suffix: (count / total * 100) for suffix, count in counter.items()}
    
    return predicted, ratios

def predict_for_prefix(model, prefix, method="빈도 기반"):
    """단일 prefix에 대한 예측 수행"""
    if method == "빈도 기반":
        predicted, ratios = predict_frequency(model, prefix)
    else:
        predicted, ratios = predict_frequency(model, prefix)  # 기본값
    
    confidence = max(ratios.values()) if ratios else 0.0
    
    return {
        'predicted': predicted,
        'ratios': ratios,
        'confidence': confidence
    }

def predict_confidence_threshold(model, prefix, method="빈도 기반", threshold=60):
    """신뢰도 임계값 전략"""
    result = predict_for_prefix(model, prefix, method)
    confidence = result.get('confidence', 0.0)
    predicted = result.get('predicted')
    
    if predicted is None:
        return {
            'predicted': None,
            'ratios': result.get('ratios', {}),
            'confidence': confidence,
            'strategy_name': f'신뢰도임계값_{threshold}'
        }
    
    confidence_rounded = round(confidence, 1)
    threshold_rounded = round(threshold, 1)
    
    if confidence_rounded < threshold_rounded:
        return {
            'predicted': None,
            'ratios': result.get('ratios', {}),
            'confidence': confidence,
            'strategy_name': f'신뢰도임계값_{threshold}'
        }
    
    return {
        'predicted': result.get('predicted'),
        'ratios': result.get('ratios', {}),
        'confidence': confidence,
        'strategy_name': f'신뢰도임계값_{threshold}'
    }

def save_predictions(cutoff_grid_string_id=None, window_sizes=[5, 6, 7, 8, 9],
                     methods=["빈도 기반"], thresholds=[0, 50, 60, 70, 80, 90, 100],
                     batch_size=1000):
    """예측값 저장"""
    conn = get_db_connection()
    if conn is None:
        return None
    
    try:
        # 이전 데이터 선택
        if cutoff_grid_string_id is None:
            query = "SELECT id FROM preprocessed_grid_strings ORDER BY id"
            params = []
        else:
            query = "SELECT id FROM preprocessed_grid_strings WHERE id <= ? ORDER BY id"
            params = [cutoff_grid_string_id]
        
        df_historical = pd.read_sql_query(query, conn, params=params)
        
        if len(df_historical) == 0:
            print("❌ 학습 데이터가 없습니다.")
            return {
                'total_saved': 0,
                'new_records': 0,
                'updated_records': 0,
                'unique_prefixes': 0
            }
        
        print(f"📊 학습 데이터: {len(df_historical)}개")
        
        historical_ids = df_historical['id'].tolist()
        
        total_saved = 0
        new_records = 0
        updated_records = 0
        unique_prefixes_set = set()
        
        cursor = conn.cursor()
        
        for window_size in window_sizes:
            print(f"\n🔄 window_size={window_size} 처리 중...")
            train_ngrams = load_ngram_chunks(window_size=window_size, grid_string_ids=historical_ids)
            
            if len(train_ngrams) == 0:
                print(f"  ⚠️  ngram_chunks가 없습니다.")
                continue
            
            print(f"  📊 ngram_chunks: {len(train_ngrams):,}개")
            
            # 모델 구축
            for method in methods:
                print(f"  🔨 모델 구축 중 (method={method})...")
                if method == "빈도 기반":
                    model = build_frequency_model(train_ngrams)
                else:
                    model = build_frequency_model(train_ngrams)
                
                # 모든 가능한 prefix 추출
                all_prefixes = set(train_ngrams['prefix'].unique())
                print(f"  📋 고유 prefix: {len(all_prefixes):,}개")
                
                # 각 prefix에 대해 예측값 계산 및 저장
                batch_data = []
                processed = 0
                
                for prefix in all_prefixes:
                    unique_prefixes_set.add((window_size, prefix))
                    
                    # 각 임계값에 대해 예측값 계산
                    for threshold in thresholds:
                        if threshold == 0:
                            prediction_result = predict_for_prefix(model, prefix, method)
                        else:
                            prediction_result = predict_confidence_threshold(model, prefix, method, threshold)
                        
                        predicted = prediction_result.get('predicted')
                        confidence = prediction_result.get('confidence', 0.0)
                        ratios = prediction_result.get('ratios', {})
                        
                        b_ratio = ratios.get('b', 0.0)
                        p_ratio = ratios.get('p', 0.0)
                        
                        batch_data.append((
                            window_size,
                            prefix,
                            predicted,
                            confidence,
                            b_ratio,
                            p_ratio,
                            method,
                            threshold
                        ))
                    
                    processed += 1
                    if processed % 1000 == 0:
                        print(f"    진행: {processed:,}/{len(all_prefixes):,} prefix 처리됨")
                
                # 배치로 저장/업데이트
                if batch_data:
                    print(f"  💾 {len(batch_data):,}개 예측값 저장 중...")
                    for i in range(0, len(batch_data), batch_size):
                        batch = batch_data[i:i + batch_size]
                        
                        for item in batch:
                            try:
                                # 기존 레코드 확인
                                cursor.execute('''
                                    SELECT id FROM stored_predictions
                                    WHERE window_size = ? 
                                      AND prefix = ? 
                                      AND method = ? 
                                      AND threshold = ?
                                ''', (item[0], item[1], item[6], item[7]))
                                
                                existing = cursor.fetchone()
                                
                                cursor.execute('''
                                    INSERT OR REPLACE INTO stored_predictions (
                                        window_size, prefix,
                                        predicted_value, confidence, b_ratio, p_ratio,
                                        method, threshold, updated_at
                                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now', '+9 hours'))
                                ''', item)
                                
                                if existing:
                                    updated_records += 1
                                else:
                                    new_records += 1
                                
                                total_saved += 1
                            except Exception as e:
                                print(f"    ⚠️  저장 오류: {str(e)}")
                                continue
                
                print(f"  ✅ window_size={window_size}, method={method} 완료")
        
        conn.commit()
        
        return {
            'total_saved': total_saved,
            'new_records': new_records,
            'updated_records': updated_records,
            'unique_prefixes': len(unique_prefixes_set)
        }
        
    except Exception as e:
        conn.rollback()
        print(f"❌ 예측값 저장/업데이트 오류: {str(e)}")
        import traceback
        traceback.print_exc()
        return None
    finally:
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("예측값 저장 스크립트 실행")
    print("=" * 60)
    
    # 상태 확인
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stored_predictions")
        current_count = cursor.fetchone()[0]
        conn.close()
        
        print(f"\n현재 stored_predictions 레코드 수: {current_count:,}개")
        
        if current_count == 0:
            print("\n예측값 저장을 시작합니다...")
            print("(이 작업은 시간이 걸릴 수 있습니다)\n")
            
            result = save_predictions(
                cutoff_grid_string_id=None,  # 전체 데이터 사용
                window_sizes=[5, 6, 7, 8, 9],
                methods=["빈도 기반"],
                thresholds=[0, 50, 60, 70, 80, 90, 100],
                batch_size=1000
            )
            
            if result:
                print("\n" + "=" * 60)
                print("✅ 예측값 저장 완료!")
                print("=" * 60)
                print(f"총 저장/업데이트: {result['total_saved']:,}개")
                print(f"새 레코드: {result['new_records']:,}개")
                print(f"업데이트: {result['updated_records']:,}개")
                print(f"고유 Prefix 수: {result['unique_prefixes']:,}개")
                
                # 저장 후 확인
                conn = get_db_connection()
                if conn:
                    cursor = conn.cursor()
                    cursor.execute("SELECT COUNT(*) FROM stored_predictions")
                    new_count = cursor.fetchone()[0]
                    conn.close()
                    print(f"\n저장 후 레코드 수: {new_count:,}개")
            else:
                print("\n❌ 예측값 저장 실패")
        else:
            print(f"\n✅ 이미 {current_count:,}개의 예측값이 저장되어 있습니다.")
            print("다시 저장하려면 stored_predictions 테이블을 비우고 실행하세요.")


