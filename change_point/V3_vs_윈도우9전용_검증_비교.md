# V3 (9–14) vs 윈도우 9 전용 검증 프로세스 비교

## 1. 요약: 무엇이 같고 무엇이 다른지

| 구분 | V3 (9–14) | 윈도우 9 전용 |
|------|------------|----------------|
| **예측 테이블** | `simulation_predictions_change_point` | 동일 |
| **시뮬레이션 테이블 생성 함수** | `generate_simulation_predictions_table()` | 동일 함수 사용 가능 |
| **테이블 생성 시 윈도우** | 기본 `(9,10,11,12,13,14)` 인자로 생성 | **별도 설정 없음** → 앱에서 V3와 동일 호출 안 함 |
| **검증 시 읽는 윈도우** | 9 → 10 → 11 → 12 → 13 → 14 순차 | **9만** |
| **앵커 정의** | Change-point 동일 | 동일 |
| **앵커 순서** | current_pos 이후 첫 앵커부터 순서대로 | 동일 |
| **앵커당 검증 횟수** | 1~6회 (윈도우별, RULE-1/2에 따라) | **항상 1회** (윈도우 9만) |
| **종료 후 다음** | 다음 앵커로 이동 | 동일 (해당 앵커 종료 → 다음 앵커, 전체 스트링 아님) |
| **검증 범위** | 전체 스트링의 모든 앵커 | **전체 스트링의 모든 앵커** (첫 앵커만 아님) |

---

## 2. 시뮬레이션 설정 비교

### 2.1 공통: 테이블 생성

둘 다 **같은 테이블**을 씁니다.

- 테이블명: `simulation_predictions_change_point`
- 생성: `create_simulation_predictions_change_point_table()`  
  + `save_predictions_to_simulation_table(...)`  
  → 래퍼: **`generate_simulation_predictions_table(cutoff_grid_string_id, window_sizes=..., method=..., threshold=..., min_sample_count=15)`**

### 2.2 V3 (9–14) 설정

- **호출 위치:** `change_point_hypothesis_test_app.py`  
  - 가설 선택이 `first_anchor_extended_window_v3` 일 때만  
    “윈도우 크기 (9–14)” UI + **“예측값 테이블 생성”** 버튼 노출
- **실제 호출:**
  ```text
  generate_simulation_predictions_table(
      cutoff_grid_string_id=cutoff_sim,
      window_sizes=tuple(ws),   # ws = [9,10,11,12,13,14] (사용자 선택)
      method=method_sim,
      threshold=thresh_sim,
  )
  ```
- **결과:** 테이블에 **window_size 9, 10, 11, 12, 13, 14** 전부에 대한 예측이 들어감.

### 2.3 윈도우 9 전용 설정

- **호출 위치:**  
  - `change_point_hypothesis_test_app.py` 에는 **`first_anchor_window9_only` 전용 UI/분기가 없음**
  - 따라서 “예측값 테이블 생성” 버튼도 없고,  
    `generate_simulation_predictions_table` 를 **윈도우 9 전용으로 호출하는 코드도 없음**
- **실제 동작:**
  - 다른 일반 가설과 같은 “윈도우 크기” UI (5,6,7,8 등)만 있음
  - 단일 테스트 시 `first_anchor_window9_only` 이면  
    `batch_validate_hypothesis_cp(...)` 로 빠지며,  
    **`batch_validate_first_anchor_window9_only_cp` 는 앱에서 호출되지 않음**
- **결과:**  
  - 시뮬레이션 설정이 **V3와 다르게** 됨  
    (V3는 9–14로 테이블 생성 후 검증, 윈도우 9 전용은 그런 전제가 앱에 없음)

### 2.4 정리: “시뮬레이션 설정부터 다르다”는 점

- **V3:**  
  “9–14 선택 → 예측값 테이블 생성 → (같은 ws로) V3 배치 검증” 이 한 프로세스로 앱에 들어가 있음.
- **윈도우 9 전용:**  
  - 같은 테이블(`simulation_predictions_change_point`)을 쓰지만,  
  - 앱에서 **동일한 시뮬레이션 설정(테이블 생성)을 거치지 않음**  
  - 전용 배치 검증(`batch_validate_first_anchor_window9_only_cp`)도 앱에서 호출되지 않음.

즉, “검증하는 프로세스는 동일해야 하는데” 요구사항 대비해서는  
**시뮬레이션 설정(테이블 생성 단계)이 V3와 동일하게 가지 않도록 되어 있지 않고**,  
그래서 **설정부터 다르다**고 볼 수 있음.

