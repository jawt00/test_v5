---
name: live-game-prefix-rules-v2-redesign
overview: Streamlit 기반 라이브 게임 Prefix 규칙 앱(v2)을 승무패 기록 및 라운드 관리용으로 단순화/변경
todos:
  - id: init-state-v2
    content: v2에서 필요한 세션 상태(prefix_rules_grid_string, prefix_rules_rounds 등) 정의 및 초기화
    status: completed
  - id: simplify-grid-flow
    content: Grid String 시작 이후 히스토리/테일/라이브 업데이트 로직 제거하고 Grid/앵커까지만 표시
    status: completed
  - id: implement-prefix-match
    content: 끝에서부터 윈도우 스캔으로 prefix 매칭 1개 찾는 로직 구현 및 결과 표시 UI
    status: completed
  - id: round-record-ui
    content: prefix, 예측값, 실제값, 승/무/패 기록 UI와 세션 상태(prefix_rules_rounds) 저장 로직
    status: completed
  - id: round-summary-table
    content: 최대 8회차까지 라운드 요약 테이블 UI (회차, prefix, 예측값, 실제값, 결과)
    status: completed
  - id: reset-behavior
    content: Grid만 초기화 / 전체 라운드 초기화 버튼 분리
    status: completed
  - id: odds-optional
    content: (선택) 라운드 테이블에 배당/수익 컬럼 추가
    status: pending
isProject: false
---

## 목표

- `live_game_prefix_rules_v2.py`만 수정해서, **Prefix 규칙 관리 UI는 유지**하고, Grid String 입력 이후의 흐름을 **승무패 라운드 기록용**으로 재구성한다.
- Grid/앵커까지만 시각화하고, 히스토리/테일/라이브 업데이트 로직은 제거한다.
- 불러오기된 규칙 리스트에서 **끝에서부터 윈도우로 쪼개어 가장 먼저 발견되는 prefix 1개**를 찾고, **예측 vs 실제(segment 다음 글자)**로 승/패를 자동 판정하며, 최대 8회차까지 라운드 기록을 관리한다.

## 변경 대상 파일

- `[change_point/live_game_prefix_rules_v2.py](change_point/live_game_prefix_rules_v2.py)`

---

## 오늘 작업 및 변경 사항 (반영 완료)

- **규칙 구조**: 계획서에 있던 “전체 패턴 문자열을 prefix+suffix로 정제”하는 방식은 **적용하지 않음**. JSON/추가 시 **원본 `prefix`·`prediction` 필드 그대로** 사용한다. (예: `momentum_221312.json` 형식 그대로 사용)
- **prefix 매칭 알고리즘**:
  - 입력 스트링을 **끝 인덱스부터 0까지** `end`를 감소시키며 순환.
  - 각 `end`에서 규칙에 등장하는 **prefix 길이(윈도우)**를 **긴 것 우선**으로 적용해 `segment = grid_string[start:end+1]` 생성.
  - `segment`가 어떤 규칙의 `prefix`와 **완전 일치**하면 그 규칙을 “가장 먼저 발견된” 매칭으로 선택하고 종료.
  - **actual(실제값)** = segment 바로 다음 글자 (`end+1`; 끝이면 `grid_string[-1]`).
  - **승/패** = `prediction == actual` 이면 승, 아니면 패 (UI에서 무는 선택 가능).
- **초기화**: **Grid만 초기화** / **전체 라운드 초기화** 버튼 분리 구현됨.
- **라운드**: `prefix_rules_rounds`에 `round_index`, `prefix`, `prediction`, `actual`, `result` 저장, 최대 8회차 제한 및 요약 테이블 표시.

---

## 상세 계획 (현재 구현 기준)

- **Prefix 규칙 섹션 유지**
  - 상단의 `Prefix 규칙 (추가·삭제만 가능, prefix 순 정렬)` 섹션은 기존과 동일.
  - 규칙 추가/삭제, JSON 저장/불러오기, 필터 조건 표시 유지. **불러오기·추가 시 정제 없이 원본 prefix/prediction 사용.**

- **Grid String 처리 흐름**
  - **유지**: Grid String 입력, `🎮 시작`으로 b/p만 남겨 세션 저장, `_anchors_from_grid_string` + `render_grid_string_and_anchors`로 Grid/앵커 시각화.
  - **제거됨**: 히스토리/테일/라이브 업데이트 관련 호출 및 UI.

- **끝부분 prefix 매칭 (구현된 방식)**
  - `find_matching_prefix_at_tail(grid_string, rules)` 사용.
  - 규칙은 **원본 그대로**: `rule["prefix"]`, `rule["prediction"]` (길이 7·8·9 등 제한 없음).
  - **스캔 순서**: `end`를 `len(s)-1` → `0`으로 감소. 각 `end`에서 규칙에 등장하는 prefix 길이 집합을 **내림차순**으로 적용해 윈도우로 사용.
  - `segment = grid_string[start:end+1]` (start = end - window + 1)가 어떤 규칙의 prefix와 **완전 일치**하면 해당 규칙 선택, `actual = grid_string[end+1]`(끝이면 `grid_string[-1]`), `result = "승" if prediction == actual else "패"`.
  - 반환: `{ "prefix", "prediction", "actual", "result", "start", "end" }` 또는 None.

- **승무패 라운드 기록**
  - `st.session_state.prefix_rules_rounds` 리스트, 원소: `{ "round_index", "prefix", "prediction", "actual", "result" }`.
  - 매칭 결과(prefix, 예측값, 실제값, 결과) 읽기 전용 표시 후, 결과는 예측 vs 실제로 기본 설정되며 **무**로 변경 가능.
  - “이 Grid를 라운드로 추가”로 현재 매칭을 라운드로 추가, 최대 8회차까지.

- **배당(odds)**
  - `ODDS_P`, `ODDS_B`, `ODDS_DRAW` 상수 정의만 되어 있음. 테이블에 배당/수익 컬럼 추가는 선택 사항(미구현).

- **초기화**
  - **Grid만 초기화**: `prefix_rules_grid_string`, `prefix_rules_current_match`만 초기화, 라운드는 유지.
  - **전체 라운드 초기화**: `prefix_rules_rounds` 비우기.

---

## 산출물 (현재 상태)

- 수정된 `live_game_prefix_rules_v2.py`:
  - 상단 Prefix 규칙 관리 UI 유지, **규칙 원본(prefix/prediction) 그대로 사용.**
  - Grid String 입력 후 Grid/앵커까지만 시각화.
  - **끝에서부터 윈도우 스캔**으로 prefix 매칭 1개 선택, 예측 vs 실제로 승/패 자동 판정 + 무 선택 가능.
  - 최대 8회차 라운드 요약 테이블.
  - Grid만 초기화 / 전체 라운드 초기화 버튼 분리.

- **선택적 남은 작업**: 라운드 테이블에 배당(및 수익) 컬럼 추가.
