"""
change_point_ngram.db 데이터베이스 확인 스크립트
ngram_chunks_change_point 테이블의 데이터를 확인합니다.
"""

import sqlite3
import os
from svg_parser_module import get_change_point_db_connection

def check_database():
    """데이터베이스 상태 확인"""
    try:
        conn = get_change_point_db_connection()
        cursor = conn.cursor()
        
        # 테이블 목록 확인
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = [row[0] for row in cursor.fetchall()]
        print("=" * 60)
        print("데이터베이스 테이블 목록:")
        for table in tables:
            print(f"  - {table}")
        print()
        
        # preprocessed_grid_strings 테이블 확인
        cursor.execute("SELECT COUNT(*) FROM preprocessed_grid_strings")
        grid_string_count = cursor.fetchone()[0]
        print(f"preprocessed_grid_strings 레코드 수: {grid_string_count}")
        
        if grid_string_count > 0:
            cursor.execute("SELECT id, string_length, created_at FROM preprocessed_grid_strings ORDER BY id LIMIT 5")
            print("\n최근 5개 grid_string:")
            for row in cursor.fetchall():
                print(f"  ID: {row[0]}, 길이: {row[1]}, 생성일: {row[2]}")
        print()
        
        # ngram_chunks_change_point 테이블 확인
        cursor.execute("SELECT COUNT(*) FROM ngram_chunks_change_point")
        ngram_count = cursor.fetchone()[0]
        print(f"ngram_chunks_change_point 레코드 수: {ngram_count}")
        
        if ngram_count > 0:
            # 윈도우 크기별 통계
            cursor.execute("""
                SELECT window_size, COUNT(*) as count
                FROM ngram_chunks_change_point
                GROUP BY window_size
                ORDER BY window_size
            """)
            print("\n윈도우 크기별 N-gram 개수:")
            for row in cursor.fetchall():
                print(f"  윈도우 크기 {row[0]}: {row[1]:,}개")
            
            # grid_string_id별 통계
            cursor.execute("""
                SELECT grid_string_id, COUNT(*) as count
                FROM ngram_chunks_change_point
                GROUP BY grid_string_id
                ORDER BY grid_string_id
                LIMIT 10
            """)
            print("\nGrid String ID별 N-gram 개수 (상위 10개):")
            for row in cursor.fetchall():
                print(f"  Grid String ID {row[0]}: {row[1]:,}개")
        else:
            print("\n⚠️ ngram_chunks_change_point 테이블에 데이터가 없습니다!")
        
        # 각 grid_string_id에 대해 윈도우 크기별로 데이터가 있는지 확인
        if grid_string_count > 0:
            print("\n" + "=" * 60)
            print("Grid String별 윈도우 크기 데이터 존재 여부:")
            cursor.execute("SELECT id FROM preprocessed_grid_strings ORDER BY id LIMIT 10")
            grid_string_ids = [row[0] for row in cursor.fetchall()]
            
            for grid_string_id in grid_string_ids:
                cursor.execute("""
                    SELECT window_size, COUNT(*) as count
                    FROM ngram_chunks_change_point
                    WHERE grid_string_id = ?
                    GROUP BY window_size
                    ORDER BY window_size
                """, (grid_string_id,))
                
                window_data = cursor.fetchall()
                if window_data:
                    window_sizes = [str(row[0]) for row in window_data]
                    print(f"  Grid String ID {grid_string_id}: 윈도우 크기 {', '.join(window_sizes)}")
                else:
                    print(f"  Grid String ID {grid_string_id}: ⚠️ N-gram 데이터 없음")
        
        conn.close()
        print("\n" + "=" * 60)
        
    except Exception as e:
        print(f"오류 발생: {str(e)}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_database()