---

## 3. 검증 실행 비교 (함수/배치)

### 3.1 V3 (9–14)

**배치**

- `batch_validate_first_anchor_extended_window_v3_cp(
    cutoff_grid_string_id,
    window_sizes=(9, 10, 11, 12, 13, 14),
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
  )`
- 단일: `validate_first_anchor_extended_window_v3_cp(grid_string_id, cutoff_grid_string_id, window_sizes=(9,...,14), method, threshold, stop_on_match)`

**단일 검증 로직 요약**

1. `preprocessed_grid_strings` 에서 `grid_string` 조회
2. Change-point로 앵커 수집: `grid_string[i] != grid_string[i+1]` 인 `i`
3. `current_pos = 0`, `anchor_idx = 0`
4. **앵커 루프**  
   - [REQ-101] `current_pos` 이후 첫 앵커: `next_anchor = anchors[anchor_idx]` (이미 `anchors[anchor_idx] >= current_pos` 되도록 인덱스 진행)
   - [REQ-102] **윈도우 루프** `for window_size in (9, 10, 11, 12, 13, 14)`:
     - `pos = next_anchor + window_size - 1`
     - `pos >= len(grid_string)` 이면 break
     - `pos < current_pos` 이면 continue
     - `prefix = grid_string[pos - (window_size-1) : pos]`
     - **쿼리:**  
       `simulation_predictions_change_point`  
       `WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = ?`
     - 예측 없음 → 스킵(히스토리만 기록), 연속 실패로 안 셈, 다음 윈도우로
     - 예측 있음 → `predicted` vs `actual = grid_string[pos]` 비교  
       - **RULE-1 (적중):** `current_pos = pos + 1`, `anchor_idx += 1`, **현재 앵커 종료(break)**  
       - **RULE-2 (불일치):** `anchor_consecutive_failures += 1`  
         - `>= 3` 이면 `current_pos = last_mismatched_pos + 1`, `anchor_idx += 1`, **현재 앵커 종료(break)**  
         - 아니면 다음 `window_size` 로 계속
5. 반환 구조: `grid_string_id`, `max_consecutive_failures`, `total_steps`, `total_failures`, `total_predictions`, `total_skipped`, `accuracy`, `history`, `stopped_early`

### 3.2 윈도우 9 전용

**배치**

- `batch_validate_first_anchor_window9_only_cp(
    cutoff_grid_string_id,
    method="빈도 기반",
    threshold=0,
    stop_on_match=False,
  )`
- **`window_sizes` 인자 없음** (내부 고정 9)

**단일 검증 로직 요약**

1. `preprocessed_grid_strings` 에서 `grid_string` 조회
2. 앵커 수집: V3와 동일 (Change-point)
3. `current_pos = 0`, `anchor_idx = 0`, `WINDOW_SIZE = 9`
4. **앵커 루프**  
   - `current_pos` 이후 첫 앵커: `next_anchor = anchors[anchor_idx]` (인덱스 진행 방식 동일)
   - **윈도우 9 한 번만:**
     - `pos = next_anchor + 8`
     - `pos >= len(grid_string)` 이면 `anchor_idx += 1`, continue
     - `pos < current_pos` 이면 `anchor_idx += 1`, continue
     - `prefix = grid_string[pos - 8 : pos]`
     - **쿼리:**  
       `simulation_predictions_change_point`  
       `WHERE window_size = 9 AND prefix = ? AND method = ? AND threshold = ?`
     - 예측 없음 → 스킵 기록, **`current_pos = pos + 1`, `anchor_idx += 1`** (한 스텝으로 앵커 종료)
     - 예측 있음 → 비교 후 히스토리 기록, **`current_pos = pos + 1`, `anchor_idx += 1`** (한 스텝으로 앵커 종료)
   - RULE-2(3연속 불일치) 없음. **앵커당 항상 1회 검증 후 다음 앵커**
5. 반환 구조: V3와 동일 필드

### 3.3 같은 점

- 사용 테이블: `simulation_predictions_change_point`
- 조회 조건: `(window_size, prefix, method, threshold)`
- 앵커 정의 및 정렬
- “current_pos 이후 첫 앵커” 선택
- `current_pos`, `anchor_idx` 로 다음 앵커로 넘어가는 방식
- `history` 항목 구조 (step, position, anchor, window_size, prefix, predicted, actual, is_correct, confidence, skipped 등)

