# 슬라이딩 스케일 조건 도출 및 6연패 검증 워크플로우

## 의도 (핵심)

1. **스킵된 스텝을 분석**해서, **놓친 기회**(스킵했지만 맞았을 스텝)가 나오는 **빈도 신뢰도·시뮬 승률 조건**을 추출한다.
2. **추출한 조건과 기존 조건을 모두 적용**해서 점진적으로 검증한다.
3. **6연패가 발생하는 시뮬레이션 결과가 나오는지** 확인한다. **(핵심 검증)**
4. **6연패가 없으면** 그 조건을 **추가 조건**으로 제시한다. (추가 조건 적용 시 진입 기회는 늘어날 것으로 추정되며, 6연패가 나오지 않는지가 핵심이다.)

---

## 실행 순서 요약

| 순서 | 단계 | 내용 |
|------|------|------|
| **0** | (선택) 백필 | step_events의 null(predicted, actual, is_correct) 채우기. |
| **1** | 스킵 스텝 내보내기 | step_events에서 스킵 스텝 추출. **조건 추출용**이면 `--with-sim-wr`로 시뮬 승률(sim_win_rate_pct)까지 붙인 CSV 저장. |
| **2** | 조건 추출 | 스킵 CSV에서 **놓친 기회**(is_correct=1)가 나오는 **빈도 신뢰도·시뮬 승률** 조건을 도출 → JSON 저장. |
| **3** | 점진 검증 | **기존 조건 + 추출된 조건**을 함께 적용해 점진 실행 → first_anchor_window9_sliding_scale 로 저장. |
| **4** | 6연패 확인 | 저장된 결과에 대해 **연속 6회 실패** 발생 여부 전수 조사. **건수 0이면** 추출한 조건을 **추가 조건**으로 제시 가능. |
| **5** | 반복 | 6연패가 발생하면 조건을 조정한 뒤 2→3→4 다시 실행. |

**웹앱:** `streamlit run results_db/sliding_scale_workflow_app.py`

---

## 전제

- 결과 DB: `results_db/sim_results_change_point.db`
- 기존 가설 `first_anchor_window9_freq518_win50` run 20개, step_events에 스킵/진입·predicted/actual/is_correct 저장됨.

---

## 1단계: 스킵 스텝 내보내기

- **조건 추출**을 하려면 **빈도 신뢰도(confidence) + 시뮬 승률(sim_win_rate_pct)**가 필요하므로 **`--with-sim-wr`** 로 내보낸다.
- run별 cutoff로 예측 테이블을 복원한 뒤, 해당 run의 스킵 스텝에 sim_win_rate_pct를 붙여 CSV로 저장한다.

```bash
python -m results_db.export_skipped_steps_with_sim_wr ^
  --hypothesis first_anchor_window9_freq518_win50 ^
  --limit 20 ^
  --with-sim-wr ^
  --out results_db/skipped_steps.csv
```

- `--with-sim-wr` 없으면: confidence, predicted, actual, is_correct만 (예측 테이블 복원 없음).
- `--with-sim-wr` 있으면: 위 + sim_win_rate_pct (조건 추출용).

---

## 2단계: 놓친 기회 조건 추출

- 1단계 CSV(confidence, sim_win_rate_pct, is_correct)에서 **놓친 기회**(is_correct=1)가 많은 구간을 보고, **빈도 신뢰도·시뮬 승률** 조건(구간별 최소 sim_wr 등)을 도출한다.
- 출력 JSON은 3단계에서 **기존 조건 + 추가 조건**으로 함께 쓴다.

```bash
python -m results_db.derive_sliding_scale ^
  --input results_db/skipped_steps.csv ^
  --out results_db/derived_sliding_config.json
```

- `--conf-safe-low`, `--conf-high`: 신뢰도 구간 경계 (기본 51.3, 54.0).
- `--min-success-rate`: 구간별 “진입 시 목표 성공률” (기본 50). sim_win_rate_pct가 있을 때 최소 sim_wr 제안에 사용.

---

## 3단계: 기존 조건 + 추출 조건 적용 — 점진 검증

- **기존 조건**(예: 51.3 초과, 49.9 초과)과 **추출된 조건**(도출된 wr_strict, wr_safe, wr_relaxed 등)을 **모두 적용**한 슬라이딩 스케일로, run 순서대로 점진 검증 후 저장한다.

```bash
python -m results_db.run_progressive_sliding_scale ^
  --config results_db/derived_sliding_config.json ^
  --limit 20 ^
  --audit
```

- `--config`: 2단계에서 만든 JSON. 없으면 모듈 기본값만 사용.

---

## 4단계: 6연패 전수 조사 (핵심)

- 저장된 슬라이딩 가설 run에 대해 **연속 6회 실패**가 발생한 (run_id, grid_string_id) 건수를 확인한다.
- **건수 0** → 추출한 조건을 **추가 조건**으로 제시해도 됨 (진입 기회 확대, 6연패 없음).
- **건수 > 0** → 조건 조정 후 2→3→4 반복.

```bash
python results_db/audit_consecutive_failures.py ^
  --hypothesis-key first_anchor_window9_sliding_scale
```

---

## 요약

1. 스킵 스텝 분석 → **놓친 기회**가 나오는 **빈도 신뢰도·시뮬 승률 조건** 추출.
2. **추출 조건 + 기존 조건** 모두 적용해 점진 검증.
3. **6연패 발생 여부** 확인. **(핵심)**
4. 6연패가 없으면 해당 조건을 **추가 조건**으로 제시. 추가 조건으로 진입 기회가 늘어나도 **6연패가 나오지 않는지 검증**이 핵심이다.
