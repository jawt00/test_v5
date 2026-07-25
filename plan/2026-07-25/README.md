# 2026-07-25 작업 계획 아카이브

이 폴더는 당일 스레드에서 작성·구현된 계획 문서를 보관합니다.

| 파일 | 요약 | 상태 |
|------|------|------|
| [list3_prediction_pipeline.plan.md](./list3_prediction_pipeline.plan.md) | list3(ws11/ws13) 예측 파이프라인 · compare 앱 독립 구현 | 완료 |
| [unified_livegame_app.plan.md](./unified_livegame_app.plan.md) | W9_10(L2) + W9_11(L3) 통합 라이브게임 TEST 앱 | 완료 |
| [final_table_confidence_schema.plan.md](./final_table_confidence_schema.plan.md) | 최종 테이블 rule_confidence 스키마 확장 (C안) | 완료 |

## 당일 추가 작업 (계획 외 UI)

- compare 앱(list2/list3): 타이틀 아래 가이드 영역 축소
- `livegame_unified_TEST.py`: UI 영역 축소, expander 기본 펼침

## 검증

```bash
python3 -m pytest tests/test_final_confidence.py -q
python3 change_point/verify_final_confidence.py --profile both
```
