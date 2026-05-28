#!/usr/bin/env python3
"""
stored_predictions 테이블에 예측값 저장 (직접 실행)
"""
import sqlite3
import os
import sys

# DB 경로
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'hypothesis_validation.db')

def check_status():
    """현재 상태 확인"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # stored_predictions 확인
    cursor.execute("SELECT COUNT(*) FROM stored_predictions")
    count = cursor.fetchone()[0]
    
    # preprocessed_grid_strings 확인
    cursor.execute("SELECT COUNT(*) FROM preprocessed_grid_strings")
    grid_count = cursor.fetchone()[0]
    
    # ngram_chunks 확인
    cursor.execute("SELECT COUNT(*) FROM ngram_chunks")
    ngram_count = cursor.fetchone()[0]
    
    conn.close()
    
    print(f"preprocessed_grid_strings: {grid_count}개")
    print(f"ngram_chunks: {ngram_count}개")
    print(f"stored_predictions: {count}개")
    
    return count == 0

if __name__ == "__main__":
    print("=" * 60)
    print("예측값 저장 스크립트")
    print("=" * 60)
    
    # 상태 확인
    is_empty = check_status()
    
    if is_empty:
        print("\n⚠️  stored_predictions 테이블이 비어있습니다.")
        print("예측값을 저장하려면 hypothesis_validation_app.py의")
        print("'예측값 저장/업데이트' 섹션에서 버튼을 클릭하세요.")
        print("\n또는 다음 Python 코드를 실행하세요:")
        print("""
from hypothesis_validation_app import save_or_update_predictions_for_historical_data

result = save_or_update_predictions_for_historical_data(
    cutoff_grid_string_id=None,  # 전체 데이터 사용
    window_sizes=[5, 6, 7, 8, 9],
    methods=["빈도 기반"],
    thresholds=[0, 50, 60, 70, 80, 90, 100],
    batch_size=1000
)
print(result)
        """)
    else:
        print("\n✅ stored_predictions 테이블에 데이터가 있습니다.")
        
        # 상세 정보 출력
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("""
            SELECT window_size, method, threshold, COUNT(*) as count
            FROM stored_predictions
            GROUP BY window_size, method, threshold
            ORDER BY window_size, method, threshold
        """)
        
        print("\n저장된 예측값 분포:")
        for row in cursor.fetchall():
            print(f"  window_size={row[0]}, method={row[1]}, threshold={row[2]}: {row[3]:,}개")
        
        conn.close()


