"""
종료조건 충족 후 '다음 포지션부터 앵커 탐색' 시나리오 검증.

- bbbbbpbpbbppbb 종료 → last b 포지션 13 기억, search_from=14 유지
- bbbbbpbpbbppbbp, ...pp → 스킵 시 search_from 유지, 앵커 13(b→p) 사용 금지 (대기)
- bbbbbpbpbbppbbppb → 이전 p(15)가 앵커로 인식, 15부터 다음 종료조건 대기
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from change_point_live_game_app_flow import (
    _anchors_from_grid_string,
    _first_anchor_from_position,
)


def main():
    # 종료 시 last b = 13, search_from = 14 유지 (스킵 시 갱신 안 함)
    search_from = 14

    cases = [
        ("bbbbbpbpbbppbb", "종료 직후"),
        ("bbbbbpbpbbppbbp", "p 1회 입력 → p를 다음 앵커로 인식하면 안 됨"),
        ("bbbbbpbpbbppbbpp", "p 2회 입력"),
        ("bbbbbpbpbbppbbppb", "b 입력 → 이전 p를 다음 앵커로 인식"),
    ]
    print("시나리오: search_from=14 유지, 종료 포지션 다음부터 앵커 탐색\n")
    for s, label in cases:
        anchors = _anchors_from_grid_string(s)
        idx = _first_anchor_from_position(anchors, search_from)
        first_ge = anchors[idx] if idx < len(anchors) else None
        print(f"{label}")
        print(f"  문자열: {s} (len={len(s)})")
        print(f"  앵커 위치: {anchors}")
        print(f"  search_from={search_from} → first >= search_from: idx={idx}, pos={first_ge}")
        if s == "bbbbbpbpbbppbbp":
            assert idx >= len(anchors), "앵커 13 사용 금지"
            print("  ✓ p를 다음 앵커로 인식하지 않음 (대기)")
        elif s == "bbbbbpbpbbppbbppb":
            assert first_ge == 15, f"이전 p(15) 인식 기대, 실제={first_ge}"
            print("  ✓ 이전 p(15)를 다음 앵커로 인식")
        print()

    print("검증 통과.")


if __name__ == "__main__":
    main()
