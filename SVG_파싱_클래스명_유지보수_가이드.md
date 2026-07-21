# SVG 파싱 클래스명 유지보수 가이드

Bead Road 그리드가 들어 있는 HTML 구조의 **클래스명**이 바뀌었을 때, 이 문서만 참조하여 동일한 수정을 반영하면 됩니다.

**주 사용 앱:** `change_point/change_point_ngram_app_file_batch.py` (HTML 배치 일괄 파싱)

---

## 1. 수정 대상 요약

| 구분 | 클래스 역할 | 현재 값 (2026-07 적용분) |
|------|-------------|--------------------------|
| 메인 컨테이너 | 그리드를 감싸는 최상위 div | `uS_uX` |
| 행(Row) | 한 줄(행)을 나타내는 div | `uS_uq` |
| 셀(Cell) | 각 칸(플/뱅/무 또는 P/B/T)을 나타내는 div | `uS_uZ` |

**새 HTML 구조 확인 방법:** 브라우저 개발자도구에서 그리드 최상위 div의 `class`를 확인한 뒤, 그 div 기준으로 자식 행/셀의 class를 확인합니다.

---

## 2. 파싱 구조 (file_batch 앱 기준)

`change_point_ngram_app_file_batch.py`는 아래 경로로 파싱합니다.

```
_parse_bead_road_html()
  └─ parse_bead_road_svg_with_pbt_fallback()   ← svg_parser_pbt_extension.py
       ├─ 1차: parse_bead_road_svg()           ← svg_parser_module.py (한글 플/뱅/무)
       └─ 2차: P/B/T 파서 fallback             ← svg_parser_pbt_extension.py (영문 P/B/T)
```

- Save Page HTML 전체·**iframe srcdoc** 안에서 컨테이너를 자동 탐색합니다.
- 에러 메시지·사용 방법 expander의 컨테이너 클래스 표시는 `BEAD_ROAD_MAIN_CONTAINER_CLASS` 변수를 사용하므로, **모듈 상수만 맞추면 UI 문구도 자동 반영**됩니다 (fallback 기본값은 별도 수정).

---

## 3. 반드시 수정해야 할 위치

클래스명이 바뀌면 **아래 필수 항목을 모두** 새 값으로 맞춥니다.

### 3.1 `svg_parser_module.py` (필수, 2곳)

| # | 위치 | 수정 내용 |
|---|------|-----------|
| 1 | **상수 3개** (약 19~21행) | `BEAD_ROAD_MAIN_CONTAINER_CLASS`, `BEAD_ROAD_ROW_CLASS`, `BEAD_ROAD_CELL_CLASS` |
| 2 | **docstring 이력** (약 62행 근처) | `(최신 구조)` 줄에 새 클래스명 반영 또는 새 이력 줄 추가 |

파싱 코드는 상수를 참조합니다 (`soup.find(..., class_=BEAD_ROAD_MAIN_CONTAINER_CLASS)` 등). **하드코딩 문자열을 직접 찾아 바꿀 필요 없음.**

**검색 키워드:** `BEAD_ROAD_` / `[유지보수]`

### 3.2 `svg_parser_pbt_extension.py` (필수, 1곳 — batch 앱 P/B/T fallback)

| # | 위치 | 수정 내용 |
|---|------|-----------|
| 3 | **상수 3개** (약 30~32행) | `PBT_BEAD_ROAD_MAIN_CONTAINER_CLASS`, `PBT_BEAD_ROAD_ROW_CLASS`, `PBT_BEAD_ROAD_CELL_CLASS` |

한글 파서 실패 후 영문 P/B/T HTML을 파싱할 때 사용합니다. **file_batch 앱 사용 시 반드시 함께 수정.**

**검색 키워드:** `PBT_BEAD_ROAD_`

### 3.3 `change_point/change_point_ngram_app_file_batch.py` (필수, 1곳 — 주 사용 앱)

