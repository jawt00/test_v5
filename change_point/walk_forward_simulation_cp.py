"""
Walk-forward Analysis 기반 신뢰도 패턴 예측 시뮬레이션

- 5회 연속 실패 방지 목표
- 윈도우 크기 8-12, 임계값 50-65% 범위 탐색
- 최소 표본 수 필터 적용
"""

import sys
from pathlib import Path

# 상위 폴더의 모듈을 import하기 위해 경로 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from svg_parser_module import get_change_point_db_connection
from change_point_prediction_module import (
    save_or_update_predictions_for_change_point_data,
    batch_validate_multi_window_scenario_cp,
)


def count_five_consecutive_losses(history):
    """
    히스토리에서 5회 이상 연속 실패 구간의 개수를 카운트
    
    Args:
        history: 검증 결과의 history 리스트
    
    Returns:
        int: 5회 이상 연속 실패가 발생한 구간의 개수
    """
    if not history:
        return 0
    
    failure_score = 0
    consecutive_failures = 0
    in_failure_sequence = False
    
    for entry in history:
        is_correct = entry.get("is_correct")
        # 예측이 수행된 경우만 카운트
        if is_correct is not None:
            if not is_correct:
                consecutive_failures += 1
                if consecutive_failures >= 5 and not in_failure_sequence:
                    # 5회 연속 실패 시작
                    in_failure_sequence = True
                    failure_score += 1
            else:
                # 일치하면 연속 실패 리셋
                consecutive_failures = 0
                in_failure_sequence = False
    
    return failure_score


def measure_performance(validation_results):
    """
    검증 결과에서 성과 지표 계산
    
    Args:
        validation_results: batch_validate_multi_window_scenario_cp() 반환값
    
    Returns:
        dict: {
            "mcl": Max Consecutive Losses,
            "total_bets": Total Bets,
            "win_rate": Win Rate (%),
            "failure_score": 5연패 발생 횟수
        }
    """
    if not validation_results or not validation_results.get("results"):
        return {
            "mcl": 0,
            "total_bets": 0,
            "win_rate": 0.0,
            "failure_score": 0,
        }
    
    # 전체 결과 집계
    all_history = []
    total_predictions = 0
    max_consecutive_failures = 0
    
    for result in validation_results["results"]:
        history = result.get("history", [])
        all_history.extend(history)
        total_predictions += result.get("total_predictions", 0)
        max_consecutive_failures = max(
            max_consecutive_failures,
            result.get("max_consecutive_failures", 0)
        )
    
    # Failure Score 계산 (전체 히스토리에서 5연패 구간 카운트)
    failure_score = count_five_consecutive_losses(all_history)
    
    # Win Rate 계산
    total_correct = sum(
        1 for entry in all_history
        if entry.get("is_correct") is True
    )
    win_rate = (total_correct / total_predictions * 100) if total_predictions > 0 else 0.0
    
    return {
        "mcl": max_consecutive_failures,
        "total_bets": total_predictions,
        "win_rate": win_rate,
        "failure_score": failure_score,
    }


