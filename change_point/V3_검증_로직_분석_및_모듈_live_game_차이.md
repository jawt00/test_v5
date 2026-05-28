# V3 검증 로직 분석 및 모듈 vs live_game 차이점

## 1. V3 검증 로직 핵심 요약

### 1.1. 요구사항
- **[REQ-101]** `current_pos` 이후의 가장 빠른 앵커를 검증 대상으로 선정
- **[REQ-102]** 윈도우 크기 9, 10, 11, 12, 13, 14 순차 검증
- **[RULE-1]** 적중 시 즉시 종료 → `current_pos = matched_pos + 1`, 다음 앵커 탐색
- **[RULE-2]** 3회 연속 불일치 시 앵커 실패 → `current_pos = mismatched_pos + 1`, 다음 앵커 탐색
- **스킵**: 예측 테이블에 값 없음 → 연속 실패 카운트에 포함하지 않음

### 1.2. 모듈 `validate_first_anchor_extended_window_v3_cp` 핵심 흐름

```
current_pos = 0, anchor_idx = 0

while current_pos < len(grid_string) and anchor_idx < len(anchors):
    # [REQ-101] current_pos 이후 첫 앵커 찾기
    while anchor_idx < len(anchors) and anchors[anchor_idx] < current_pos:
        anchor_idx += 1
    
    next_anchor = anchors[anchor_idx]
    
    for window_size in (9,10,11,12,13,14):
        pos = next_anchor + window_size - 1
        if pos >= len(grid_string): break
        if pos < current_pos: continue
        
        # 스킵: current_pos 업데이트 없음, continue
        # 적중: current_pos=pos+1, anchor_idx+=1, break
        # 3연속 실패: current_pos=last_mismatched_pos+1, anchor_idx+=1, break
    
    # for 루프 끝 (성공/3패 없음): current_pos 업데이트, anchor_idx += 1
```

---

## 2. 모듈 vs live_game cold_start 차이점 (이슈)

### 차이 1: 스킵 시 current_pos 업데이트

| | 모듈 | live_game |
|---|-----|-----------|
| **동작** | `current_pos` 업데이트 **하지 않음** | `current_pos = max(current_pos, pos + 1)` |
| **위치** | `change_point_hypothesis_module.py` L2516-2533 | `change_point_live_game_app_flow.py` L134-138 |
| **영향** | 스킵 후에도 같은 앵커의 다음 윈도우 계속 검증 | 스킵 시 current_pos가 진행되어 이후 `pos < current_pos`로 일부 포지션이 건너뛰어질 수 있음 |

**모듈 (정답):**
```python
if len(df_pred) == 0:
    total_skipped += 1
    history.append(...)
    continue  # current_pos 변경 없음
```

**live_game (수정 필요):**
```python
if len(df_pred) == 0:
    total_skipped += 1
    current_pos = max(current_pos, pos + 1)  # ← 제거 필요
    history.append(...)
    continue
```

---

### 차이 2: 윈도우 루프 종료 후 다음 앵커 선택 (REQ-101 위반)

| | 모듈 | live_game |
|---|-----|-----------|
| **동작** | `anchor_idx += 1` 후, 다음 while에서 `anchors[anchor_idx] >= current_pos`인 첫 앵커로 수렴 | `anchor_idx += 1`만 수행 → **리스트의 다음 앵커** (current_pos 무시) |
| **위치** | `change_point_hypothesis_module.py` L2595-2616 | `change_point_live_game_app_flow.py` L192-198 |

**예시 (불일치 발생):**
- `anchors = [0, 5, 10]`, `current_pos = 11` (앵커 0에서 2패 후 윈도우 소진)
- 모듈: `anchor_idx += 1` → 1, 다음 while에서 `anchors[1]=5 < 11` → `anchor_idx=2` → **앵커 10** 사용
- live_game: `anchor_idx += 1` → 1 → **앵커 5** 사용 (잘못됨)

**모듈:** `anchor_idx += 1`이지만 while 시작 시 `anchors[anchor_idx] < current_pos`인 동안 계속 증가시키므로, 결과적으로 **current_pos 이후 첫 앵커**를 선택함.

**live_game (수정 필요):** `anchor_idx = _first_anchor_from_position(anchors, current_pos)`로 변경해야 함.

```python
# 현재 (잘못됨)
if not anchor_success and fail_count < MAX_CONSECUTIVE_FAILURES:
    if last_mismatched_pos is not None:
        current_pos = last_mismatched_pos + 1
    anchor_idx += 1  # ← REQ-101 위반

# 수정 후
if not anchor_success and fail_count < MAX_CONSECUTIVE_FAILURES:
    if last_mismatched_pos is not None:
        current_pos = last_mismatched_pos + 1
    anchor_idx = _first_anchor_from_position(anchors, current_pos)
```

---

### 차이 3: 문자열 끝 처리 시 current_pos

| | 모듈 | live_game |
|---|-----|-----------|
| **동작** | `break`만, current_pos는 for 내부에서 변경 안 함 | `current_pos = len(grid_string)` 설정 |
| **영향** | 다음 while 조건 `current_pos < len(grid_string)`로 루프 종료 | 동일하게 루프 종료. live_game은 current_pos를 명시적으로 끝으로 설정 |

이 부분은 기능적으로 동일한 종료 결과를 내므로 큰 이슈는 아님.

---

### 차이 4: synthetic 앵커

- **live_game**: `anchor_idx >= len(anchors)`일 때 `anchors.append(current_pos)`로 synthetic 앵커 추가
- **모듈**: 그냥 `break`

live_game은 실시간 피드백용으로 "다음 앵커 대기" 상태를 표현하기 위해 synthetic 앵커를 사용. Cold Start의 전수 검증 결과와 모듈을 비교할 때는, Cold Start가 끝나기 전까지는 synthetic 앵커가 추가되지 않을 수 있음. 검증 결과 동일성 관점에서는 Cold Start가 완료될 때까지의 history/summary 비교가 중요.

---

## 3. 수정 권장 사항

1. **스킵 시 `current_pos` 업데이트 제거**  
   - `change_point_live_game_app_flow.py` 136행: `current_pos = max(current_pos, pos + 1)` 삭제

2. **윈도우 루프 종료 후 앵커 선택을 REQ-101에 맞게 수정**  
   - `anchor_idx += 1` 대신 `anchor_idx = _first_anchor_from_position(anchors, current_pos)` 사용

이 두 가지 수정으로 cold_start 검증 결과가 모듈의 `validate_first_anchor_extended_window_v3_cp`와 동일해져야 함.
