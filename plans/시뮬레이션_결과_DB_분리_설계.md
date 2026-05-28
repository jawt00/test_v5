# 시뮬레이션 결과 DB 분리 설계

## 목적/범위

- V3(9–14) 등 시뮬레이션 검증 결과를 **기존 change-point DB와 분리된 SQLite 파일**에 누적 저장
- 사용자가 **"결과 저장" 버튼을 눌렀을 때만** 저장(수동 저장)
- **개별 grid_string 상세 히스토리(step 단위)**를 반드시 포함

## DB 파일 위치 정책

- **폴더**: `c:\test_v5\results_db\`
- **파일**: `sim_results_change_point.db` (단일 파일에 누적, 로테이션은 추후 확장)
- 기존 DB(change_point_ngram.db 등)는 읽기/예측 생성만 사용, 결과 쓰기는 이 DB로만 수행

## 스키마(테이블/인덱스)

### runs (런 메타)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| run_id | TEXT PK | UUID |
| created_at | TIMESTAMP | |
| engine_version | TEXT | 재현성용 (git hash 등) |
| hypothesis_key | TEXT | 예: first_anchor_extended_window_v3 |
| cutoff_grid_string_id | INTEGER | |
| method | TEXT | |
| threshold | REAL | |
| window_sizes | TEXT | JSON 배열 문자열 "[9,10,11,12,13,14]" |
| notes | TEXT | optional |

### run_summary (런 단위 집계)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| run_id | TEXT PK/FK | |
| total_grid_strings | INTEGER | |
| avg_accuracy | REAL | |
| max_consecutive_failures | INTEGER | |
| avg_max_consecutive_failures | REAL | |
| total_steps | INTEGER | |
| total_failures | INTEGER | |
| total_predictions | INTEGER | |
| total_skipped | INTEGER | |

### grid_results (grid_string 단위 결과)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| run_id | TEXT FK | |
| grid_string_id | INTEGER | |
| accuracy | REAL | |
| max_consecutive_failures | INTEGER | |
| total_steps | INTEGER | |
| total_failures | INTEGER | |
| total_predictions | INTEGER | |
| total_skipped | INTEGER | |
| stopped_early | INTEGER | 0/1 |
| PRIMARY KEY(run_id, grid_string_id) | | |

### step_events (개별 스트링 상세 히스토리, 필수)

| 컬럼 | 타입 | 설명 |
|------|------|------|
| run_id | TEXT FK | |
| grid_string_id | INTEGER | |
| step | INTEGER | |
| position | INTEGER | |
| anchor | INTEGER | |
| window_size | INTEGER | |
| prefix | TEXT | |
| predicted | TEXT | nullable |
| actual | TEXT | nullable |
| is_correct | INTEGER | 1/0/NULL |
| confidence | REAL | |
| selected_window_size | INTEGER | nullable |
| skipped | INTEGER | 0/1 |
| skip_reason | TEXT | nullable |
| all_predictions_json | TEXT | nullable, JSON |
| PRIMARY KEY(run_id, grid_string_id, step) | | |

### 인덱스

- `grid_results(run_id)`
- `grid_results(grid_string_id)`
- `step_events(run_id, grid_string_id)`
- `step_events(run_id, anchor)`
- `step_events(run_id, window_size)`

## 저장 흐름

- **run_id 생성**: 저장 시점에 UUID 생성
- **트랜잭션**: run 1회 저장 = BEGIN → insert_run → insert_run_summary → insert_grid_results → insert_step_events → COMMIT. step_events 없이 run 저장하지 않음.
- **연결**: `change_point/results_storage.py`의 `get_results_db_connection(path)` 사용

## 저장된 run_id로 조회 쿼리 예시

결과 DB 파일(`results_db/sim_results_change_point.db`)을 연 뒤 아래 SQL로 분석 가능.

### 런 목록 및 요약

```sql
SELECT r.run_id, r.created_at, r.hypothesis_key, r.cutoff_grid_string_id, r.method, r.threshold,
       s.total_grid_strings, s.avg_accuracy, s.max_consecutive_failures, s.total_predictions, s.total_skipped
FROM runs r
JOIN run_summary s ON r.run_id = s.run_id
ORDER BY r.created_at DESC;
```

### 특정 run의 grid_string별 결과

```sql
SELECT grid_string_id, accuracy, max_consecutive_failures, total_predictions, total_skipped, stopped_early
FROM grid_results
WHERE run_id = ?
ORDER BY grid_string_id;
```

### 특정 run의 개별 스트링 상세 히스토리(step_events)

```sql
SELECT step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped, skip_reason
FROM step_events
WHERE run_id = ? AND grid_string_id = ?
ORDER BY step;
```

### run별 step_events 개수 확인

```sql
SELECT run_id, grid_string_id, COUNT(*) AS step_count
FROM step_events
GROUP BY run_id, grid_string_id;
```

## 추후 로테이션 확장

- 현재: 단일 파일에 누적
- 확장 시: `results_db/sim_results_change_point_YYYY_MM.db` 또는 per_run 별도 파일 등 path resolver에서 분기
