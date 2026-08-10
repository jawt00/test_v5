import sqlite3
import pandas as pd
import os

# DB 경로 및 저장 경로 설정
db_path1 = '/Users/tj/test_v5/change_point/db_backup/pattern_list2_TEST.db'
db_path2 = '/Users/tj/test_v5/change_point/db_backup/pattern_list3_TEST.db'
output_dir = '/Users/tj/test_v5/change_point/db_backup'
output_csv = os.path.join(output_dir, 'live_step_results_merged.csv')

# 각 DB에서 데이터 읽어오기
conn1 = sqlite3.connect(db_path1)
df1 = pd.read_sql_query("SELECT * FROM live_step_results", conn1)
conn1.close()

conn2 = sqlite3.connect(db_path2)
df2 = pd.read_sql_query("SELECT * FROM live_step_results", conn2)
conn2.close()

# 두 데이터프레임 합치기 (세로 방향)
merged_df = pd.concat([df1, df2], ignore_index=True)

# created_at 기준 오름차순(시간 순서) 정렬 및 검증
# created_at이 문자열 형태(예: '2026-08-07 12:00:00')여도 ISO 형식이면 정렬이 잘 동작하며, 
# 필요 시 datetime 타입으로 변환 후 정렬합니다.
merged_df['created_at_dt'] = pd.to_datetime(merged_df['created_at'])
merged_df = merged_df.sort_values(by='created_at_dt', ascending=True)

# 임시 변환 컬럼 삭제
merged_df = merged_df.drop(columns=['created_at_dt'])

# CSV 파일로 추출 (인덱스 제외)
merged_df.to_csv(output_csv, index=False, encoding='utf-8-sig')

print(f"추출 완료! 저장 경로: {output_csv}")
print(f"총 {len(merged_df)}건의 데이터가 created_at 기준 오름차순으로 정렬되었습니다.")