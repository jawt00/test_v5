# Prefix 규칙 도형 시각화 명세

Prefix 규칙 목록에서 각 prefix 문자열 오른쪽에 그려지는 **빨간/파란 원 도형**의 규칙과 구현 방법을 정리한 문서이다. 이 문서만으로 동일한 시각화를 재구현할 수 있도록 상세히 기술한다.

---

## 1. 개요

- **목적**: prefix 문자열(예: `bpbbbbbbpb`)을 **b/p 시퀀스**로 한눈에 보이게 하기 위하여, 문자를 격자 위의 **원(circle)**으로 표현한다.
- **색상**: `b` = 빨간색, `p` = 파란색.
- **레이아웃**: 문자가 바뀌는 지점(**꺽임**)에서 **줄바꿈을 횡으로 적용**한다. 즉, 같은 문자가 이어지는 구간(run)마다 **한 열(column)**을 차지하고, 그 run의 문자들은 그 열 안에서 **위에서 아래로** 쌓인다. 열들은 **왼쪽에서 오른쪽**으로 나열된다.
- **마지막 문자**: 시퀀스의 **마지막 문자**에 해당하는 원만 **우하단 1/4 원**을 같은 색으로 채운다(현재 위치/끝 표시).

---

## 2. 입력·출력

| 항목 | 설명 |
|------|------|
| **입력** | `prefix`: 문자열. 모든 문자가 `b` 또는 `p`여야 함. 빈 문자열 또는 b/p 외 문자가 있으면 시각화하지 않음(빈 문자열 반환). |
| **출력** | SVG 문자열. HTML에 그대로 삽입 가능(`<svg>...</svg>`). |
| **파라미터** | `cell_px`: 한 칸(셀)의 픽셀 크기(기본 10). `r_px`: 원의 반지름 픽셀(기본 4). |

---

## 3. 규칙 상세

### 3.1 Run 분할 (꺽임)

- **Run**: 연속된 **같은 문자**의 구간.
- **꺽임**: `prefix[i] != prefix[i+1]`인 위치. 꺽임에서 run이 끊긴다.
- **예**: `bpbbbbbbpb` → run 5개: `b`(길이 1), `p`(1), `b`(6), `p`(1), `b`(1).

### 3.2 레이아웃 (횡으로 줄바꿈)

- 각 **run**은 **한 열(column)**을 이룬다.
- 한 열 안에서는 run의 문자들이 **세로로** 쌓인다(행 인덱스 0, 1, 2, …).
- 열들은 **run 순서대로** 왼쪽에서 오른쪽으로 배치(열 인덱스 0, 1, 2, …).

좌표계:

- **열 인덱스 `col`**: 0부터 시작. run 인덱스와 동일.
- **행 인덱스 `row`**: 각 열 내에서 0부터 시작. 같은 run 내에서만 증가.

예: `bpbbbbbbpb` (run: b | p | bbbbbb | p | b)

- 열 0: b 1개 → (col=0, row=0)
- 열 1: p 1개 → (col=1, row=0)
- 열 2: b 6개 → (col=2, row=0..5)
- 열 3: p 1개 → (col=3, row=0)
- 열 4: b 1개 → (col=4, row=0)

### 3.3 색상

- `b` → **빨강**: `#c00` (또는 `#cc0000`).
- `p` → **파랑**: `#06c` (또는 `#0066cc`).

### 3.4 원 그리기

- 각 문자에 대해 **원 하나**를 그린다.
- **테두리만**: `fill="none"`, `stroke={색상}`, `stroke-width="1.2"`.
- **마지막 문자**에 대해서만: 해당 원의 **우하단 1/4**를 같은 색으로 채운다(SVG `<path>`로 1/4 원 호 + 중심 연결).

### 3.5 SVG 좌표·크기

- **셀**: 한 문자를 담는 정사각형. 한 변 = `cell_px` (기본 10px).
- **원 중심**  
  - 열 `col`, 행 `row`인 문자:  
    - `cx = col * cell_px + cell_px / 2`  
    - `cy = row * cell_px + cell_px / 2`
- **캔버스 크기**  
  - `width = (열 개수) * cell_px`  
  - `height = (가장 긴 run의 길이) * cell_px`  
  - 열 개수 = run 개수. 가장 긴 run = `max(run 길이)`.

---

## 4. 데이터 구조·알고리즘

### 4.1 Run 목록 구하기

```
runs = []
i = 0
while i < len(prefix):
    c = prefix[i]
    j = i
    while j < len(prefix) and prefix[j] == c:
        j += 1
    runs.append( (문자 c, 길이 j-i) )
    i = j
```