def _run_single_combination(
    all_ids,
    initial_train_count,
    validation_count,
    window_size,
    threshold,
    method,
    min_sample_count,
    total_count,
):
    """
    단일 조합에 대한 Walk-forward Analysis 실행
    
    Args:
        all_ids: 전체 데이터 ID 리스트
        initial_train_count: 초기 학습 데이터 개수
        validation_count: 검증 데이터 개수
        window_size: 윈도우 크기
        threshold: 임계값
        method: 예측 방법
        min_sample_count: 최소 표본 수
        total_count: 전체 데이터 개수
    
    Returns:
        dict: result_entry 또는 None
    """
    try:
        # Walk-forward Analysis 실행
        train_ids = all_ids[:initial_train_count].copy()
        validation_start_idx = initial_train_count
        
        # 전체 검증 결과를 누적할 리스트
        all_validation_results = []
        
        # 검증 구간을 순회
        while validation_start_idx < total_count:
            validation_end_idx = min(
                validation_start_idx + validation_count,
                total_count
            )
            validation_ids = all_ids[validation_start_idx:validation_end_idx]
            
            if not validation_ids:
                break
            
            # 학습 데이터로 예측값 생성 (최소 표본 수 필터 적용)
            cutoff_id = train_ids[-1] if train_ids else None
            try:
                save_or_update_predictions_for_change_point_data(
                    cutoff_grid_string_id=cutoff_id,
                    window_sizes=(window_size,),
                    methods=(method,),
                    thresholds=(threshold,),
                    min_sample_count=min_sample_count,
                )
            except Exception as e:
                # 예측값 생성 실패 시 스킵
                return None
            
            # 검증 수행
            validation_cutoff_id = train_ids[-1] if train_ids else 0
            validation_result = batch_validate_multi_window_scenario_cp(
                cutoff_grid_string_id=validation_cutoff_id,
                window_sizes=(window_size,),
                method=method,
                threshold=threshold,
            )
            
            if validation_result and validation_result.get("results"):
                all_validation_results.append(validation_result)
            
            # 검증 완료 후 학습 데이터에 검증 데이터 추가 (Rolling 업데이트)
            train_ids.extend(validation_ids)
            validation_start_idx = validation_end_idx
        
        # 모든 검증 구간의 결과를 집계
        if not all_validation_results:
            return None
        
        # 결과 병합
        merged_results = {
            "results": [],
            "summary": {
                "total_grid_strings": 0,
                "avg_accuracy": 0.0,
                "max_consecutive_failures": 0,
                "avg_max_consecutive_failures": 0.0,
                "total_steps": 0,
                "total_failures": 0,
                "total_predictions": 0,
                "total_skipped": 0,
            },
            "grid_string_ids": [],
        }
        
        for vr in all_validation_results:
            merged_results["results"].extend(vr.get("results", []))
            merged_results["grid_string_ids"].extend(vr.get("grid_string_ids", []))
        
        # 요약 통계 재계산
        if merged_results["results"]:
            n = len(merged_results["results"])
            merged_results["summary"] = {
                "total_grid_strings": n,
                "avg_accuracy": sum(x["accuracy"] for x in merged_results["results"]) / n,
                "max_consecutive_failures": max(x["max_consecutive_failures"] for x in merged_results["results"]),
                "avg_max_consecutive_failures": sum(x["max_consecutive_failures"] for x in merged_results["results"]) / n,
                "total_steps": sum(x["total_steps"] for x in merged_results["results"]),
                "total_failures": sum(x["total_failures"] for x in merged_results["results"]),
                "total_predictions": sum(x["total_predictions"] for x in merged_results["results"]),
                "total_skipped": sum(x.get("total_skipped", 0) for x in merged_results["results"]),
            }
        
        # 성과 측정
        performance = measure_performance(merged_results)
        
        result_entry = {
            "window_size": window_size,
            "threshold": threshold,
            "mcl": performance["mcl"],
            "total_bets": performance["total_bets"],
            "win_rate": performance["win_rate"],
            "failure_score": performance["failure_score"],
            "is_passed": performance["failure_score"] == 0,
        }
        return result_entry
        
    except Exception as e:
        # 에러 발생 시 None 반환
        import traceback
        print(f"Error in _run_single_combination: {e}")
        print(traceback.format_exc())
        return None


