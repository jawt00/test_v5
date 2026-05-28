---
name: Change-point 임계점 스킵 라이브게임 구현
overview: Change-point Detection 기반으로 스텝별 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임 앱 구현
todos:
  - id: "1"
    content: change_point_live_game_app.py 생성 - 임계점 스킵 + 앵커 우선순위 라이브 게임 앱 구현
    status: completed
  - id: "2"
    content: 앵커 우선순위 로직 구현 - 작은 앵커 우선순위 적용
    status: completed
  - id: "3"
    content: 디버깅 정보 표시 기능 구현 - prefix, 앵커, 윈도우, 예측값, 신뢰도, 탈락 사유 표시
    status: completed
  - id: "4"
    content: 게임 재시작 버튼 추가 - Grid String 입력 섹션에 게임 재시작 기능 구현
    status: completed
isProject: false
---

# Change-point 임계점 스킵 라이브게임 구현 계획

## 목표

Change-point Detection 기반으로 스텝별 예측값을 확인하고 실제값을 입력하여 검증하는 라이브 게임 앱을 구현합니다.

## 주요 기능

### 1. 게임 설정
- **윈도우 크기 선택**: 8, 9, 10, 11, 12 중 개별 선택 가능
- **윈도우별 임계값 설정**: 각 윈도우 크기별로 50-65 범위에서 임계값 설정
- **예측 방법**: 빈도 기반, 가중치 기반 등
- **Cutoff ID**: 학습 데이터와 검증 데이터 구분

### 2. Grid String 입력
- **Text Area 입력**: `st.text_area` 사용 (height=80)
- **게임 시작 버튼**: 설정 검증 후 게임 시작
- **게임 재시작 버튼**: 진행 중인 게임 초기화

### 3. 예측 로직
- **Change-point Detection**: 변화점 감지 및 앵커 위치 수집
- **앵커 우선순위**: 작은 앵커 우선순위 적용
- **임계값 스킵**: confidence가 임계값 미만인 경우 스킵
- **예측 포지션**: grid_string 길이와 동일한 포지션 (다음 예측할 포지션)

### 4. UI 구성
- **게임 통계**: 현재 Step, 최대 연속 불일치, 총 예측 횟수, 스킵 횟수
- **Grid String 시각화**: 앵커 위치(연한 파란색), 현재 예측 포지션(노란색), 선택된 앵커(빨간색 테두리)
- **현재 스텝 정보**: Prefix, 예측값, 신뢰도, Anchor/Window
- **실제값 선택**: B/P 버튼 및 취소 버튼
- **상세 히스토리**: 모든 스텝의 예측값, 실제값, 일치 여부, 신뢰도 등
- **디버깅 정보**: 앵커 선택 과정, prefix, 탈락 사유 등

## 기술 스택

- **Streamlit**: 웹 UI 프레임워크
- **SQLite**: 예측값 저장 및 조회 (`stored_predictions_change_point` 테이블)
- **Change-point Detection**: 변화점 기반 앵커 위치 감지

## 파일 구조

```
change_point/
  ├── change_point_live_game_app.py          # 라이브 게임 앱 (메인)
  ├── change_point_hypothesis_module.py     # 가설 모듈 (ThresholdSkipAnchorPriorityHypothesis)
  └── change_point_prediction_module.py     # 예측 모듈 (DB 조회 함수)
```

## 핵심 로직

### 1. 예측 포지션 계산
```python
# 다음 예측할 포지션 = grid_string 길이
next_position = len(grid_string)
```

### 2. 앵커 우선순위
```python
# 작은 앵커 우선순위
possible_anchors = sorted(possible_anchors)  # 작은 순서대로
```

### 3. 예측값 조회
```python
# prefix 계산
prefix = grid_string[position - prefix_len : position]

# DB 조회
SELECT predicted_value, confidence
FROM stored_predictions_change_point
WHERE window_size = ? AND prefix = ? AND method = ? AND threshold = 0

# 임계값 확인
if confidence >= ws_threshold:
    # 예측값 사용
```

### 4. 앵커 선택 로직
1. 가능한 앵커들을 작은 순서대로 정렬
2. 각 앵커에 대해 모든 윈도우 크기 시도
3. 예측값이 있고 임계값 이상인 경우 선택
4. 선택된 앵커에서 성공하면 더 이상 시도하지 않음

## 디버깅 정보

### 표시 항목
- **Position**: 예측할 포지션
- **앵커**: 시도한 앵커 위치
- **윈도우**: 시도한 윈도우 크기
- **Prefix**: 계산된 prefix
- **예측값**: 조회된 예측값
- **신뢰도**: 예측값의 신뢰도
- **결과**: 선택됨 / 임계값 미만 / DB에 예측값 없음 / 이전 앵커에서 성공

## 사용 방법

1. **게임 설정**
   - 윈도우 크기 선택 (8, 9, 10, 11, 12)
   - 각 윈도우별 임계값 설정 (50-65)
   - 예측 방법 선택
   - Cutoff ID 설정

2. **Grid String 입력**
   - 라이브 게임에서 사용할 grid_string 입력
   - "게임 시작" 버튼 클릭

3. **게임 진행**
   - 예측값 확인
   - 실제값 입력 (B/P)
   - 다음 스텝으로 진행

4. **게임 재시작**
   - "게임 재시작" 버튼으로 게임 초기화

## 참고 파일

- `change_point/change_point_live_game_app.py`: 메인 앱 파일
- `change_point/change_point_hypothesis_module.py`: ThresholdSkipAnchorPriorityHypothesis 구현
- `change_point/WORK_LOG.md`: 작업 로그
- `live_game_app_parallel.py`: 참고 앱 (UI 구조 참고)

## 주요 개선 사항

1. **예측 포지션 로직 수정**: `current_position + 1` → `len(grid_string)` (아직 입력되지 않은 다음 포지션)
2. **앵커 우선순위 수정**: 큰 앵커 우선 → 작은 앵커 우선 (이전 앵커 우선순위)
3. **디버깅 정보 개선**: prefix 정보 추가, DB 조회 실패/임계값 미만 구분
4. **게임 재시작 기능**: Grid String 입력 섹션에 게임 재시작 버튼 추가
