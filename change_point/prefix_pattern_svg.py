"""Prefix b/p run-column SVG (Prefix_규칙_도형_시각화_문서.md)."""


def prefix_pattern_svg(prefix: str, cell_px: int = 10, r_px: float = 4) -> str:
    """
    prefix 문자열을 꺽임(run)마다 열로 세로 배치. b=빨강, p=파랑.
    마지막 문자는 우하단 1/4 원 채움.
    """
    if not prefix or not all(c in "bp" for c in prefix):
        return ""
    n = len(prefix)
    runs = []
    i = 0
    while i < n:
        c = prefix[i]
        j = i
        while j < n and prefix[j] == c:
            j += 1
        runs.append((c, j - i))
        i = j

    circles = []
    idx = 0
    max_rows = 0
    for col, (_char, run_len) in enumerate(runs):
        for row_in_run in range(run_len):
            c = prefix[idx]
            color = "#c00" if c == "b" else "#06c"
            is_last = idx == n - 1
            cx = col * cell_px + cell_px / 2
            cy = row_in_run * cell_px + cell_px / 2
            circles.append((cx, cy, color, is_last))
            idx += 1
        max_rows = max(max_rows, run_len)

    w = len(runs) * cell_px
    h = max_rows * cell_px
    svg_parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" style="vertical-align:middle;">'
    ]
    for cx, cy, color, is_last in circles:
        svg_parts.append(
            f'<circle cx="{cx}" cy="{cy}" r="{r_px}" fill="none" '
            f'stroke="{color}" stroke-width="1.2"/>'
        )
        if is_last:
            svg_parts.append(
                f'<path d="M{cx + r_px},{cy} A{r_px},{r_px} 0 0 1 {cx},{cy + r_px} '
                f'L{cx},{cy} Z" fill="{color}"/>'
            )
    svg_parts.append("</svg>")
    return "".join(svg_parts)


def prefix_pattern_svg_viz(prefix: str, cell_px: int = 10, r_px: float = 4) -> str:
    """
    화면용 도형: prefix 앞에 첫 문자 1개를 추가해 시각화.
    예) bpbbbpbp → bbpbbbpbp (표시 텍스트는 원본 prefix 유지).
    """
    p = (prefix or "").strip().lower()
    if not p or not all(c in "bp" for c in p):
        return ""
    return prefix_pattern_svg(p[0] + p, cell_px=cell_px, r_px=r_px)
