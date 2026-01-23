---
name: Change-point Detection 기반 N-gram 생성 기능 추가
overview: Change-point Detection(변화점 탐지) 기반의 새로운 ngram 생성 방식을 구현합니다. 기존의 슬라이딩 윈도우 방식과 달리, 문자가 변하는 시점(변화점)을 감지하여 그 이전 위치를 앵커로 사용하여 ngram을 생성합니다.
todos:
  - id: analyze-existing-code
    content: 기존 generate_and_save_ngram_chunks 함수 구조 분석 및 이해
    status: pending
  - id: design-algorithm
    content: Change-point Detection 알고리즘 설계 - 앵커 수집 로직 구현
    status: pending
  - id: implement-function
    content: generate_and_save_ngram_chunks_change_point 함수 구현
    status: pending
  - id: add-anchor-collection
    content: 앵커 위치 수집 로직 구현 - 인덱스 0 추가 및 변화점 감지
    status: pending
  - id: implement-ngram-generation
    content: 앵커 기반 ngram 생성 로직 구현 - 각 앵커에서 window_size만큼 추출
    status: pending
  - id: add-db-storage
    content: DB 저장 로직 구현 - 기존 ngram_chunks 테이블 구조 활용
    status: pending
  - id: test-basic-case
    content: 기본 테스트 케이스 검증 - 간단한 문자열로 앵커 감지 확인
    status: pending
  - id: test-integration
    content: 통합 테스트 - 실제 grid_string으로 ngram 생성 및 DB 저장 확인
    status: pending
---

# Change-point Detection 기반 N-gram 생성 기능 추가 계획

## 1. 배경 및 목적

### 1.1 기존 방식
- **위치**: `svg_parser_module.py`의 `generate_and_save_ngram_chunks()` 함수
- **방식**: 슬라이딩 윈도우 - 모든 위치에서 고정된 윈도우 크기로 ngram 생성
- **특징**: 모든 인덱스에서 연속적으로 ngram 생성

### 1.2 새로운 방식
- **이름**: Change-point Detection 기반 ngram 생성
- **개념**: 문자가 변하는 시점(변화점)을 감지하여 의미 있는 위치에서만 ngram 생성
- **목적**: 패턴 변화 지점에 집중하여 더 의미 있는 ngram 추출

## 2. Change-point Detection 규칙 정의

### 2.1 Trigger (절단 조건)
```
Input[i] ≠ Input[i+1] 일 때 변화점 감지
```
- 문자가 바뀌는 시점을 감지
- 예: `"bbppbb"` → 인덱스 1→2, 3→4에서 변화 감지

### 2.2 Anchor (N-gram 시작점)
```
변화 감지 이전 위치 (i)를 앵커로 사용
```
- 변화점이 감지된 위치의 **이전 위치**를 앵커로 사용
- 예: 인덱스 1에서 변화 감지 → 앵커는 0 (변화 이전)
- 앵커 0 기준, 윈도우 5면 `grid_string[0:5]` = `"bppbb"`로 ngram 생성

### 2.3 예외 규칙
```
grid_string 시작점(인덱스 0)은 항상 앵커로 포함
```
- 문자열의 시작점은 조건 없이 항상 앵커로 사용
- 첫 번째 ngram은 항상 인덱스 0에서 시작

## 3. 구현 세부사항

### 3.1 함수 시그니처
```python
def generate_and_save_ngram_chunks_change_point(
    grid_string_id, 
    grid_string, 
    window_sizes=[5, 6, 7, 8, 9], 
    conn=None
):
    """
    Change-point Detection 기반으로 ngram_chunks를 생성하여 DB에 저장
    
    Args:
        grid_string_id: preprocessed_grid_strings 테이블의 id
        grid_string: 처리할 grid string
        window_sizes: 생성할 윈도우 크기 리스트
        conn: 기존 데이터베이스 연결 (None이면 새로 생성)
    
    Returns:
        dict: {window_size: chunk_count}
    """
```

### 3.2 알고리즘 흐름

1. **앵커 위치 수집**
   - 인덱스 0을 앵커 리스트에 추가 (예외 규칙)
   - `i`를 0부터 `len(grid_string)-2`까지 순회
   - `grid_string[i] != grid_string[i+1]` 조건 확인
   - 조건 만족 시 `i`를 앵커 리스트에 추가 (변화 이전 위치)

