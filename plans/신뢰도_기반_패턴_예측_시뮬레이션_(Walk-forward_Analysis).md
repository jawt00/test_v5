---
name: 신뢰도 기반 패턴 예측 시뮬레이션 (Walk-forward Analysis)
overview: 5회 연속 실패 방지를 목표로, Walk-forward Analysis를 적용한 신뢰도 기반 패턴 예측 시뮬레이션을 구현합니다. 윈도우 크기 8-12, 임계값 50-65% 범위에서 최적 조합을 탐색하며, 최소 표본 수 제약을 적용합니다.
todos:
  - id: "1"
    content: walk_forward_simulation_cp.py 신규 생성 - Walk-forward Analysis 엔진 구현 (데이터 분할, 학습/검증/업데이트 루프)
    status: pending
  - id: "2"
    content: change_point_prediction_module.py 수정 - save_or_update_predictions_for_change_point_data에 min_sample_count 파라미터 추가 및 필터링 로직 구현
    status: pending
  - id: "3"
    content: walk_forward_simulation_cp.py에 성과 측정 함수 추가 - MCL, Total Bets, Win Rate, Failure Score 계산
    status: pending
  - id: "4"
    content: change_point_simulation_app.py 수정 - Walk-forward Analysis 모드 UI 추가 (윈도우 크기 8-12, 임계값 50-65%, 최소 표본 수 설정)
    status: pending
  - id: "5"
    content: 결과 표시 로직 구현 - 각 (W,T) 조합별 성과 지표 테이블, MCL<5 만족 조합 하이라이트, 최적 조합 추천
    status: pending
isProject: false
---

# 신뢰도 기반 패턴 예측 시뮬레이션 계획 (Walk-forward Analysis)

## 목표
- **핵심 목표**: Max Consecutive Losses < 5를 만족하는 가장 낮은 임계값(T) 탐색
- **윈도우 크기**: 8, 9, 10, 11, 12 (5개 케이스)
- **임계값 범위**: 50% ~ 65% (1% 단위 스캔, 총 16개 값)

## 아키텍처 개요

### 데이터 흐름
```
전체 데이터 (preprocessed_grid_strings)
    ↓
[초기 학습] 첫 40% → Prefix-Suffix 확률 테이블 생성
    ↓
[검증] 다음 10% → 예측 수행 및 성과 기록
    ↓
[업데이트] 검증 완료된 10% → 학습 데이터에 포함
    ↓
[반복] 데이터 끝까지 반복
```

### 핵심 컴포넌트

1. **Walk-forward Analysis 엔진** (`walk_forward_simulation_cp.py`)
   - 데이터 분할: 40% 초기 학습, 이후 10%씩 검증/업데이트
   - 시간 순서 유지: `created_at` 또는 `id` 기준 정렬
   - Rolling 업데이트: 검증 완료 후 학습 데이터에 자동 포함

2. **최소 표본 수 필터** (S_min = 15)
   - `stored_predictions_change_point` 생성 시 패턴 출현 횟수 확인
   - `ngram_chunks_change_point`에서 prefix별 출현 횟수 집계
   - 15회 미만 패턴은 예측에서 제외

3. **성과 측정 모듈**
   - MCL (Max Consecutive Losses): 최대 연속 패배 횟수
   - Total Bets: 총 베팅 횟수
   - Win Rate: 베팅 시 적중률
   - Failure Score: 5연패 발생 횟수 (0이어야 합격)

## 구현 세부사항

### 1. Walk-forward Analysis 함수

**파일**: `change_point/walk_forward_simulation_cp.py` (신규 생성)

```python
def walk_forward_simulation_cp(
    window_sizes=(8, 9, 10, 11, 12),
    threshold_range=(50, 65, 1),  # (min, max, step)
    method="빈도 기반",
    initial_train_ratio=0.4,
    validation_ratio=0.1,
    min_sample_count=15,  # S_min
    progress_callback=None
):
    """
    Walk-forward Analysis 기반 시뮬레이션
    
    Returns:
        dict: {
            "results": [
                {
                    "window_size": W,
                    "threshold": T,
                    "mcl": Max Consecutive Losses,
                    "total_bets": Total Bets,
                    "win_rate": Win Rate,
                    "failure_score": 5연패 발생 횟수,
                    "is_passed": failure_score == 0
                }
            ],
            "optimal_combinations": [
                {"window_size": W, "threshold": T, ...}  # MCL < 5 만족하는 조합
            ]
        }
    """
```

**구현 단계**:
1. 전체 데이터를 시간 순서로 정렬 (`ORDER BY id` 또는 `created_at`)
2. 초기 학습 데이터: `total_count * 0.4`만큼 선택
3. 검증 데이터: 다음 `total_count * 0.1`만큼 선택
4. 각 (W, T) 조합에 대해:
   - 학습 데이터로 `stored_predictions_change_point` 생성 (최소 표본 수 필터 적용)
   - 검증 데이터에서 예측 수행 및 성과 측정
   - 검증 완료 후 학습 데이터에 검증 데이터 추가
