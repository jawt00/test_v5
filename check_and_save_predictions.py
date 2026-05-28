"""
stored_predictions 테이블 상태 확인 및 예측값 저장 스크립트
"""
import sqlite3
import os
import sys

# hypothesis_validation_app.py의 함수들을 import하기 위해 경로 추가
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Streamlit 없이 실행하기 위해 mock 설정
class MockStreamlit:
    def error(self, msg):
        print(f"[ERROR] {msg}")
    def warning(self, msg):
        print(f"[WARNING] {msg}")
    def info(self, msg):
        print(f"[INFO] {msg}")

# streamlit 모듈을 mock으로 교체
sys.modules['streamlit'] = type(sys)('streamlit')
sys.modules['streamlit'].st = MockStreamlit()

# 이제 hypothesis_validation_app을 import
from hypothesis_validation_app import (
    get_db_connection,
    save_or_update_predictions_for_historical_data,
    load_ngram_chunks
)

def check_db_status():
    """DB 상태 확인"""
    print("=" * 60)
    print("📊 데이터베이스 상태 확인")
    print("=" * 60)
    
    conn = get_db_connection()
    if conn is None:
        print("❌ 데이터베이스 연결 실패")
        return
    
    try:
        cursor = conn.cursor()
        
        # preprocessed_grid_strings 확인
        cursor.execute("SELECT COUNT(*) FROM preprocessed_grid_strings")
        grid_count = cursor.fetchone()[0]
        print(f"✅ preprocessed_grid_strings: {grid_count:,}개")
        
        # ngram_chunks 확인
        cursor.execute("SELECT window_size, COUNT(*) FROM ngram_chunks GROUP BY window_size")
        ngram_results = cursor.fetchall()
        total_ngrams = 0
        for window_size, count in ngram_results:
            print(f"✅ ngram_chunks (window_size={window_size}): {count:,}개")
            total_ngrams += count
        print(f"   총 ngram_chunks: {total_ngrams:,}개")
        
        # stored_predictions 확인
        cursor.execute("SELECT COUNT(*) FROM stored_predictions")
        pred_count = cursor.fetchone()[0]
        print(f"{'✅' if pred_count > 0 else '❌'} stored_predictions: {pred_count:,}개")
        
        if pred_count > 0:
            # 저장된 예측값 샘플 확인
            cursor.execute("""
                SELECT window_size, method, threshold, COUNT(*) as count
                FROM stored_predictions
                GROUP BY window_size, method, threshold
                ORDER BY window_size, method, threshold
            """)
            print("\n📋 저장된 예측값 분포:")
            for row in cursor.fetchall():
                print(f"   window_size={row[0]}, method={row[1]}, threshold={row[2]}: {row[3]:,}개")
        
        # grid_string_id 범위 확인
        cursor.execute("SELECT MIN(id), MAX(id) FROM preprocessed_grid_strings")
        min_id, max_id = cursor.fetchone()
        print(f"\n📌 grid_string_id 범위: {min_id} ~ {max_id}")
        
    except Exception as e:
        print(f"❌ 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

def save_predictions(cutoff_id=None, window_sizes=[5, 6, 7, 8, 9], 
                     methods=["빈도 기반"], thresholds=[0, 50, 60, 70, 80, 90, 100]):
    """예측값 저장"""
    print("\n" + "=" * 60)
    print("💾 예측값 저장 시작")
    print("=" * 60)
    
    if cutoff_id is None:
        print("📌 cutoff_id가 지정되지 않았습니다. 전체 데이터를 사용합니다.")
    else:
        print(f"📌 cutoff_id: {cutoff_id} (이 ID 이하가 학습 데이터)")
    
    print(f"📌 window_sizes: {window_sizes}")
    print(f"📌 methods: {methods}")
    print(f"📌 thresholds: {thresholds}")
    print()
    
    try:
        result = save_or_update_predictions_for_historical_data(
            cutoff_grid_string_id=cutoff_id,
            window_sizes=window_sizes,
            methods=methods,
            thresholds=thresholds,
            batch_size=1000
        )
        
        if result:
            print("\n✅ 예측값 저장 완료!")
            print(f"   총 저장/업데이트: {result['total_saved']:,}개")
            print(f"   새 레코드: {result['new_records']:,}개")
            print(f"   업데이트: {result['updated_records']:,}개")
            print(f"   고유 Prefix 수: {result['unique_prefixes']:,}개")
        else:
            print("\n❌ 예측값 저장 실패")
            
    except Exception as e:
        print(f"\n❌ 오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # 1. DB 상태 확인
    check_db_status()
    
    # 2. 예측값 저장 여부 확인
    conn = get_db_connection()
    if conn:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM stored_predictions")
        pred_count = cursor.fetchone()[0]
        conn.close()
        
        if pred_count == 0:
            print("\n" + "=" * 60)
            print("⚠️  stored_predictions 테이블이 비어있습니다.")
            print("=" * 60)
            
            # 자동으로 예측값 저장 실행 (전체 데이터 사용)
            print("\n자동으로 예측값을 저장합니다...")
            cutoff_id = None  # 전체 데이터 사용
            
            # 예측값 저장 실행
            save_predictions(cutoff_id=cutoff_id)
            
            # 저장 후 다시 확인
            print("\n" + "=" * 60)
            print("📊 저장 후 상태 확인")
            print("=" * 60)
            check_db_status()
        else:
            print(f"\n✅ stored_predictions 테이블에 {pred_count:,}개의 예측값이 이미 저장되어 있습니다.")
    else:
        print("❌ 데이터베이스 연결 실패")

