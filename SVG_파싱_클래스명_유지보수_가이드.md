# SVG 파싱 클래스명 유지보수 가이드

Bead Road 그리드가 들어 있는 HTML 구조의 **클래스명**이 바뀌었을 때, 이 문서만 참조하여 동일한 수정을 반영하면 됩니다.

---

## 1. 수정 대상 요약

| 구분 | 클래스 역할 | 현재 값 (2025-02 적용분) |
|------|-------------|--------------------------|
| 메인 컨테이너 | 그리드를 감싸는 최상위 div | `q1_rc` |
| 행(Row) | 한 줄(행)을 나타내는 div | `q1_oN` |
| 셀(Cell) | 각 칸(플/뱅/무)을 나타내는 div | `q1_rf` |

**새 HTML 구조 확인 방법:** 브라우저 개발자도구에서 그리드 최상위 div의 `class`를 확인한 뒤, 그 div 기준으로 자식 행/셀의 class를 확인합니다.

---

## 2. 반드시 수정해야 할 위치 (총 9곳)

클래스명이 바뀌면 **아래 9곳을 모두** 새 값으로 맞춰 수정합니다.

### 2.1 `svg_parser_module.py` (5곳)

| # | 위치 | 수정 내용 |
|---|------|-----------|
| 1 | **실제 코드** – 메인 컨테이너 (약 66행) | `soup.find('div', class_='...')` 에 새 **메인 컨테이너** 클래스명 |
| 2 | **실제 코드** – 행 (약 71행) | `find_all('div', class_='...')` 에 새 **행** 클래스명 |
| 3 | **실제 코드** – 셀 (약 78행) | `find_all('div', class_='...')` 에 새 **셀** 클래스명 |
| 4 | **docstring** – 이력 마지막 줄 (약 51행) | `(최신 구조)` 가 달린 줄에 새 클래스명 반영 또는 새 이력 줄 추가 |
| 5 | **docstring** – 예시 코드 3줄 (약 57~59행) | `main_container`, `rows`, `cells` 예시의 클래스명 3개 수정 |

**검색 키워드:** `[유지보수] 클래스명 변경 시` 또는 `BEAD_ROAD_` 로 해당 라인을 찾을 수 있습니다.

### 2.2 `svg_parser_app.py` (1곳)

| # | 위치 | 수정 내용 |
|---|------|-----------|
| 6 | **사용 방법** expander 내 문구 (약 334행) | `현재 지원하는 클래스명: \`...\` (메인 컨테이너), \`...\` (행), \`...\` (셀)` 부분을 새 클래스명 3개로 수정 |

**검색 키워드:** `현재 지원하는 클래스명`

### 2.3 `change_point/change_point_ngram_app.py` (3곳)

| # | 위치 | 수정 내용 |
|---|------|-----------|
| 7 | **사용 방법** expander – 클래스명 문구 (약 720행) | `현재 지원하는 클래스명: \`...\` (메인 컨테이너), \`...\` (행), \`...\` (셀)` 부분을 새 클래스명 3개로 수정 |
| 8 | **getattr fallback** (약 27행) | `getattr(_svg_parser, 'BEAD_ROAD_MAIN_CONTAINER_CLASS', '...')` 세 번째 인자(기본값)에 새 **메인 컨테이너** 클래스명. SVG 입력 섹션의 콘솔 복사 명령에도 사용됨 |
| 9 | **사용 방법** expander – 복사 명령 예시 (약 721행) | `copy(document.querySelector('.pf_pi').outerHTML);` 에서 `.pf_pi` 부분을 새 메인 컨테이너 클래스명으로 수정 |

**검색 키워드:** `현재 지원하는 클래스명` / `BEAD_ROAD_MAIN_CONTAINER_CLASS` / `querySelector('.`

---

## 3. 작업 체크리스트 (클래스명 변경 시)

- [ ] **1** `svg_parser_module.py` – `main_container`용 `class_='...'` 수정
- [ ] **2** `svg_parser_module.py` – `rows`용 `class_='...'` 수정
- [ ] **3** `svg_parser_module.py` – `cells`용 `class_='...'` 수정
- [ ] **4** `svg_parser_module.py` – docstring 이력에 새 줄 추가 또는 최신 구조 줄 수정
- [ ] **5** `svg_parser_module.py` – docstring 예시 3줄의 클래스명 수정
- [ ] **6** `svg_parser_app.py` – 사용 방법 안내의 클래스명 3개 수정
- [ ] **7** `change_point/change_point_ngram_app.py` – 사용 방법 안내의 클래스명 3개 수정
- [ ] **8** `change_point/change_point_ngram_app.py` – getattr fallback(약 27행) 메인 컨테이너 클래스명 수정
- [ ] **9** `change_point/change_point_ngram_app.py` – 사용 방법 expander 내 `querySelector('.…')` 복사 명령 예시 수정

---

## 4. 변경 이력 (참고용)

| 적용 시기 | 메인 컨테이너 | 행 | 셀 |
|-----------|----------------|-----|-----|
| 2025-02-XX (현재) | `q1_rc` | `q1_oN` | `q1_rf` |
| 2025-02-XX | `pf_pi` | `pf_ow` | `pf_pk` |
| 2025-02-XX | `rw_rz` | `rw_qM` | `rw_rB` |
| 2025-01-XX | `rf_ri` | `rf_qW` | `rf_rk` |
| 그 이전 | (docstring 내 이력 참고) | … | … |

이력 상세는 `svg_parser_module.py`의 `parse_bead_road_svg` 함수 docstring을 참고합니다.

---

## 5. 참고 사항

- **파싱 로직(플/뱅/무, SVG 색상)** 은 수정하지 않습니다. 클래스명만 바꾸면 됩니다.
- `change_point_ngram_app.py`는 `svg_parser_module.parse_bead_road_svg`를 사용하므로, **모듈만 수정하면 파싱 동작은 자동으로 새 구조를 따릅니다.** 앱 쪽은 사용 방법 문구만 맞추면 됩니다.
- 수정 후 `svg_parser_app` 또는 `change_point_ngram_app`에서 새 HTML을 붙여넣고 파싱해 보며 동작을 확인하면 됩니다.

이후 클래스명이 다시 바뀌면 **이 문서의 체크리스트와 9곳만 참조**하여 동일하게 수정하면 됩니다.
