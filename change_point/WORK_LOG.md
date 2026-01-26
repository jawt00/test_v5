# Change-point Detection 기반 N-gram 생성 작업 로그

## 작업 개요

Change-point Detection 기반 N-gram 생성 기능에 윈도우 크기 10, 11, 12를 추가하고, 관련 스크립트와 앱을 수정한 작업 내역입니다.

---

## 작업 일자

2026-01-26

---

## 작업 배경

### 초기 상태
- Change-point Detection 기반 N-gram 생성 기능이 구현되어 있었음
- 기본 윈도우 크기: 5, 6, 7, 8, 9만 생성
- `change_point/change_point_ngram.db` 데이터베이스 사용
- `change_point/change_point_ngram_app.py` Streamlit 앱 존재

### 요구사항
- 윈도우 크기 10, 11, 12 추가 생성
- 기존 데이터와의 호환성 유지
- 앱에서 자동 생성 시에도 새로운 윈도우 크기 포함

---

## 작업 내역

### 1. 스크립트 수정: `generate_change_point_ngrams.py`

**위치**: 루트 폴더 (`c:\test_v5\generate_change_point_ngrams.py`)

**변경 내용**:
- 기본 윈도우 크기를 `[10, 11, 12]`로 변경
- 윈도우 크기별 개별 확인 로직 추가
- 누락된 윈도우 크기만 선택적으로 생성

**주요 기능**:
```python
def generate_ngrams_for_all_grid_strings(window_sizes=[10, 11, 12]):
    # 각 윈도우 크기별로 이미 있는지 확인
    # 없는 윈도우 크기만 생성
```

**사용 방법**:
```bash
python generate_change_point_ngrams.py
```

---

### 2. 앱 수정: `change_point/change_point_ngram_app.py`

**변경 내용**:

#### 2.1 자동 생성 시 윈도우 크기 추가 (281번 줄)
```python
# 수정 전
generate_and_save_ngram_chunks_change_point(record_id, grid_string, conn=conn)

# 수정 후
generate_and_save_ngram_chunks_change_point(
    record_id, 
    grid_string, 
    window_sizes=[5, 6, 7, 8, 9, 10, 11, 12],
    conn=conn
)
```

#### 2.2 중복된 grid_string 처리 개선 (185-196번 줄)
- 중복된 grid_string의 경우에도 누락된 윈도우 크기 확인 및 생성
- 윈도우 크기별로 개별 확인하여 없는 것만 생성

**변경 전**:
```python
# 이미 저장된 레코드이므로 ngram_chunks_change_point는 생성하지 않음
```

**변경 후**:
```python
# 중복된 경우에도 누락된 윈도우 크기 확인 및 생성
target_window_sizes = [5, 6, 7, 8, 9, 10, 11, 12]
missing_window_sizes = []
# 각 윈도우 크기별로 확인하고 없는 것만 생성
```

#### 2.3 UI 업데이트
- 안내 메시지: "기본 윈도우 크기(5, 6, 7, 8, 9)" → "(5, 6, 7, 8, 9, 10, 11, 12)"
- 윈도우 크기 선택 옵션에 10, 11, 12 추가
- 사용 방법 안내 문서 업데이트

---

### 3. 데이터베이스 확인 스크립트 생성

**파일**: `check_ngram_db.py` (루트 폴더)

**기능**:
- 데이터베이스 테이블 목록 확인
- `preprocessed_grid_strings` 레코드 수 확인
- `ngram_chunks_change_point` 레코드 수 확인
- 윈도우 크기별 N-gram 개수 통계
- Grid String별 윈도우 크기 데이터 존재 여부 확인

**사용 방법**:
```bash
python check_ngram_db.py
```

---

### 4. 누락된 윈도우 크기 생성 스크립트

**파일**: `generate_missing_ngrams.py` (루트 폴더)

**기능**:
- 기존 데이터 유지
- 누락된 윈도우 크기(10, 11, 12)만 생성
- 윈도우 크기별 개별 확인

**사용 방법**:
```bash
python generate_missing_ngrams.py
```

**특징**:
- 안전하고 빠름
- 기존 데이터 손실 없음
- 중복 생성 방지

---

### 5. 전체 재생성 스크립트

**파일**: `regenerate_all_ngrams.py` (루트 폴더)

**기능**:
- `ngram_chunks_change_point` 테이블 삭제 후 재생성
- 모든 윈도우 크기(5, 6, 7, 8, 9, 10, 11, 12) 재생성
- 사용자 확인 후 실행

**사용 방법**:
```bash
python regenerate_all_ngrams.py
```

**주의사항**:
- ⚠️ 기존 데이터를 모두 삭제함
- 실행 전 사용자 확인 필요

