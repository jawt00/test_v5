"""
스킵된 스텝 CSV를 분석해, **놓친 기회**(스킵했지만 맞았을 스텝)가 나오는 **빈도 신뢰도·시뮬 승률 조건**을 추출.

- 스킵 스텝의 성공/실패 = 저장된 predicted vs actual (is_correct).
- "놓친 기회" = is_correct=1. 이들이 몰려 있는 (confidence, sim_win_rate_pct) 구간을 보고
  conf_safe_low, conf_high, wr_strict, wr_safe, wr_relaxed 를 도출 → 기존 조건과 함께 적용해 6연패 여부 검증 후, 0이면 추가 조건으로 제시.

실행:
  python -m results_db.derive_sliding_scale --input skipped_steps.csv --out config.json
"""

import csv
import json
import sys
from pathlib import Path

_root = Path(__file__).resolve().parent.parent
_change_point_dir = _root / "change_point"
for p in (_root, _change_point_dir):
    s = str(p)
    if s not in sys.path:
        sys.path.insert(0, s)

# 기본 구간 경계 (고정 또는 CLI)
DEFAULT_CONF_SAFE_LOW = 51.3
DEFAULT_CONF_HIGH = 54.0
MIN_SUCCESS_RATE_PCT = 50.0  # 이 이상이 되도록 min sim_wr 제안


