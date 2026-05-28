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

def generate_ngrams_for_all_grid_strings(window_sizes=[10, 11, 12]):
    """
    change_point_ngram.db의 모든 grid_string에 대해 ngram_chunks_change_point 생성
    지정된 윈도우 크기만 생성 (기존에 없는 윈도우 크기만)
    
    Args:
        window_sizes: 생성할 윈도우 크기 리스트 (기본값: [10, 11, 12])
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
    print(f"윈도우 크기 {window_sizes}로 N-gram 생성합니다...")
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
        skipped_count = 0
        error_count = 0
        
        for record_id, grid_string in all_records:
            try:
                # 각 윈도우 크기별로 이미 있는지 확인
                missing_window_sizes = []
                for window_size in window_sizes:
                    cursor.execute('''
                        SELECT COUNT(*) FROM ngram_chunks_change_point 
                        WHERE grid_string_id = ? AND window_size = ?
                    ''', (record_id, window_size))
                    
                    existing_count = cursor.fetchone()[0]
                    if existing_count == 0:
                        missing_window_sizes.append(window_size)
                
                # 모든 윈도우 크기가 이미 있으면 건너뜀
                if not missing_window_sizes:
                    skipped_count += 1
                    print(f"ID {record_id}: 모든 윈도우 크기({window_sizes})가 이미 존재합니다. (건너뜀)")
                    continue
                
                # 없는 윈도우 크기만 생성
                result = generate_and_save_ngram_chunks_change_point(
                    record_id,
                    grid_string,
                    window_sizes=missing_window_sizes,
                    conn=conn
                )
                
                total_ngrams = sum(result.values())
                processed_count += 1
                
                print(f"ID {record_id}: {total_ngrams}개의 ngram 생성 완료 (윈도우 크기: {missing_window_sizes})")
                print(f"  - 윈도우별: {result}")
                
            except Exception as e:
                error_count += 1
                print(f"ID {record_id}: 오류 발생 - {str(e)}")
        
        print("-" * 60)
        print(f"처리 완료!")
        print(f"  - 성공: {processed_count}개")
        print(f"  - 건너뜀: {skipped_count}개")
        print(f"  - 오류: {error_count}개")
        
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
    
    # 윈도우 크기 10, 11, 12 생성
    generate_ngrams_for_all_grid_strings(window_sizes=[10, 11, 12])