---

## 데이터베이스 구조

### 테이블: `preprocessed_grid_strings`
- `id`: PRIMARY KEY
- `grid_string`: TEXT NOT NULL UNIQUE
- `source_session_id`: TEXT
- `source_id`: TEXT
- `string_length`: INTEGER
- `b_count`, `p_count`, `t_count`: INTEGER
- `b_ratio`, `p_ratio`: REAL
- `created_at`: TIMESTAMP

### 테이블: `ngram_chunks_change_point`
- `id`: PRIMARY KEY
- `grid_string_id`: INTEGER (FK to preprocessed_grid_strings)
- `window_size`: INTEGER (5, 6, 7, 8, 9, 10, 11, 12)
- `chunk_index`: INTEGER (앵커 위치)
- `prefix`: TEXT
- `suffix`: TEXT
- `full_chunk`: TEXT
- `created_at`: TIMESTAMP
- UNIQUE 제약: `(grid_string_id, window_size, chunk_index)`

---

## Change-point Detection 알고리즘

### 규칙
1. **Trigger (변화점 감지)**: `Input[i] ≠ Input[i+1]` 일 때 변화점 감지
2. **Anchor (앵커 위치)**: 변화 감지 이전 위치 (i)를 앵커로 사용
3. **N-gram 생성**: 각 앵커 위치에서 `window_size`만큼 추출

### 예시
- 입력: `"bbppbb"`, `window_size = 5`
- 변화점 감지:
  - 인덱스 1: 'b' → 'p' (변화 감지) → 앵커 1 추가 (변화 이전 위치 i=1)
  - 인덱스 3: 'p' → 'b' (변화 감지) → 앵커 3 추가 (변화 이전 위치 i=3)
- 앵커 리스트: [1, 3]
  - 앵커 1: 인덱스 1 (두 번째 'b')
  - 앵커 3: 인덱스 3 (두 번째 'p')
- N-gram 생성:
  - 앵커 1: `grid_string[1:6]` = `"bppbb"` (prefix: `"bppb"`, suffix: `"b"`)
  - 앵커 3: `grid_string[3:8]` = `"pbb"` (길이 부족, 생성 불가)

---

## 관련 파일 목록

### 앱 파일
- `change_point/change_point_ngram_app.py` - Change-point Detection 기반 N-gram 생성 앱
- `change_point/suffix_confidence_viewer_app.py` - Suffix 신뢰도 조회 앱

### 스크립트 파일 (루트 폴더)
- `generate_change_point_ngrams.py` - 윈도우 크기 10, 11, 12 생성 스크립트
- `generate_missing_ngrams.py` - 누락된 윈도우 크기만 생성 스크립트
- `regenerate_all_ngrams.py` - 전체 재생성 스크립트
- `check_ngram_db.py` - 데이터베이스 확인 스크립트

### 모듈 파일
- `svg_parser_module.py` - 핵심 함수들
  - `get_change_point_db_connection()` - DB 연결
  - `create_change_point_preprocessed_grid_strings_table()` - 테이블 생성
  - `create_change_point_ngram_chunks_table()` - 테이블 생성
  - `generate_and_save_ngram_chunks_change_point()` - N-gram 생성

### 데이터베이스
- `change_point/change_point_ngram.db` - Change-point Detection 데이터베이스

### 계획서
- `plans/Change-point_Detection_기반_N-gram_생성_기능_추가.md` - 초기 계획서

---

## 현재 상태

### 완료된 작업
- ✅ 윈도우 크기 10, 11, 12 추가 생성 기능 구현
- ✅ 앱에서 자동 생성 시 새로운 윈도우 크기 포함
- ✅ 중복된 grid_string 처리 개선
- ✅ 데이터베이스 확인 스크립트 생성
- ✅ 누락된 윈도우 크기 생성 스크립트 생성
- ✅ 전체 재생성 스크립트 생성

### 지원하는 윈도우 크기
- 기본: 5, 6, 7, 8, 9, 10, 11, 12

---

## 향후 작업 가이드

### 새로운 grid_string 추가 시
1. `change_point_ngram_app.py` 앱 사용
2. SVG 코드 입력 및 파싱
3. "💾 DB 저장" 버튼 클릭
4. 자동으로 모든 윈도우 크기(5-12)의 N-gram 생성됨

### 기존 데이터에 누락된 윈도우 크기 추가 시
```bash
python generate_missing_ngrams.py
```

### 전체 재생성이 필요한 경우
```bash
python regenerate_all_ngrams.py
```

### 데이터베이스 상태 확인
```bash
python check_ngram_db.py
```

---

## 주의사항

