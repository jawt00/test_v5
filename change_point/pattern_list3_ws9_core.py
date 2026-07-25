"""list3 prefix → 8자 ws9_core 정규화 (라이브·compare 공용)."""

from __future__ import annotations


def norm_prefix(value) -> str:
    if value is None:
        return ""
    s = str(value).strip().lower()
    return s


def to_ws9_core(prefix: str, window_size: int) -> str:
    """native prefix → 8자 ws9 lookup 키."""
    p = norm_prefix(prefix)
    if not p:
        return ""
    ws = int(window_size)
    if ws == 9:
        return p
    if ws == 10:
        return p[1:] if len(p) > 1 else ""
    if ws == 11:
        return p[2:] if len(p) > 2 else ""
    if ws == 12:
        return p[3:] if len(p) > 3 else ""
    if ws == 13:
        return p[4:] if len(p) > 4 else ""
    raise ValueError(f"unsupported window_size for ws9_core: {window_size}")
