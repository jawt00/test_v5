"""rule_confidence (conservative min) 단위 테스트."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "change_point"))

from pattern_list_final_confidence import compute_final_confidence  # noqa: E402


def _row(**kwargs) -> pd.Series:
    base = {
        "sim_pred": None,
        "sim_conf": None,
        "grid10_pred": None,
        "grid10_conf": None,
        "grid12_pred": None,
        "grid12_conf": None,
        "ngram12_pred": None,
        "ngram12_conf": None,
        "agree_sim_ngram": False,
        "agree_sim_grid10": False,
        "agree_new_three": False,
    }
    base.update(kwargs)
    return pd.Series(base)


def test_r1_consensus_min():
    row = _row(
        grid10_pred="b",
        grid10_conf=58.0,
        grid12_pred="b",
        grid12_conf=62.0,
        ngram12_pred="b",
        ngram12_conf=55.0,
    )
    out = compute_final_confidence(
        row, final_rule="R1", final_pred="b", profile="list2"
    )
    assert out["rule_confidence"] == 55.0
    assert out["conf_source"] == "consensus_min"
    assert out["agree_count_new_three"] == 3


def test_r2_sim_only():
    row = _row(sim_pred="p", sim_conf=50.1, ngram12_pred="b", ngram12_conf=55.0)
    out = compute_final_confidence(
        row, final_rule="R2", final_pred="p", profile="list2"
    )
    assert out["rule_confidence"] == 50.1
    assert out["conf_source"] == "sim"
    assert out["agree_count"] == 1


def test_r3_consensus_min():
    row = _row(
        sim_pred="b",
        sim_conf=60.0,
        ngram12_pred="b",
        ngram12_conf=52.0,
        grid10_pred="b",
        grid10_conf=58.0,
    )
    out = compute_final_confidence(
        row, final_rule="R3", final_pred="b", profile="list2"
    )
    assert out["rule_confidence"] == 52.0
    assert out["conf_source"] == "consensus_min"


def test_r5a_sim():
    row = _row(
        sim_pred="p",
        sim_conf=49.5,
        ngram12_pred="p",
        ngram12_conf=51.0,
        grid10_pred="b",
        grid10_conf=55.0,
        agree_sim_ngram=True,
        agree_sim_grid10=False,
    )
    out = compute_final_confidence(
        row, final_rule="R5", final_pred="p", profile="list2"
    )
    assert out["rule_confidence"] == 49.5
    assert out["conf_source"] == "sim"


def test_r5b_majority_min():
    row = _row(
        sim_pred="b",
        sim_conf=70.0,
        ngram12_pred="p",
        ngram12_conf=54.0,
        grid10_pred="p",
        grid10_conf=56.0,
        grid12_pred="p",
        grid12_conf=58.0,
        agree_sim_ngram=False,
        agree_sim_grid10=False,
    )
    out = compute_final_confidence(
        row, final_rule="R5", final_pred="p", profile="list2"
    )
    assert out["rule_confidence"] == 54.0
    assert out["conf_source"] == "majority_min"


def test_r4_pass_null():
    out = compute_final_confidence(
        _row(), final_rule="R4", final_pred="pass", profile="list2"
    )
    assert out["rule_confidence"] is None
    assert out["conf_source"] == "pass"


def test_list3_profile_grid_aliases():
    """list3 compare row (aliased to list2 names) 동일 로직."""
    row = _row(
        grid10_pred="b",
        grid10_conf=58.0,
        grid12_pred="b",
        grid12_conf=62.0,
        ngram12_pred="b",
        ngram12_conf=55.0,
    )
    out = compute_final_confidence(
        row, final_rule="R1", final_pred="b", profile="list3"
    )
    assert out["rule_confidence"] == 55.0
