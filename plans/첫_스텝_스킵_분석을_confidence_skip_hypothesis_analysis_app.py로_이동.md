---
name: 첫 스텝 스킵 분석을 confidence_skip_hypothesis_analysis_app.py로 이동
overview: interactive_multi_step_validation_app.py에 추가한 "첫 스텝 스킵 분석" 기능을 confidence_skip_hypothesis_analysis_app.py로 이동하고, interactive_multi_step_validation_app.py에서 제거
todos: []
---

## 목표

- `interactive_multi_step_validation_app.py`에 추가한 "첫 스텝 스킵 분석" 코드를 `confidence_skip_hypothesis_analysis_app.py`로 이동
- `interactive_multi_step_validation_app.py`에서 해당 코드 제거

## 작업 내용

### 1. confidence_skip_hypothesis_analysis_app.py에 필요한 함수 추가

**필요한 함수들:**

- `validate_interactive_multi_step_scenario_with_confidence_skip_first_step_analysis()` - 검증 함수
- `batch_validate_with_first_step_skip_analysis()` - 배치 검증 함수
- `save_first_step_skip_analysis_results()` - 저장 함수
- `analyze_first_step_skip_correlation()` - 분석 함수

**필요한 helper 함수들 (interactive_multi_step_validation_app.py에서 import):**

- `load_ngram_chunks`
- `build_frequency_model`
- `build_weighted_model`
- `predict_for_prefix`
- `predict_with_fallback_interval`
- `get_next_prefix`
- `uuid` (import uuid)

### 2. confidence_skip_hypothesis_analysis_app.py의 main 함수에 UI 추가

- 기존 가설 검증 분석 섹션 다음에 "첫 스텝 스킵 분석" 섹션 추가
- 설정 폼과 분석 실행 버튼
- 분석 결과 표시 (승률 비교, 이상치 발생 비율 비교 등)

### 3. interactive_multi_step_validation_app.py에서 코드 제거

- `validate_interactive_multi_step_scenario_with_confidence_skip_first_step_analysis()` 함수 제거
- `batch_validate_with_first_step_skip_analysis()` 함수 제거
- `save_first_step_skip_analysis_results()` 함수 제거
- `analyze_first_step_skip_correlation()` 함수 제거
- main 함수에서 "첫 스텝 스킵 분석" UI 섹션 제거 (5308-5500줄 근처)

## 파일 구조

### confidence_skip_hypothesis_analysis_app.py

```
imports (기존 + 추가)
  - from interactive_multi_step_validation_app import (
      load_ngram_chunks,
      build_frequency_model,
      build_weighted_model,
      predict_for_prefix,
      predict_with_fallback_interval,
      get_next_prefix
  )
  - import uuid

함수들 (기존 + 추가)
  - validate_interactive_multi_step_scenario_with_confidence_skip_first_step_analysis()
  - batch_validate_with_first_step_skip_analysis()
  - save_first_step_skip_analysis_results()
  - analyze_first_step_skip_correlation()

main() 함수
  - 기존 가설 검증 분석 섹션
  - 첫 스텝 스킵 분석 섹션 (새로 추가)
```

### interactive_multi_step_validation_app.py

- 위 4개 함수 제거
- main 함수에서 첫 스텝 스킵 분석 UI 섹션 제거