def _float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def load_skipped_csv(path):
    """
    CSV에서 confidence, sim_win_rate_pct, is_correct 로드.
    is_correct = 예측(predicted) vs 실제(actual) 검증 결과 (1=성공, 0=실패).
    스킵 스텝 중 is_correct==1 인 것이 "놓친 기회"(스킵했지만 맞았을 스텝).
    """
    rows = []
    with open(path, "r", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            conf = _float(row.get("confidence"))
            sim_wr = _float(row.get("sim_win_rate_pct"))
            is_correct = row.get("is_correct")
            if is_correct is not None and str(is_correct).strip() in ("1", "true", "True", "1.0"):
                ok = 1
            elif is_correct is not None and str(is_correct).strip() in ("0", "false", "False", "0.0"):
                ok = 0
            else:
                ok = None
            if conf is None:
                continue
            rows.append({"confidence": conf, "sim_win_rate_pct": sim_wr, "is_correct": ok})
    return rows


def bin_rows(rows, conf_safe_low, conf_high):
    """rows를 conf 구간별로 나눔. (low: conf <= conf_safe_low, mid: 51.3 < conf < 54, high: conf >= 54)"""
    low, mid, high = [], [], []
    for r in rows:
        c = r.get("confidence")
        if c is None:
            continue
        if c <= conf_safe_low:
            low.append(r)
        elif c < conf_high:
            mid.append(r)
        else:
            high.append(r)
    return low, mid, high


def suggest_min_sim_wr(band_rows, min_success_rate_pct, step=0.5):
    """
    band_rows = 해당 conf 구간의 스킵 스텝들 (일부는 놓친 기회 is_correct=1, 일부는 실패 is_correct=0).
    "sim_wr >= X 일 때만 진입"으로 가정했을 때, 진입한 스텝들의 성공률이 min_success_rate_pct 이상이
    되도록 하는 최소 X를 찾음. → 놓친 기회를 더 잡되, 성공률은 유지.
    """
    valid = [r for r in band_rows if r.get("sim_win_rate_pct") is not None and r.get("is_correct") is not None]
    if not valid:
        return None
    sim_wr_vals = sorted(set(r["sim_win_rate_pct"] for r in valid))
    if not sim_wr_vals:
        return None
    best_x = None
    for x in [sim_wr_vals[0] - step] + [v for v in sim_wr_vals] + [sim_wr_vals[-1] + step]:
        if x < 0 or x > 100:
            continue
        entered = [r for r in valid if r["sim_win_rate_pct"] >= x]
        if not entered:
            continue
        success = sum(1 for r in entered if r["is_correct"] == 1)
        rate = 100.0 * success / len(entered)
        if rate >= min_success_rate_pct:
            best_x = x
            break
    return best_x


def derive_config(
    csv_path,
    conf_safe_low=DEFAULT_CONF_SAFE_LOW,
    conf_high=DEFAULT_CONF_HIGH,
    min_success_rate_pct=MIN_SUCCESS_RATE_PCT,
):
    """
    스킵 스텝 CSV로부터 슬라이딩 스케일 설정 도출.
    - 스킵 스텝 = 기존 조건으로 진입하지 않은 스텝. predicted/actual로 성공·실패 구분.
    - 놓친 기회 = 스킵 스텝 중 is_correct==1 (스킵했지만 맞았을 스텝).
    - 각 conf 구간별로, "진입 시 성공률 >= min_success_rate_pct"가 되도록 최소 sim_wr 제안.

    Returns:
        dict: conf_safe_low, conf_high, wr_strict, wr_safe, wr_relaxed, _meta(구간별 스킵 수·놓친 기회 수)
    """
    rows = load_skipped_csv(csv_path)
    low, mid, high = bin_rows(rows, conf_safe_low, conf_high)

    def count_missed_opportunities(band_rows):
        return sum(1 for r in band_rows if r.get("is_correct") == 1)

    wr_strict = suggest_min_sim_wr(low, min_success_rate_pct)
    wr_safe = suggest_min_sim_wr(mid, min_success_rate_pct)
    wr_relaxed = suggest_min_sim_wr(high, min_success_rate_pct)

    from change_point.change_point_sliding_scale_module import WR_SAFE, WR_STRICT, WR_RELAXED
    return {
        "conf_safe_low": conf_safe_low,
        "conf_high": conf_high,
        "wr_strict": wr_strict if wr_strict is not None else WR_STRICT,
        "wr_safe": wr_safe if wr_safe is not None else WR_SAFE,
        "wr_relaxed": wr_relaxed if wr_relaxed is not None else WR_RELAXED,
        "_meta": {
            "n_low": len(low),
            "n_mid": len(mid),
            "n_high": len(high),
            "missed_opportunities_low": count_missed_opportunities(low),
            "missed_opportunities_mid": count_missed_opportunities(mid),
            "missed_opportunities_high": count_missed_opportunities(high),
            "missed_opportunities_total": count_missed_opportunities(rows),
            "min_success_rate_pct": min_success_rate_pct,
        },
    }


def main():
    import argparse
    p = argparse.ArgumentParser(description="Derive sliding scale config from skipped-steps CSV.")
    p.add_argument("--input", required=True, help="CSV from export_skipped_steps_with_sim_wr (confidence, sim_win_rate_pct, is_correct)")
    p.add_argument("--out", default=None, help="Output JSON path (default: print)")
    p.add_argument("--conf-safe-low", type=float, default=DEFAULT_CONF_SAFE_LOW)
    p.add_argument("--conf-high", type=float, default=DEFAULT_CONF_HIGH)
    p.add_argument("--min-success-rate", type=float, default=MIN_SUCCESS_RATE_PCT, help="Target min success rate %% for suggested wr")
    args = p.parse_args()

    config = derive_config(
        args.input,
        conf_safe_low=args.conf_safe_low,
        conf_high=args.conf_high,
        min_success_rate_pct=args.min_success_rate,
    )
    meta = config.get("_meta", {})
    print("스킵 스텝 구간별: low(conf<=51.3) mid(51.3<conf<54) high(conf>=54)")
    print(f"  건수: low={meta.get('n_low',0)} mid={meta.get('n_mid',0)} high={meta.get('n_high',0)}")
    print(f"  놓친 기회(is_correct=1): low={meta.get('missed_opportunities_low',0)} mid={meta.get('missed_opportunities_mid',0)} high={meta.get('missed_opportunities_high',0)} (총 {meta.get('missed_opportunities_total',0)})")
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(config, f, indent=2, ensure_ascii=False)
        print(f"Wrote: {args.out}")
    else:
        print(json.dumps(config, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
