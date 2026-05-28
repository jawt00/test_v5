import sqlite3, csv  
db_path = r'C:\test_v5\results_db\sim_results_change_point.db'  
csv_path = r'C:\test_v5\results_db\step_events_export.csv'  
with sqlite3.connect(db_path) as conn:  
    cur = conn.cursor()  
    cur.execute('SELECT * FROM step_events')  
    cols = [d[0] for d in cur.description]  
    with open(csv_path, 'w', newline='', encoding='utf-8-sig') as f:  
        w = csv.writer(f)  
        w.writerow(cols)  
        w.writerows(cur.fetchall())  
print('--- 추출 완료! ---') 
