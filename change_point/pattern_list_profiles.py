"""pattern_list / pattern_list2 프로필 경로 설정."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CHANGE_POINT_DIR = PROJECT_ROOT / "change_point"
DB_BACKUP_DIR = CHANGE_POINT_DIR / "db_backup"

SOURCE_DB = CHANGE_POINT_DIR / "change_point_ngram.db"

# 라이브 앱(livegame_three_modes_v4)이 읽는 운영 DB — 갱신 앱에서 쓰지 않음.
LIST2_LIVE_PREDICTIONS_DB = CHANGE_POINT_DIR / "pattern_list2.db"
# 예측 테이블 갱신/비교/스냅샷 파이프라인 전용 테스트 DB.
LIST2_TEST_PREDICTIONS_DB = DB_BACKUP_DIR / "pattern_list2_TEST.db"

__all__ = [
    "PROJECT_ROOT",
    "CHANGE_POINT_DIR",
    "DB_BACKUP_DIR",
    "SOURCE_DB",
    "LIST2_LIVE_PREDICTIONS_DB",
    "LIST2_TEST_PREDICTIONS_DB",
    "GRID_CSV_NAME",
    "NGRAM_CSV_NAME",
    "TABLE_GRID",
    "TABLE_NGRAM",
    "TABLE_SIM",
    "PatternListProfile",
    "PROFILES",
    "get_profile",
]

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
    is_test_db: bool = False


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
        # 테스트 완료 전까지 갱신 파이프라인은 TEST DB만 사용.
        # 라이브는 LIST2_LIVE_PREDICTIONS_DB (pattern_list2.db) 고정.
        predictions_db=LIST2_TEST_PREDICTIONS_DB,
        json_name_prefix="prefix_list2_new_3way_agree",
        is_test_db=True,
    ),
}


def get_profile(name: str) -> PatternListProfile:
    key = (name or "list1").strip().lower()
    if key not in PROFILES:
        raise ValueError(f"Unknown profile: {name!r}. Choose from: {list(PROFILES)}")
    return PROFILES[key]
