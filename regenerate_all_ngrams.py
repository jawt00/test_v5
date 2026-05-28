"""
ngram_chunks_change_point 테이블 삭제 후 재생성 스크립트
모든 윈도우 크기(5, 6, 7, 8, 9, 10, 11, 12)를 다시 생성합니다.
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

def regenerate_all_ngrams(window_sizes=[5, 6, 7, 8, 9, 10, 11, 12]):
    """
    ngram_chunks_change_point 테이블을 삭제하고 모든 grid_string에 대해 재생성
    
    Args:
        window_sizes: 생성할 윈도우 크기 리스트
    """
    print("=" * 60)
    print("⚠️  경고: 이 스크립트는 ngram_chunks_change_point 테이블의 모든 데이터를 삭제합니다!")
    print("=" * 60)
    print()
    
    # 사용자 확인
    response = input("계속하시겠습니까? (yes/no): ")
    if response.lower() not in ['yes', 'y']:
        print("취소되었습니다.")
        return
    
    # 테이블 생성 확인
    print("\n테이블 생성 확인 중...")
    try:
        create_change_point_preprocessed_grid_strings_table()
        create_change_point_ngram_chunks_table()
        print("✅ 테이블 생성 완료")
    except Exception as e:
        print(f"⚠️ 테이블 생성 중 오류 (계속 진행): {str(e)}")
    
    conn = get_change_point_db_connection()
    cursor = conn.cursor()
    
    try:
        # 기존 ngram_chunks_change_point 테이블 삭제
        print("\n기존 ngram_chunks_change_point 테이블 삭제 중...")
        cursor.execute("DROP TABLE IF EXISTS ngram_chunks_change_point")
        conn.commit()
        print("✅ 테이블 삭제 완료")
        
        # 테이블 재생성
        print("\n테이블 재생성 중...")
        create_change_point_ngram_chunks_table()
        print("✅ 테이블 재생성 완료")
        
        print()
        print(f"윈도우 크기 {window_sizes}로 N-gram 생성합니다...")
        print()
        
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
        total_generated = 0
        
        for record_id, grid_string in all_records:
            try:
                # ngram_chunks_change_point 생성
                result = generate_and_save_ngram_chunks_change_point(
                    record_id,
                    grid_string,
                    window_sizes=window_sizes,
                    conn=conn
                )
                
                total_ngrams = sum(result.values())
                total_generated += total_ngrams
                processed_count += 1
                
                if processed_count % 10 == 0 or processed_count == 1:
                    print(f"ID {record_id}: {total_ngrams}개의 ngram 생성 완료")
                    print(f"  - 윈도우별: {result}")
                    print(f"  진행률: {processed_count}/{total_count} ({processed_count*100//total_count}%)")
                
            except Exception as e:
                error_count += 1
                print(f"ID {record_id}: 오류 발생 - {str(e)}")
        
        print("-" * 60)
        print(f"처리 완료!")
        print(f"  - 성공: {processed_count}개")
        print(f"  - 오류: {error_count}개")
        print(f"  - 총 생성된 N-gram: {total_generated:,}개")
        
        # 최종 통계
        print("\n" + "=" * 60)
        print("최종 통계:")
        cursor.execute("""
            SELECT window_size, COUNT(*) as count
            FROM ngram_chunks_change_point
            GROUP BY window_size
            ORDER BY window_size
        """)
        for row in cursor.fetchall():
            print(f"  윈도우 크기 {row[0]}: {row[1]:,}개")
        
    except Exception as e:
        print(f"오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()


if __name__ == "__main__":
    print("=" * 60)
    print("ngram_chunks_change_point 테이블 재생성 스크립트")
    print("=" * 60)
    print()
    
    # 모든 윈도우 크기 재생성
    regenerate_all_ngrams(window_sizes=[5, 6, 7, 8, 9, 10, 11, 12])