각 요소는 `(문자, run_길이)`.

### 4.2 원 목록(좌표·색·마지막 여부) 구하기

전역 문자 인덱스 `idx`를 0으로 두고:

```
circles = []
idx = 0
max_rows = 0
for col, (char, run_len) in enumerate(runs):
    for row_in_run in 0 .. run_len-1:
        c = prefix[idx]
        color = (c == 'b') ? "#c00" : "#06c"
        is_last = (idx == len(prefix) - 1)
        cx = col * cell_px + cell_px / 2
        cy = row_in_run * cell_px + cell_px / 2
        circles.append( (cx, cy, color, is_last) )
        idx += 1
    max_rows = max(max_rows, run_len)

width  = len(runs) * cell_px
height = max_rows * cell_px
```

### 4.3 SVG 생성

- **루트**: `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="{width}" height="{height}" style="vertical-align:middle;">`
- **각 원**  
  - 기본:  
    `<circle cx="{cx}" cy="{cy}" r="{r_px}" fill="none" stroke="{color}" stroke-width="1.2"/>`  
  - `is_last`인 경우, 같은 (cx, cy, color)에 대해 **우하단 1/4 원** path 추가:
    - 3시 방향 → 6시 방향으로 가는 90° 호.
    - Path:  
      `M {cx+r_px},{cy} A {r_px},{r_px} 0 0 1 {cx},{cy+r_px} L {cx},{cy} Z`  
      `fill="{color}"`
- **닫기**: `</svg>`

1/4 원 path 설명:

- `M cx+r_px, cy`: 원의 3시 지점에서 시작.
- `A r_px r_px 0 0 1 cx, cy+r_px`: 반지름 `r_px`인 호를 그려 6시 지점으로 이동( sweep 1 = 시계 방향).
- `L cx, cy`: 중심으로 직선.
- `Z`: 경로 닫기.

---

## 5. 구현 체크리스트 (동일 구현용)

1. **전처리**: prefix가 비어 있거나, b/p 외 문자가 있으면 빈 문자열 `""` 반환.
2. **Run 분할**: 위 4.1대로 run 리스트 생성.
3. **좌표·색·마지막 플래그**: 4.2대로 `circles`와 `width`, `height` 계산.
4. **SVG**  
   - viewBox, width, height, `vertical-align:middle`  
   - 각 (cx, cy, color, is_last)에 대해 circle + (is_last이면) 1/4 원 path  
   - 기본 상수: `cell_px=10`, `r_px=4`, stroke-width `1.2`, b=`#c00`, p=`#06c`

---

## 6. UI에서의 사용 (참고)

- **위치**: Prefix 규칙 테이블에서, 각 행의 **prefix 텍스트 오른쪽** 열.
- **호출**: `svg_string = _prefix_pattern_svg(row_prefix)`  
  - `row_prefix`: 해당 규칙의 prefix 문자열(소문자 b/p만).
- **렌더링**: HTML로 삽입. 예: Streamlit에서는  
  `st.markdown(svg_string, unsafe_allow_html=True)`  
- **예외**: prefix가 비어 있거나 b/p가 아니면 도형 대신 "—" 등 대체 텍스트 표시 가능.

---

## 7. 예시: `bpbbbbbbpb`

- Run: `b`(1), `p`(1), `b`(6), `p`(1), `b`(1).
- 열 5개, 최대 run 길이 6 → width=50, height=60 (cell_px=10 기준).
- 시각적 형태(개념):

```
  b   p   b   p   b
          b
          b
          b
          b
          b   (마지막 b만 우하단 1/4 채움)
```

- 마지막 문자(맨 아래 오른쪽 b)에만 우하단 1/4 원이 채워진다.

---

## 8. 요약 표

| 항목 | 값/규칙 |
|------|---------|
| b 색상 | `#c00` |
| p 색상 | `#06c` |
| 셀 크기 | `cell_px` (기본 10) |
| 원 반지름 | `r_px` (기본 4) |
| stroke-width | 1.2 |
| 레이아웃 | run = 열, run 내 문자 = 세로 배치, 열은 가로 나열 |
| 마지막 문자 | 우하단 1/4 원 path로 채움 |
| 1/4 원 path | `M{cx+r},{cy} A{r},{r} 0 0 1 {cx},{cy+r} L{cx},{cy} Z` |

이 명세와 체크리스트를 따르면 문서만 보고 동일한 prefix 도형 시각화를 구현할 수 있다.