2. **N-gram 생성**
   - 각 윈도우 크기에 대해:
     - 앵커 리스트를 순회
     - 각 앵커 위치에서 `grid_string[anchor:anchor+window_size]` 추출
     - 문자열 길이 체크 (범위 초과 방지)
     - prefix와 suffix 분리
     - DB에 저장

3. **DB 저장**
   - 기존 `ngram_chunks` 테이블 구조 그대로 사용
   - `chunk_index`는 앵커 위치 사용
   - 중복 방지: `INSERT OR IGNORE` 사용

### 3.3 예시

**입력**: `grid_string = "bbppbb"`, `window_size = 5`

**변화점 감지**:
- 인덱스 1: 'b' → 'p' (변화 감지) → 앵커 0 추가
- 인덱스 3: 'p' → 'b' (변화 감지) → 앵커 2 추가

**앵커 리스트**: [0, 0, 2] → 중복 제거 후 [0, 2]

**N-gram 생성**:
- 앵커 0: `grid_string[0:5]` = `"bbppb"` (prefix: `"bbpp"`, suffix: `"b"`)
- 앵커 2: `grid_string[2:7]` = `"ppbb"` (길이 부족, 생성 불가)

**결과**: window_size=5일 때 1개의 ngram 생성

## 4. 파일 수정 위치

### 4.1 svg_parser_module.py
- **위치**: `generate_and_save_ngram_chunks()` 함수 다음 (약 319번 줄 이후)
- **작업**: 새로운 함수 `generate_and_save_ngram_chunks_change_point()` 추가
- **기존 함수**: 수정하지 않음 (기존 슬라이딩 윈도우 방식 유지)

### 4.2 함수 구조
```python
def generate_and_save_ngram_chunks_change_point(...):
    # 1. DB 연결 설정
    # 2. 앵커 위치 수집
    #    - 인덱스 0 추가
    #    - 변화점 감지 및 앵커 수집
    # 3. 각 윈도우 크기별 처리
    #    - 앵커별 ngram 생성
    #    - DB 저장
    # 4. 결과 반환
```

## 5. 데이터베이스 구조

### 5.1 기존 테이블 활용
- **테이블**: `ngram_chunks` (기존 구조 그대로 사용)
- **컬럼**: 
  - `grid_string_id`: grid_string ID
  - `window_size`: 윈도우 크기
  - `chunk_index`: 앵커 위치 (변화 이전 위치)
  - `prefix`: ngram의 앞부분
  - `suffix`: ngram의 마지막 문자
  - `full_chunk`: 전체 ngram

### 5.2 중복 처리
- `UNIQUE(grid_string_id, window_size, chunk_index)` 제약조건 활용
- 동일한 앵커 위치에서 생성된 ngram은 자동으로 중복 제거

## 6. 기존 방식과의 차이점

### 6.1 생성되는 N-gram 수
- **기존 방식**: `len(grid_string) - window_size + 1` 개
- **새로운 방식**: 앵커 개수에 따라 결정 (일반적으로 더 적음)

### 6.2 생성 위치
- **기존 방식**: 모든 연속 위치
- **새로운 방식**: 변화점 이전 위치만 (의미 있는 위치)

### 6.3 사용 목적
- **기존 방식**: 전체 패턴 학습
- **새로운 방식**: 패턴 변화 지점에 집중한 학습

## 7. 검증 방법

### 7.1 단위 테스트
- 간단한 문자열로 앵커 감지 정확성 확인
- 예: `"bbppbb"` → 앵커 [0, 2] 확인

### 7.2 통합 테스트
- 실제 grid_string으로 ngram 생성
- DB 저장 확인
- 생성된 ngram 개수 확인

### 7.3 비교 분석
- 동일한 grid_string에 대해 기존 방식과 새로운 방식 비교
- 생성된 ngram 개수 및 패턴 차이 분석

## 8. 향후 확장 가능성

### 8.1 하이브리드 방식
- 기존 슬라이딩 윈도우와 Change-point Detection 병행 사용
- 두 방식의 ngram을 모두 저장하여 선택적으로 활용

### 8.2 가중치 적용
- 변화점 근처의 ngram에 더 높은 가중치 부여
- 예측 모델에서 가중치 기반 학습

### 8.3 변화 강도 고려
- 단순 변화뿐만 아니라 변화 강도(연속 변화 등) 고려
- 더 정교한 앵커 선택 로직
