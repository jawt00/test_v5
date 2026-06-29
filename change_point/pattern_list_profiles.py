"""pattern_list / pattern_list2 프로필 경로 설정."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANGE_POINT_DIR = PROJECT_ROOT / "change_point"

SOURCE_DB = CHANGE_POINT_DIR / "change_point_ngram.db"

GRID_CSV_NAME = "grid_string_prefix_chunks.csv"
NGRAM_CSV_NAME = "ngram_chunks_ws12.csv"

TABLE_GRID = "grid_string_prefix_chunks_predictions"
TABLE_NGRAM = "ngram_chunks_ws12_predictions"
TABLE_SIM = "simulation_predictions_change_point"


@dataclass(frozen=True)
class PatternListProfile:
    name: str
    pattern_csv: Path
    export_dir: Path
    predictions_db: Path
    json_name_prefix: str


PROFILES: dict[str, PatternListProfile] = {
    "list1": PatternListProfile(
        name="list1",
        pattern_csv=PROJECT_ROOT / "pattern_list.csv",
        export_dir=CHANGE_POINT_DIR / "pattern_list_exports",
        predictions_db=SOURCE_DB,
        json_name_prefix="prefix_list_new_3way_agree",
    ),
    "list2": PatternListProfile(
        name="list2",
        pattern_csv=PROJECT_ROOT / "pattern_list2.csv",
        export_dir=CHANGE_POINT_DIR / "pattern_list2_exports",
        predictions_db=CHANGE_POINT_DIR / "pattern_list2.db",
        json_name_prefix="prefix_list2_new_3way_agree",
    ),
}


def get_profile(name: str) -> PatternListProfile:
    key = (name or "list1").strip().lower()
    if key not in PROFILES:
        raise ValueError(f"Unknown profile: {name!r}. Choose from: {list(PROFILES)}")
    return PROFILES[key]
