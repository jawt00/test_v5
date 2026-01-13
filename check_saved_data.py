import sqlite3

conn = sqlite3.connect('hypothesis_validation.db')
cursor = conn.cursor()

# 최근 세션 확인
validation_id = '621bf73d'
cursor.execute('''
    SELECT validation_id, window_size, method, use_threshold, threshold, cutoff_grid_string_id
    FROM confidence_skip_validation_sessions 
    WHERE validation_id LIKE ?
''', (validation_id + '%',))
session = cursor.fetchone()

if session:
    print('=== 최근 세션 정보 ===')
    print(f'validation_id: {session[0]}')
    print(f'window_size: {session[1]}, method: {session[2]}, use_threshold: {session[3]}, threshold: {session[4]}, cutoff: {session[5]}')
    
    # stored_predictions 테이블 확인
    snapshot_threshold = session[4] if session[3] else 0.0
    cursor.execute('''
        SELECT COUNT(*) FROM stored_predictions 
        WHERE window_size = ? AND method = ? AND threshold = ?
    ''', (session[1], session[2], snapshot_threshold))
    stored_count = cursor.fetchone()[0]
    print(f'\nstored_predictions 테이블에 해당 조건의 예측값 개수: {stored_count}')
    
    # 예측값 스냅샷 확인
    cursor.execute('''
        SELECT COUNT(*) FROM validation_session_prediction_snapshots 
        WHERE validation_id = ?
    ''', (session[0],))
    snapshot_count = cursor.fetchone()[0]
    print(f'validation_session_prediction_snapshots에 저장된 개수: {snapshot_count}')
    
    # Grid String ID 리스트 확인
    cursor.execute('''
        SELECT COUNT(*) FROM validation_session_grid_strings 
        WHERE validation_id = ?
    ''', (session[0],))
    grid_count = cursor.fetchone()[0]
    print(f'validation_session_grid_strings에 저장된 개수: {grid_count}')

# 모든 세션의 저장 상태 확인
print('\n=== 모든 세션 저장 상태 ===')
cursor.execute('''
    SELECT 
        s.validation_id,
        s.window_size,
        s.method,
        s.threshold,
        (SELECT COUNT(*) FROM validation_session_prediction_snapshots WHERE validation_id = s.validation_id) as snapshot_count,
        (SELECT COUNT(*) FROM validation_session_grid_strings WHERE validation_id = s.validation_id) as grid_count
    FROM confidence_skip_validation_sessions s
    ORDER BY s.created_at DESC
    LIMIT 5
''')
for row in cursor.fetchall():
    print(f'validation_id: {row[0][:8]}..., window_size: {row[1]}, method: {row[2]}, threshold: {row[3]}, snapshot: {row[4]}, grid: {row[5]}')

conn.close()