1. **중복 방지**: `UNIQUE(grid_string_id, window_size, chunk_index)` 제약조건으로 중복 자동 방지
2. **데이터 손실**: `regenerate_all_ngrams.py` 실행 시 기존 데이터 삭제됨
3. **성능**: 대량의 grid_string 처리 시 시간이 소요될 수 있음
4. **연결 관리**: DB 연결은 함수 내에서 자동 관리됨 (conn 파라미터 전달 시 재사용)

---

## 문제 해결

### N-gram이 생성되지 않는 경우
1. `check_ngram_db.py`로 데이터베이스 상태 확인
2. 윈도우 크기별 데이터 존재 여부 확인
3. 누락된 경우 `generate_missing_ngrams.py` 실행

### 중복된 grid_string 처리
- 앱에서 중복된 grid_string 저장 시 자동으로 누락된 윈도우 크기 확인 및 생성
- 수동으로 확인하려면 `check_ngram_db.py` 사용

---

## 참고 자료

- 계획서: `plans/Change-point_Detection_기반_N-gram_생성_기능_추가.md`
- 모듈: `svg_parser_module.py`
- 앱: `change_point/change_point_ngram_app.py`

---

## 업데이트 이력

- 2026-01-26: 윈도우 크기 10, 11, 12 추가 및 관련 스크립트/앱 수정 완료

---

## 시뮬레이션 가설 모듈화 작업 (2026-01-26)

### 작업 배경
- 기존 `change_point_simulation_app.py`는 하드코딩된 전략만 사용
- 다양한 가설을 쉽게 추가하고 테스트할 수 있는 확장 가능한 구조 필요
- 가설을 변경하면서 성능을 비교하고 싶은 요구사항

### 작업 내용

#### 1. 파일 구조 정리
- `change_point_simulation_app.py` → `change_point/change_point_simulation_app.py`로 이동
- 상위 폴더 모듈 import를 위한 경로 설정 추가

#### 2. 가설 모듈 생성 (`change_point/change_point_hypothesis_module.py`)
- **Hypothesis 추상 클래스**: 모든 가설이 구현해야 하는 인터페이스 정의
  - `predict()`: 예측 수행 메서드
  - `get_name()`, `get_description()`, `get_config_schema()`: 메타데이터 메서드
- **가설 레지스트리 시스템**: 
  - `register_hypothesis()`: 가설 등록
  - `get_hypothesis()`: 가설 인스턴스 생성
  - `list_hypotheses()`: 등록된 가설 목록 조회
- **기본 가설 구현**:
  - `BestConfidenceHypothesis`: 최고 신뢰도 선택 (기존 `get_multi_window_prediction_cp` 로직)
  - `ConfidenceSkipHypothesis`: 신뢰도 스킵 전략 (기존 `get_multi_window_prediction_with_confidence_skip_cp` 로직)
- **검증 함수**:
  - `validate_hypothesis_cp()`: 단일 grid_string 검증
  - `batch_validate_hypothesis_cp()`: 배치 검증

#### 3. 가설 테스트 앱 생성 (`change_point/change_point_hypothesis_test_app.py`)
- **단일 테스트 모드**: 하나의 가설을 선택하여 상세 분석
- **비교 테스트 모드**: 여러 가설을 동시에 실행하여 성능 비교
- **동적 설정 UI**: 가설별 설정 파라미터를 스키마 기반으로 자동 생성
- **결과 표시**: 비교 테이블, 메트릭, 상세 히스토리

### 파일 구조
```
change_point/
  ├── change_point_simulation_app.py          # 기본 앱 (기존 기능 유지)
  ├── change_point_hypothesis_module.py      # 가설 시스템 (새로 생성)
  └── change_point_hypothesis_test_app.py     # 가설 테스트 앱 (새로 생성)
```

### 사용 방법
1. **기본 앱**: `change_point/change_point_simulation_app.py` - 기존과 동일하게 사용
2. **가설 테스트**: `change_point/change_point_hypothesis_test_app.py` - 새로운 가설 테스트 및 비교

### 새로운 가설 추가 방법
1. `Hypothesis` 클래스를 상속하는 새 클래스 생성
2. `predict()`, `get_name()`, `get_description()` 메서드 구현
3. 필요시 `get_config_schema()`로 설정 파라미터 정의
4. `register_hypothesis("가설명", 가설클래스)`로 레지스트리에 등록

### 주요 특징
- 확장 가능한 구조: 새로운 가설을 쉽게 추가 가능
- 기존 코드 호환성: 기존 앱과 함수들은 그대로 유지
- 모듈화: 가설 로직과 검증 로직 분리
- 유연한 설정: 가설별 파라미터를 동적으로 설정 가능