### 3.4 다른 점

| 항목 | V3 (9–14) | 윈도우 9 전용 |
|------|------------|----------------|
| 배치 함수 인자 | `window_sizes=(9,10,11,12,13,14)` 있음 | `window_sizes` 없음 (내부 9 고정) |
| 앵커당 시도 윈도우 | 9 → 10 → … → 14 순차 | 9만 |
| 앵커당 검증 횟수 | 1~6회 (적중/3연속 실패 시 종료) | 항상 1회 |
| RULE-1 | 적중 시 해당 앵커 종료 | (동일 개념: 1회만 하므로 그 1회가 곧 종료) |
| RULE-2 | 3연속 불일치 시 앵커 종료 | 없음 (이미 1회만 수행) |

---

## 4. 앱(테스트 앱) 실행 경로 비교

### 4.1 V3 (9–14)

1. 가설 선택: `first_anchor_extended_window_v3`
2. UI: 윈도우 9–14 체크, 임계값, **“예측값 테이블 생성”** 버튼
3. 테이블 생성: `generate_simulation_predictions_table(cutoff_sim, window_sizes=tuple(ws), ...)`  
   → `ws` = [9,10,11,12,13,14]
4. 검증 실행:  
   `batch_validate_first_anchor_extended_window_v3_cp(cutoff_sim, window_sizes=tuple(ws), method_sim, thresh_sim)`

### 4.2 윈도우 9 전용

1. 가설 선택: `first_anchor_window9_only`
2. UI: **V3 전용 UI가 없음** → 다른 가설과 같은 “윈도우 크기”(5,6,7,8 등)만 있음  
   → “예측값 테이블 생성” 버튼 없음
3. 테이블 생성: **앱에서 호출하지 않음** (직접 스크립트로 호출해야 함)
4. 검증 실행:  
   - 단일 테스트 시 `first_anchor_window9_only` 이면 **else** 분기로 가서  
     `batch_validate_hypothesis_cp(cutoff_sim, hypothesis=get_hypothesis("first_anchor_window9_only"), window_sizes=tuple(ws), ...)`  
     → 여기서 `ws` 는 위 일반 UI에서 고른 5,6,7,8 등이라 **9가 포함되지 않을 수 있음**
   - `batch_validate_multiple_train_ratios(..., hypothesis="first_anchor_window9_only")` 를 쓰는 경로에서는  
     모듈 내부에서 `batch_validate_first_anchor_window9_only_cp` 가 호출됨 (시뮬 설정은 여전히 앱에서 안 함)

즉, **같은 “검증 프로세스”로 보려면**  
- 시뮬레이션 설정: **동일한 `generate_simulation_predictions_table(..., window_sizes=(9,10,11,12,13,14))`** 한 번 실행  
- 검증: V3는 `batch_validate_first_anchor_extended_window_v3_cp(..., window_sizes=(9,...,14))`,  
  윈도우 9 전용은 `batch_validate_first_anchor_window9_only_cp(cutoff, method, threshold)`  
으로 두면, **테이블은 동일하고, 검증 시 읽는 윈도우만 9 하나로 제한**하는 형태로 맞출 수 있음.  
현재는 **앱 쪽에서 시뮬레이션 설정부터 V3와 동일하게 타지 않음**이 차이입니다.

---

## 5. 정리 표

| 구분 | V3 (9–14) | 윈도우 9 전용 | 동일화하려면 |
|------|------------|----------------|--------------|
| 시뮬 테이블 | `simulation_predictions_change_point` | 동일 | 그대로 |
| 테이블 생성 함수 | `generate_simulation_predictions_table(..., window_sizes=(9..14))` | 앱에서 미호출 | 윈도우 9 전용도 **같은 함수·같은 window_sizes**로 한 번 생성하면 됨 |
| 검증 시 읽는 윈도우 | 9,10,11,12,13,14 순차 | 9만 | 검증 로직 차이 (의도된 것) |
| 앵커당 스텝 수 | 1~6 (RULE-1/2) | 1 | 검증 로직 차이 (의도된 것) |
| 앱에서 전용 UI/버튼 | 있음 (9–14 + 테이블 생성) | 없음 | 윈도우 9 전용도 “예측값 테이블 생성” 등 **동일 프로세스**로 넣을지 여부는 코드 수정 시 결정 |

이 문서는 비교·확인용이며, 코드 수정은 비교 확인 후 진행하시면 됩니다.