5. 다음 10% 검증 데이터로 반복

### 2. 최소 표본 수 필터 적용

**수정 파일**: `change_point_prediction_module.py`

`save_or_update_predictions_for_change_point_data` 함수에 `min_sample_count` 파라미터 추가:

```python
def save_or_update_predictions_for_change_point_data(
    cutoff_grid_string_id=None,
    window_sizes=(5, 6, 7, 8, 9),
    methods=("빈도 기반",),
    thresholds=(0, 50, 60, 70, 80, 90, 100),
    batch_size=1000,
    min_sample_count=15,  # 신규 추가
):
    # ngram_chunks_change_point에서 prefix별 출현 횟수 집계
    # 15회 미만 패턴은 stored_predictions_change_point에 저장하지 않음
```

**쿼리 예시**:
```sql
SELECT prefix, COUNT(*) as occurrence_count
FROM ngram_chunks_change_point
WHERE window_size = ? AND grid_string_id IN (...)
GROUP BY prefix
HAVING occurrence_count >= 15
```

### 3. 예측 함수 수정

**수정 파일**: `change_point_prediction_module.py`

`get_multi_window_prediction_cp` 함수는 이미 `stored_predictions_change_point`를 조회하므로, 최소 표본 수 필터가 적용된 데이터만 조회됨 (자동 적용).

### 4. 성과 측정 로직

**파일**: `change_point/walk_forward_simulation_cp.py`

```python
def measure_performance(validation_results):
    """
    검증 결과에서 성과 지표 계산
    
    Args:
        validation_results: validate_multi_window_scenario_cp() 반환값
    
    Returns:
        dict: {
            "mcl": Max Consecutive Losses,
            "total_bets": Total Bets,
            "win_rate": Win Rate (%),
            "failure_score": 5연패 발생 횟수
        }
    """
    # consecutive_failures >= 5인 구간 카운트
    failure_score = count_five_consecutive_losses(validation_results["history"])
    return {
        "mcl": validation_results["max_consecutive_failures"],
        "total_bets": validation_results["total_predictions"],
        "win_rate": validation_results["accuracy"],
        "failure_score": failure_score
    }
```

### 5. UI 통합

**수정 파일**: `change_point/change_point_simulation_app.py`

새로운 섹션 추가:
- Walk-forward Analysis 모드 선택
- 윈도우 크기: 8, 9, 10, 11, 12 (체크박스)
- 임계값 범위: 50% ~ 65% (1% 단위)
- 최소 표본 수: 기본값 15 (조정 가능)
- 실행 버튼 및 결과 표시

**결과 표시 형식**:
- 각 (W, T) 조합별 성과 지표 테이블
- MCL < 5 만족하는 조합 하이라이트
- 가장 낮은 T 값 추천
- Failure Score = 0인 조합만 필터링 옵션

## 파일 구조

### 신규 생성 파일
- `change_point/walk_forward_simulation_cp.py`: Walk-forward Analysis 엔진

### 수정 파일
- `change_point_prediction_module.py`: 최소 표본 수 필터 추가
- `change_point/change_point_simulation_app.py`: UI 통합

## 데이터베이스 활용

### 기존 테이블
- `preprocessed_grid_strings`: 전체 데이터 (시간 순서)
- `ngram_chunks_change_point`: N-gram 청크 (prefix 출현 횟수 집계용)
- `stored_predictions_change_point`: Prefix-Suffix 확률 테이블 (최소 표본 수 필터 적용)

### 쿼리 패턴
```sql
-- 시간 순서로 데이터 로드
SELECT id, grid_string, created_at
FROM preprocessed_grid_strings
ORDER BY id;

-- Prefix 출현 횟수 집계
SELECT prefix, COUNT(*) as count
FROM ngram_chunks_change_point
WHERE window_size = ? AND grid_string_id IN (?)
GROUP BY prefix
HAVING count >= 15;
```

## 성능 최적화

1. **배치 처리**: 각 검증 구간마다 `stored_predictions_change_point` 재생성 (필요시)
2. **캐싱**: 동일한 학습 데이터로 여러 임계값 테스트 시 재사용
3. **병렬 처리**: 윈도우 크기별로 병렬 실행 가능 (선택사항)

## 검증 기준

- **합격 조건**: Failure Score = 0 (5연패 발생하지 않음)
- **최적 조합**: Failure Score = 0이면서 가장 낮은 T 값
- **보조 지표**: MCL, Total Bets, Win Rate (참고용)

## 주의사항

1. **최소 표본 수 (S_min = 15)**: 패턴이 15회 미만 출현한 경우 예측에서 제외
2. **동적 임계점**: 시뮬레이션 결과 W마다 최적 T가 다르다면, 실제 운영 시 앙상블(다수결) 고려
3. **시간 순서**: `created_at` 또는 `id` 기준으로 반드시 시간 순서 유지
4. **데이터 분할**: 초기 40%, 이후 10%씩 검증/업데이트 (마지막 구간은 나머지)