def walk_forward_simulation_cp(
    window_sizes=(8, 9, 10, 11, 12),
    threshold_range=(50, 65, 1),  # (min, max, step)
    method="빈도 기반",
    initial_train_ratio=0.4,
    validation_ratio=0.1,
    min_sample_count=15,  # S_min
    progress_callback=None,
    max_workers=10,  # ThreadPoolExecutor 작업자 수
):
    """
    Walk-forward Analysis 기반 시뮬레이션
    
    Args:
        window_sizes: 윈도우 크기 목록
        threshold_range: 임계값 범위 (min, max, step)
        method: 예측 방법
        initial_train_ratio: 초기 학습 데이터 비율 (기본 0.4 = 40%)
        validation_ratio: 검증 데이터 비율 (기본 0.1 = 10%)
        min_sample_count: 최소 표본 수 (기본 15)
        progress_callback: 진행 상황 콜백 함수 (pct, message)
    
    Returns:
        dict: {
            "results": [
                {
                    "window_size": W,
                    "threshold": T,
                    "mcl": Max Consecutive Losses,
                    "total_bets": Total Bets,
                    "win_rate": Win Rate,
                    "failure_score": 5연패 발생 횟수,
                    "is_passed": failure_score == 0
                }
            ],
            "optimal_combinations": [
                {"window_size": W, "threshold": T, ...}  # MCL < 5 만족하는 조합
            ]
        }
    """
    conn = get_change_point_db_connection()
    try:
        # 진행 상황 콜백 호출 (데이터 로드 시작)
        if progress_callback:
            progress_callback(0.01, "데이터베이스 연결 완료. 데이터 로드 중...")
        
        # 전체 데이터를 시간 순서로 로드
        df_all = pd.read_sql_query(
            "SELECT id FROM preprocessed_grid_strings ORDER BY id",
            conn,
        )
        
        if len(df_all) == 0:
            if progress_callback:
                progress_callback(1.0, "⚠️ 경고: 데이터가 없습니다.")
            return {
                "results": [],
                "optimal_combinations": [],
            }
        
        total_count = len(df_all)
        all_ids = df_all["id"].tolist()
        
        if progress_callback:
            progress_callback(0.02, f"데이터 로드 완료: 총 {total_count:,}개 레코드")
        
        # 데이터 분할 계산
        initial_train_count = int(total_count * initial_train_ratio)
        validation_count = int(total_count * validation_ratio)
        
        if progress_callback:
            progress_callback(0.03, f"데이터 분할: 학습 {initial_train_count:,}개, 검증 {validation_count:,}개")
        
        # 임계값 목록 생성
        threshold_min, threshold_max, threshold_step = threshold_range
        thresholds = []
        t = threshold_min
        while t <= threshold_max:
            thresholds.append(round(t, 1))
            t += threshold_step
        
        # 결과 저장
        all_results = []
        total_combinations = len(window_sizes) * len(thresholds)
        
        if progress_callback:
            progress_callback(0.04, f"시뮬레이션 설정 완료: {len(window_sizes)}개 윈도우 × {len(thresholds)}개 임계값 = 총 {total_combinations}개 조합")
        
        if progress_callback:
            progress_callback(
                0.05,
                f"🚀 Walk-forward Analysis 시작 | "
                f"총 {total_combinations}개 조합 테스트 예정 | "
                f"최적화 모드: 예측값 캐싱 + ThreadPoolExecutor ({max_workers}개 작업자)"
            )
        
        completed_count = 0
        start_time = time.time()
        
        # 단계 1: 윈도우별로 예측값 생성 (캐싱)
        if progress_callback:
            progress_callback(0.06, f"📦 예측값 생성 중... (윈도우별 캐싱)")
        
        # 윈도우별로 예측값을 미리 생성하여 재사용
        # 각 윈도우에 대해 모든 임계값에 대한 예측값을 한 번에 생성
        for window_idx, window_size in enumerate(window_sizes):
            if progress_callback:
                window_progress = 0.06 + (window_idx / len(window_sizes)) * 0.10
                progress_callback(
                    window_progress,
                    f"📦 윈도우 {window_size} 예측값 생성 중... ({window_idx + 1}/{len(window_sizes)})"
                )
            
            # 해당 윈도우에 대한 모든 임계값의 예측값을 한 번에 생성
            try:
                save_or_update_predictions_for_change_point_data(
                    cutoff_grid_string_id=None,  # 전체 데이터 사용
                    window_sizes=(window_size,),
                    methods=(method,),
                    thresholds=thresholds,  # 모든 임계값 한 번에 생성
                    min_sample_count=min_sample_count,
                )
            except Exception as e:
                if progress_callback:
                    progress_callback(
                        window_progress,
                        f"⚠️ 윈도우 {window_size} 예측값 생성 실패: {str(e)}"
                    )
        
        if progress_callback:
            progress_callback(0.16, f"✅ 예측값 생성 완료. 검증 시작...")
        
        # 단계 2: ThreadPoolExecutor를 사용하여 병렬 검증
        # 조합 목록 생성
        combinations = []
        for window_size in window_sizes:
            for threshold in thresholds:
                combinations.append((window_size, threshold))
        
        def validate_single_combination(args):
            """단일 조합 검증 함수 (ThreadPoolExecutor용)"""
            (window_size, threshold, all_ids_local, initial_train_count_local, 
             validation_count_local, total_count_local, method_local) = args
            try:
                # 예측값은 이미 생성되어 있으므로 검증만 수행
                # Walk-forward Analysis를 위해 각 검증 구간별로 실행
                train_ids = all_ids_local[:initial_train_count_local].copy()
                validation_start_idx = initial_train_count_local
                all_validation_results = []
                
                # 검증 구간을 순회
                while validation_start_idx < total_count_local:
                    validation_end_idx = min(
                        validation_start_idx + validation_count_local,
                        total_count_local
                    )
                    validation_ids = all_ids_local[validation_start_idx:validation_end_idx]
                    
                    if not validation_ids:
                        break
                    
                    # 검증 수행 (예측값은 이미 생성되어 있음)
                    validation_cutoff_id = train_ids[-1] if train_ids else 0
                    validation_result = batch_validate_multi_window_scenario_cp(
                        cutoff_grid_string_id=validation_cutoff_id,
                        window_sizes=(window_size,),
                        method=method_local,
                        threshold=threshold,
                    )
                    
                    if validation_result and validation_result.get("results"):
                        all_validation_results.append(validation_result)
                    
                    # 검증 완료 후 학습 데이터에 검증 데이터 추가 (Rolling 업데이트)
                    train_ids.extend(validation_ids)
                    validation_start_idx = validation_end_idx
                
                # 결과 집계
                if not all_validation_results:
                    return None
                
                # 결과 병합
                merged_results = {
                    "results": [],
                    "summary": {
                        "total_grid_strings": 0,
                        "avg_accuracy": 0.0,
                        "max_consecutive_failures": 0,
                        "avg_max_consecutive_failures": 0.0,
                        "total_steps": 0,
                        "total_failures": 0,
                        "total_predictions": 0,
                        "total_skipped": 0,
                    },
                    "grid_string_ids": [],
                }
                
                for vr in all_validation_results:
                    merged_results["results"].extend(vr.get("results", []))
                    merged_results["grid_string_ids"].extend(vr.get("grid_string_ids", []))
                
                # 요약 통계 재계산
                if merged_results["results"]:
                    n = len(merged_results["results"])
                    merged_results["summary"] = {
                        "total_grid_strings": n,
                        "avg_accuracy": sum(x["accuracy"] for x in merged_results["results"]) / n,
                        "max_consecutive_failures": max(x["max_consecutive_failures"] for x in merged_results["results"]),
                        "avg_max_consecutive_failures": sum(x["max_consecutive_failures"] for x in merged_results["results"]) / n,
                        "total_steps": sum(x["total_steps"] for x in merged_results["results"]),
                        "total_failures": sum(x["total_failures"] for x in merged_results["results"]),
                        "total_predictions": sum(x["total_predictions"] for x in merged_results["results"]),
                        "total_skipped": sum(x.get("total_skipped", 0) for x in merged_results["results"]),
                    }
                
                # 성과 측정
                performance = measure_performance(merged_results)
                
                return {
                    "window_size": window_size,
                    "threshold": threshold,
                    "mcl": performance["mcl"],
                    "total_bets": performance["total_bets"],
                    "win_rate": performance["win_rate"],
                    "failure_score": performance["failure_score"],
                    "is_passed": performance["failure_score"] == 0,
                }
            except Exception as e:
                import traceback
                print(f"Error in validate_single_combination (윈도우={window_size}, 임계값={threshold}): {e}")
                print(traceback.format_exc())
                return None
        
        # ThreadPoolExecutor로 병렬 검증 실행
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 모든 작업 제출 (필요한 인자 모두 전달)
            future_to_combo = {
                executor.submit(
                    validate_single_combination,
                    (window_size, threshold, all_ids, initial_train_count, 
                     validation_count, total_count, method)
                ): (window_size, threshold)
                for window_size, threshold in combinations
            }
            
            # 완료된 작업부터 처리
            last_callback_time = time.time()
            callback_interval = 0.5  # 0.5초마다 콜백 호출
            
            for future in as_completed(future_to_combo):
                combo = future_to_combo[future]
                window_size, threshold = combo
                completed_count += 1
                
                try:
                    result_entry = future.result()
                    
                    if result_entry is not None:
                        all_results.append(result_entry)
                    
                    # 진행 상황 업데이트
                    current_time = time.time()
                    if progress_callback and (current_time - last_callback_time >= callback_interval or completed_count == total_combinations):
                        last_callback_time = current_time
                        
                        # 진행률 계산 (예측값 생성 16%, 검증 84%)
                        progress = 0.16 + (completed_count / total_combinations) * 0.84
                        elapsed = time.time() - start_time
                        
                        if completed_count > 0:
                            avg_time_per_combo = elapsed / completed_count
                            remaining = (total_combinations - completed_count) * avg_time_per_combo
                            
                            # 경과 시간 포맷팅
                            elapsed_hours = int(elapsed // 3600)
                            elapsed_min = int((elapsed % 3600) // 60)
                            elapsed_sec = int(elapsed % 60)
                            
                            if elapsed_hours > 0:
                                elapsed_str = f"{elapsed_hours}시간 {elapsed_min}분 {elapsed_sec}초"
                            elif elapsed_min > 0:
                                elapsed_str = f"{elapsed_min}분 {elapsed_sec}초"
                            else:
                                elapsed_str = f"{elapsed_sec}초"
                            
                            # 남은 시간 포맷팅
                            remaining_hours = int(remaining // 3600)
                            remaining_min = int((remaining % 3600) // 60)
                            remaining_sec = int(remaining % 60)
                            
                            if remaining_hours > 0:
                                remaining_str = f"{remaining_hours}시간 {remaining_min}분 {remaining_sec}초"
                            elif remaining_min > 0:
                                remaining_str = f"{remaining_min}분 {remaining_sec}초"
                            else:
                                remaining_str = f"{remaining_sec}초"
                            
                            # 진행률 퍼센트
                            progress_pct = progress * 100
                            
                            # 현재 최고 결과 추적
                            best_result = None
                            if all_results:
                                passed_results = [r for r in all_results if r.get("is_passed", False)]
                                if passed_results:
                                    best_result = min(passed_results, key=lambda x: (x["threshold"], x["window_size"]))
                            
                            # 상태 메시지 구성
                            status_parts = [
                                f"진행률: {progress_pct:.1f}% ({completed_count}/{total_combinations})",
                                f"경과 시간: {elapsed_str}",
                                f"예상 남은 시간: {remaining_str}",
                            ]
                            
                            if best_result:
                                status_parts.append(
                                    f"현재 최고: 윈도우={best_result['window_size']}, "
                                    f"임계값={best_result['threshold']}% (MCL={best_result['mcl']}, "
                                    f"Failure Score={best_result['failure_score']})"
                                )
                            else:
                                status_parts.append(f"처리 중: 윈도우={window_size}, 임계값={threshold}%")
                            
                            status_parts.append(f"병렬 작업자: {max_workers}개")
                            
                            progress_callback(progress, " | ".join(status_parts))
                
                except Exception as e:
                    # 에러 발생 시 계속 진행
                    if progress_callback:
                        error_msg = str(e)
                        progress_callback(
                            progress,
                            f"❌ 조합 (윈도우={window_size}, 임계값={threshold}%) 오류: {error_msg} (계속 진행)"
                        )
                    pass
        
        # 최적 조합 찾기 (MCL < 5 만족하는 조합 중 가장 낮은 T)
        optimal_combinations = []
        for result in all_results:
            if result["mcl"] < 5:  # MCL < 5 만족
                optimal_combinations.append(result)
        
        # 임계값 기준으로 정렬 (낮은 순)
        optimal_combinations.sort(key=lambda x: (x["threshold"], x["window_size"]))
        
        if progress_callback:
            total_elapsed = time.time() - start_time
            elapsed_hours = int(total_elapsed // 3600)
            elapsed_min = int((total_elapsed % 3600) // 60)
            elapsed_sec = int(total_elapsed % 60)
            
            if elapsed_hours > 0:
                elapsed_str = f"{elapsed_hours}시간 {elapsed_min}분 {elapsed_sec}초"
            elif elapsed_min > 0:
                elapsed_str = f"{elapsed_min}분 {elapsed_sec}초"
            else:
                elapsed_str = f"{elapsed_sec}초"
            
            optimal_count = len(optimal_combinations)
            status_msg = (
                f"✅ 완료! | "
                f"총 소요 시간: {elapsed_str} | "
                f"테스트 조합: {len(all_results)}개 | "
                f"MCL < 5 만족 조합: {optimal_count}개"
            )
            
            if optimal_count > 0:
                best = optimal_combinations[0]
                status_msg += (
                    f" | 최적 조합: 윈도우={best['window_size']}, "
                    f"임계값={best['threshold']}% (MCL={best['mcl']}, "
                    f"Failure Score={best['failure_score']})"
                )
            
            progress_callback(1.0, status_msg)
        
        return {
            "results": all_results,
            "optimal_combinations": optimal_combinations,
        }
    finally:
        conn.close()
