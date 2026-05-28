"""
grid_string_id 1896 디버깅 스크립트
각 스텝에서 어떤 앵커와 윈도우가 시도되었는지 상세 분석
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from svg_parser_module import get_change_point_db_connection
from change_point_hypothesis_module import (
    validate_threshold_skip_anchor_priority_cp,
    ThresholdSkipAnchorPriorityHypothesis,
)

def analyze_grid_string_1896():
    """grid_string_id 1896 상세 분석"""
    grid_string_id = 1896
    
    # grid_string 로드
    conn = get_change_point_db_connection()
    try:
        df = pd.read_sql_query(
            "SELECT grid_string FROM preprocessed_grid_strings WHERE id = ?",
            conn,
            params=[grid_string_id],
        )
        if len(df) == 0:
            print(f"grid_string_id {grid_string_id}를 찾을 수 없습니다.")
            return
        grid_string = df.iloc[0]["grid_string"]
        print(f"Grid String: {grid_string}")
        print(f"길이: {len(grid_string)}")
        print()
    finally:
        conn.close()
    
    # 앵커 위치 찾기
    anchors = []
    for i in range(len(grid_string) - 1):
        if grid_string[i] != grid_string[i+1]:
            anchors.append(i)
    anchors = sorted(list(set(anchors)))
    print(f"앵커 위치: {anchors}")
    print()
    
    # 검증 실행 (윈도우 8, 9, 10, 11, 12, 임계값 50)
    window_sizes = [8, 9, 10, 11, 12]
    window_thresholds = {8: 50, 9: 50, 10: 50, 11: 50, 12: 50}
    
    result = validate_threshold_skip_anchor_priority_cp(
        grid_string_id=grid_string_id,
        cutoff_grid_string_id=0,
        window_sizes=window_sizes,
        method="빈도 기반",
        threshold=50,
        window_thresholds=window_thresholds,
    )
    
    if not result:
        print("검증 결과가 없습니다.")
        return
    
    print("=" * 80)
    print("상세 분석 결과")
    print("=" * 80)
    print()
    
    history = result.get("history", [])
    
    # Step 16 이후 상세 분석
    for i, entry in enumerate(history):
        step = entry.get("step", 0)
        position = entry.get("position", "")
        anchor = entry.get("anchor", "")
        window_size = entry.get("window_size", "")
        prefix = entry.get("prefix", "")
        predicted = entry.get("predicted", "")
        actual = entry.get("actual", "")
        confidence = entry.get("confidence", 0.0)
        skipped = entry.get("skipped", False)
        all_anchor_attempts = entry.get("all_anchor_attempts", [])
        all_predictions = entry.get("all_predictions", [])
        
        print(f"Step {step}: Position {position}")
        print(f"  실제값: {actual}")
        print(f"  사용된 앵커: {anchor}")
        print(f"  사용된 윈도우: {window_size}")
        print(f"  Prefix: {prefix}")
        print(f"  예측값: {predicted}")
        print(f"  신뢰도: {confidence:.1f}%")
        print(f"  스킵: {skipped}")
        print()
        
        if all_anchor_attempts:
            print(f"  시도한 모든 앵커:")
            for attempt in all_anchor_attempts:
                att_anchor = attempt.get("anchor", "")
                att_skipped = attempt.get("skipped", False)
                att_conf = attempt.get("confidence", 0.0)
                att_pred = attempt.get("predicted", "")
                status = "스킵" if att_skipped else "성공"
                print(f"    - 앵커 {att_anchor}: {status}, 신뢰도 {att_conf:.1f}%, 예측 {att_pred}")
            print()
        
        if all_predictions:
            print(f"  해당 앵커에서 시도한 모든 윈도우:")
            for pred in all_predictions:
                ws = pred.get("window_size", "")
                conf = pred.get("confidence", 0.0)
                pred_val = pred.get("predicted", "")
                pfx = pred.get("prefix", "")
                # 해당 윈도우의 임계값 확인
                ws_thresh = window_thresholds.get(ws, 50)
                status = "통과" if conf >= ws_thresh else "스킵 (임계값 미만)"
                print(f"    - 윈도우 {ws}: {status}, 신뢰도 {conf:.1f}% (임계값 {ws_thresh}%), 예측 {pred_val}, prefix {pfx}")
            print()
        
        # 해당 position에서 가능한 모든 앵커와 윈도우 조합 계산
        print(f"  Position {position}에서 가능한 앵커-윈도우 조합:")
        for anchor_pos in anchors:
            for ws in window_sizes:
                if anchor_pos + ws - 1 == position:
                    # prefix 계산
                    prefix_len = ws - 1
                    if position >= prefix_len:
                        pfx = grid_string[position - prefix_len : position]
                        print(f"    - 앵커 {anchor_pos} + 윈도우 {ws} = prefix '{pfx}'")
        print()
        
        print("-" * 80)
        print()
        
        # Step 16 이후만 상세 분석
        if step >= 16:
            continue

if __name__ == "__main__":
    analyze_grid_string_1896()
