# 가설: 윈도우 9 (빈도 51.8% + 승률 50%) 규칙 정리

**가설 키**: `first_anchor_window9_freq518_win50`  
**표시 이름**: 윈도우 9 (빈도 51.8% + 승률 50%)  
**검증 함수**: `validate_first_anchor_window9_freq518_win50_cp`

---

## 1. 검증 시작 및 앵커 진행

| 규칙 | 설명 |
|------|------|
| **첫 앵커부터 검증** | 전체 스트링에서 **첫 번째 앵커**(change-point 직후 위치)부터 검증을 시작한다. |
| **앵커 순차 이동** | 앵커는 인덱스 순서대로 처리한다. (`anchors[0]` → `anchors[1]` → …) |

---

## 2. 앵커당 검증 범위

| 규칙 | 설명 |
|------|------|
| **윈도우 9만 검증** | 각 앵커에서는 **window_size 9**만 사용한다. (10, 11, 12, 13, 14는 사용하지 않음) |

위치 계산: `pos = anchor + window_size - 1`  
- 윈도우 9 → `pos = anchor + 8`

---

## 3. 예측값 사용 조건 (미충족 시 스킵)

예측을 **사용**하려면 아래 조건을 **모두** 만족해야 한다. **한쪽이라도 불만족**하면 해당 스텝은 **스킵**한다.

| 조건 | 설명 |
|------|------|
| **(3a) 빈도 신뢰도 51.8% 이상** | `simulation_predictions_change_point`에서 해당 (window_size, prefix, threshold)의 **빈도 기반** 행의 `confidence` ≥ **51.8%**(또는 설정값). |
| **(3b) 시뮬레이션 승률 50% 이상** | 같은 행의 `sim_win_rate_pct` ≥ **50%**(또는 설정값). 테이블에 시뮬레이션 승률이 저장되어 있어야 한다. |
| **(3c) 스킵 처리** | 위 (3a)(3b) 중 하나라도 불만족 시 해당 (anchor, window_size, position)는 **예측으로 사용하지 않고 스킵**한다. `total_predictions`에 포함하지 않으며, history에는 `skipped=True` 및 `skip_reason`으로 기록한다. |

- 데이터 소스: `simulation_predictions_change_point`  
- **빈도 기반** 행만 사용한다. (가중치 기반 비교 없음)  
- 테이블 생성 시 `sim_win_rate_pct` 컬럼이 채워져 있어야 시뮬레이션 승률 조건을 적용할 수 있다.

---

## 4. 앵커 중첩 시 이전 앵커만 검증

| 규칙 | 설명 |
|------|------|
| **동일 position 중복 검증 금지** | 같은 **position**을 서로 다른 앵커가 커버할 수 있다. |
| **이전 앵커만 검증** | **먼저 도달하는(앵커 인덱스가 작은) 앵커**에서만 그 position을 검증한다. **다음 앵커**에서는 이미 검증된 position을 **다시 검증하지 않는다**. |
| **구현** | `validated_positions` set으로 이미 검증(또는 스킵)한 position을 기록하고, 새 (anchor, window_size)에서 계산한 `pos`가 이미 set에 있으면 해당 스텝을 **수행하지 않는다** (total_steps·history에 포함하지 않음). |

---

## 5. 요약 표

| # | 규칙 | 요약 |
|---|------|------|
| 1 | 검증 시작·진행 | 첫 앵커부터 순차 이동 |
| 2 | 검증 범위 | 앵커당 윈도우 9만 검증 |
| 3 | 예측 사용 조건 | 빈도 신뢰도 ≥ 51.8% **및** 시뮬레이션 승률 ≥ 50%; 한쪽이라도 불만족 시 스킵 |
| 4 | 앵커 중첩 | 같은 position은 이전 앵커에서만 검증, 다음 앵커에서는 생략 |

---

## 6. 관련 코드

- **검증 함수**: `change_point_hypothesis_module.validate_first_anchor_window9_freq518_win50_cp`
- **배치 검증**: `change_point_hypothesis_module.batch_validate_first_anchor_window9_freq518_win50_cp`
- **가설 클래스**: `FirstAnchorWindow9Freq518Win50Hypothesis`
- **테스트 앱**: Change-point 가설 테스트 앱에서 가설 "윈도우 9 (빈도 51.8% + 승률 50%)" 선택 후 테이블 생성(시뮬레이션 승률 포함), 시뮬레이션 실행

---

## 7. 스킵 사유 (skip_reason) 예시

- `"예측 테이블에 값 없음"` — 해당 (window_size, prefix, threshold)에 행이 없음  
- `"빈도 기반 없음"` — 빈도 기반 method 행만 없음 (다른 method만 존재)  
- `"신뢰도 부족 (빈도 X% < 51.8%)"` — 빈도 기반 confidence가 51.8% 미만  
- `"시뮬레이션 승률 없음"` — 해당 행에 `sim_win_rate_pct`가 NULL  
- `"시뮬레이션 승률 부족 (X% < 50%)"` — `sim_win_rate_pct`가 50% 미만

---

## 8. 파라미터 (hypothesis_params)

- `threshold_freq`: 빈도 신뢰도 최소값 (기본 51.8)
- `min_win_rate_pct`: 시뮬레이션 승률 최소값 (기본 50)
