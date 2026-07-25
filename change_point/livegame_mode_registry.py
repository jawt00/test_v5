"""통합 라이브게임 — 모드별 profile · DB · lookup 전략."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pattern_list_profiles import (
    LIST2_LIVE_PREDICTIONS_DB,
    LIST2_TEST_PREDICTIONS_DB,
    LIST3_LIVE_PREDICTIONS_DB,
    LIST3_TEST_PREDICTIONS_DB,
)

LookupStrategy = Literal["direct", "ws9_core"]

MODES = ("window9_10", "window9_11")
DISPLAY_ORDER = MODES


@dataclass(frozen=True)
class LivegameMode:
    key: str
    profile: str
    label: str
    window_sizes: tuple[int, ...]
    lookup: LookupStrategy

    def predictions_db(self, *, use_test: bool = True) -> Path:
        if self.profile == "list2":
            return LIST2_TEST_PREDICTIONS_DB if use_test else LIST2_LIVE_PREDICTIONS_DB
        if self.profile == "list3":
            return LIST3_TEST_PREDICTIONS_DB if use_test else LIST3_LIVE_PREDICTIONS_DB
        raise ValueError(f"unknown profile: {self.profile!r}")


MODE_REGISTRY: dict[str, LivegameMode] = {
    "window9_10": LivegameMode(
        key="window9_10",
        profile="list2",
        label="W9_10 (L2)",
        window_sizes=(9, 10),
        lookup="direct",
    ),
    "window9_11": LivegameMode(
        key="window9_11",
        profile="list3",
        label="W9_11 (L3)",
        window_sizes=(9, 11),
        lookup="ws9_core",
    ),
}

DUAL_WINDOW_MODES = frozenset(MODE_REGISTRY.keys())


def get_mode(mode: str) -> LivegameMode:
    if mode not in MODE_REGISTRY:
        raise ValueError(f"unknown mode: {mode!r}. Choose from: {list(MODE_REGISTRY)}")
    return MODE_REGISTRY[mode]


def mode_label(mode: str) -> str:
    return get_mode(mode).label