| # | 위치 | 수정 내용 |
|---|------|-----------|
| 4 | **getattr fallback** (약 28행) | `getattr(_svg_parser, 'BEAD_ROAD_MAIN_CONTAINER_CLASS', '...')` 세 번째 인자(기본값)에 새 **메인 컨테이너** 클래스명 |

expander·에러 메시지의 `.{BEAD_ROAD_MAIN_CONTAINER_CLASS}` 표시는 변수 참조이므로 **추가 수정 불필요**.

**검색 키워드:** `BEAD_ROAD_MAIN_CONTAINER_CLASS`

---

## 4. 선택 수정 (UI 안내 동기화)

다른 앱을 함께 쓰는 경우에만 맞춥니다.

| 파일 | 수정 내용 |
|------|-----------|
| `change_point/change_point_ngram_app_file.py` | getattr fallback (약 27행) + expander getattr 기본값 3개 (약 725행) |
| `change_point/change_point_ngram_app.py` | getattr fallback (약 28행) + expander 클래스명 문구·`querySelector` 예시 (약 721~722행) |
| `svg_parser_app.py` | 사용 방법 expander 클래스명 3개 (약 334행) |

---

## 5. 작업 체크리스트 (클래스명 변경 시)

### 필수 (file_batch 사용 시)

- [ ] **1** `svg_parser_module.py` – `BEAD_ROAD_*` 상수 3개 수정
- [ ] **2** `svg_parser_module.py` – docstring 이력에 새 줄 추가 또는 최신 구조 줄 수정
- [ ] **3** `svg_parser_pbt_extension.py` – `PBT_BEAD_ROAD_*` 상수 3개 수정
- [ ] **4** `change_point/change_point_ngram_app_file_batch.py` – getattr fallback 메인 컨테이너 클래스명 수정

### 선택 (다른 앱 UI 안내)

- [ ] **5** `change_point/change_point_ngram_app_file.py` – fallback + expander getattr 기본값
- [ ] **6** `change_point/change_point_ngram_app.py` – fallback + expander 문구 2곳
- [ ] **7** `svg_parser_app.py` – 사용 방법 안내 클래스명 3개

### 확인

- [ ] **8** `change_point_ngram_app_file_batch`에서 Save Page HTML 업로드 후 일괄 파싱 테스트

---

## 6. 변경 이력 (참고용)

| 적용 시기 | 메인 컨테이너 | 행 | 셀 |
|-----------|----------------|-----|-----|
| 2026-07-XX (현재) | `uS_uX` | `uS_uq` | `uS_uZ` |
| 2026-07-XX | `uv_uA` | `uv_uF` | `uv_uG` |
| 2025-02-XX | `rN_rQ` | `rN_oT` | `rN_rS` |
| 2025-02-XX | `q1_rc` | `q1_oN` | `q1_rf` |
| 2025-02-XX | `pf_pi` | `pf_ow` | `pf_pk` |
| 2025-02-XX | `rw_rz` | `rw_qM` | `rw_rB` |
| 2025-01-XX | `rf_ri` | `rf_qW` | `rf_rk` |
| 그 이전 | (docstring 내 이력 참고) | … | … |

이력 상세는 `svg_parser_module.py`의 `parse_bead_road_svg` 함수 docstring을 참고합니다.

---

## 7. 참고 사항

- **파싱 로직(플/뱅/무, P/B/T, SVG 색상)** 은 수정하지 않습니다. 클래스명만 바꾸면 됩니다.
- `svg_parser_module`과 `svg_parser_pbt_extension`의 클래스명은 **동일한 HTML 구조**를 가리키므로, 변경 시 **두 파일 모두** 같은 값으로 맞춥니다.
- file_batch 앱은 모듈 상수를 import하므로, **필수 4곳만 수정하면 파싱 동작은 자동으로 새 구조를 따릅니다.**
- 수정 후 `change_point_ngram_app_file_batch`에서 Save Page HTML을 업로드해 일괄 파싱으로 동작을 확인하면 됩니다.

이후 클래스명이 다시 바뀌면 **§3 필수 항목 + §5 체크리스트**를 참조하여 수정하면 됩니다.
