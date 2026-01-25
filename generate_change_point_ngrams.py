"""
복제된 grid_string으로 ngram_chunks_change_point 생성 스크립트
change_point_ngram.db의 preprocessed_grid_strings 테이블에서 모든 grid_string을 읽어서
ngram_chunks_change_point를 생성합니다.
"""

import sqlite3
import os
import sys

# svg_parser_module import
from svg_parser_module import (
    get_change_point_db_connection,
    create_change_point_preprocessed_grid_strings_table,
    create_change_point_ngram_chunks_table,
    generate_and_save_ngram_chunks_change_point
)

def generate_ngrams_for_all_grid_strings():
    """
    change_point_ngram.db의 모든 grid_string에 대해 ngram_chunks_change_point 생성
    """
    # 테이블 생성 확인
    print("테이블 생성 확인 중...")
    try:
        create_change_point_preprocessed_grid_strings_table()
        create_change_point_ngram_chunks_table()
        print("✅ 테이블 생성 완료")
    except Exception as e:
        print(f"⚠️ 테이블 생성 중 오류 (계속 진행): {str(e)}")
    
    print()
    
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    
    try:
        # 모든 grid_string 조회
        cursor.execute('''
            SELECT id, grid_string 
            FROM preprocessed_grid_strings 
            ORDER BY id
        ''')
        
        all_records = cursor.fetchall()
        total_count = len(all_records)
        
        print(f"총 {total_count}개의 grid_string을 처리합니다...")
        print("-" * 60)
        
        processed_count = 0
        error_count = 0
        
        for record_id, grid_string in all_records:
            try:
                # 이미 ngram_chunks_change_point가 있는지 확인
                cursor.execute('''
                    SELECT COUNT(*) FROM ngram_chunks_change_point 
                    WHERE grid_string_id = ?
                ''', (record_id,))
                
                existing_count = cursor.fetchone()[0]
                
                if existing_count > 0:
                    print(f"ID {record_id}: 이미 ngram_chunks_change_point가 존재합니다. (건너뜀)")
                    continue
                
                # ngram_chunks_change_point 생성
                result = generate_and_save_ngram_chunks_change_point(
                    record_id,
                    grid_string,
                    window_sizes=[5, 6, 7, 8, 9]
                )
                
                total_ngrams = sum(result.values())
                processed_count += 1
                
                print(f"ID {record_id}: {total_ngrams}개의 ngram 생성 완료")
                print(f"  - 윈도우별: {result}")
                
            except Exception as e:
                error_count += 1
                print(f"ID {record_id}: 오류 발생 - {str(e)}")
        
        print("-" * 60)
        print(f"처리 완료!")
        print(f"  - 성공: {processed_count}개")
        print(f"  - 오류: {error_count}개")
        print(f"  - 건너뜀: {total_count - processed_count - error_count}개")
        
    except Exception as e:
        print(f"오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("Change-point Detection 기반 N-gram 생성 스크립트")
    print("=" * 60)
    print()
    
    generate_ngrams_for_all_grid_strings()
