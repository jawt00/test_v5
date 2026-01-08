"""
중복 레코드 정리 스크립트
preprocessed_grid_strings 테이블에서 grid_string이 중복된 레코드를 삭제합니다.
각 grid_string 그룹에서 가장 오래된 레코드(id가 가장 작은 것) 하나만 남기고 나머지를 삭제합니다.
"""

import sqlite3
import os

# DB 경로 (svg_parser_module.py와 동일)
DB_PATH = 'hypothesis_validation.db'

def get_db_connection():
    """데이터베이스 연결"""
    try:
        db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)
        # 디렉토리가 없으면 생성
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir, exist_ok=True)
        return sqlite3.connect(db_path)
    except Exception as e:
        raise Exception(f"데이터베이스 연결 실패: {str(e)} (경로: {db_path})")

def check_duplicates():
    """중복 레코드 확인"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # 중복된 grid_string 확인
        cursor.execute('''
            SELECT grid_string, COUNT(*) as count, GROUP_CONCAT(id) as ids
            FROM preprocessed_grid_strings
            GROUP BY grid_string
            HAVING COUNT(*) > 1
            ORDER BY count DESC
        ''')
        
        duplicates = cursor.fetchall()
        
        if duplicates:
            print(f"\n중복된 grid_string 발견: {len(duplicates)}개 그룹\n")
            total_duplicates = 0
            for grid_string, count, ids in duplicates:
                total_duplicates += (count - 1)  # 각 그룹에서 1개는 남기므로
                print(f"Grid String: {grid_string[:50]}...")
                print(f"  중복 개수: {count}개")
                print(f"  ID 목록: {ids}")
                print()
            
            print(f"총 삭제될 레코드 수: {total_duplicates}개\n")
            return duplicates, total_duplicates
        else:
            print("중복된 레코드가 없습니다.")
            return [], 0
            
    except Exception as e:
        print(f"중복 확인 중 오류 발생: {str(e)}")
        return [], 0
    finally:
        conn.close()

def cleanup_duplicates(auto_confirm=False):
    """중복 레코드 삭제"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # 먼저 중복 확인
        duplicates, total_to_delete = check_duplicates()
        
        if total_to_delete == 0:
            print("삭제할 레코드가 없습니다.")
            return
        
        # 사용자 확인 (자동 모드가 아닐 때만)
        if not auto_confirm:
            try:
                response = input(f"\n{total_to_delete}개의 중복 레코드를 삭제하시겠습니까? (yes/no): ")
                if response.lower() != 'yes':
                    print("삭제가 취소되었습니다.")
                    return
            except (EOFError, KeyboardInterrupt):
                print("\n자동 모드로 실행합니다...")
                auto_confirm = True
        
        # 트랜잭션 시작
        conn.execute('BEGIN TRANSACTION')
        
        # 삭제 전 백업 정보 출력
        cursor.execute('SELECT COUNT(*) FROM preprocessed_grid_strings')
        before_count = cursor.fetchone()[0]
        print(f"\n삭제 전 레코드 수: {before_count}")
        
        # ngram_chunks에서 삭제될 grid_string_id의 레코드 먼저 삭제
        # 각 중복 그룹에서 가장 작은 id를 제외한 나머지 id들
        cursor.execute('''
            DELETE FROM ngram_chunks
            WHERE grid_string_id IN (
                SELECT id
                FROM preprocessed_grid_strings
                WHERE id NOT IN (
                    SELECT MIN(id)
                    FROM preprocessed_grid_strings
                    GROUP BY grid_string
                )
            )
        ''')
        ngram_deleted = cursor.rowcount
        print(f"ngram_chunks에서 삭제된 레코드: {ngram_deleted}개")
        
        # preprocessed_grid_strings에서 중복 레코드 삭제
        # 각 grid_string 그룹에서 id가 가장 작은 것 하나만 남기고 나머지 삭제
        cursor.execute('''
            DELETE FROM preprocessed_grid_strings
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM preprocessed_grid_strings
                GROUP BY grid_string
            )
        ''')
        deleted_count = cursor.rowcount
        print(f"preprocessed_grid_strings에서 삭제된 레코드: {deleted_count}개")
        
        # 삭제 후 레코드 수 확인
        cursor.execute('SELECT COUNT(*) FROM preprocessed_grid_strings')
        after_count = cursor.fetchone()[0]
        print(f"삭제 후 레코드 수: {after_count}")
        print(f"실제 삭제된 레코드 수: {before_count - after_count}개")
        
        # 커밋
        conn.commit()
        print("\n[SUCCESS] 중복 레코드 정리가 완료되었습니다!")
        
    except Exception as e:
        conn.rollback()
        print(f"\n[ERROR] 오류 발생: {str(e)}")
        print("변경사항이 롤백되었습니다.")
        raise e
    finally:
        conn.close()

if __name__ == "__main__":
    print("=" * 60)
    print("중복 레코드 정리 스크립트")
    print("=" * 60)
    
    # DB 파일 경로 확인
    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), DB_PATH)
    print(f"\n데이터베이스 경로: {db_path}")
    
    if not os.path.exists(db_path):
        print(f"\n❌ 데이터베이스 파일을 찾을 수 없습니다: {db_path}")
        exit(1)
    
    # 중복 확인만 먼저 실행
    print("\n[1단계] 중복 레코드 확인 중...")
    duplicates, total_to_delete = check_duplicates()
    
    if total_to_delete > 0:
        # 중복 삭제 실행 (자동 모드)
        print("\n[2단계] 중복 레코드 삭제 중...")
        cleanup_duplicates(auto_confirm=True)
    else:
        print("\n추가 작업이 필요하지 않습니다.")

