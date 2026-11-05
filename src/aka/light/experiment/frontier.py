"""Observed per-Shape minima from the Campaign's standard full Evaluations."""
from __future__ import annotations

import logging
import math
from datetime import datetime

from aka.light.evaluation.public_result import canonical_result, input_scope
from aka.light.evaluation.provider import result_from_response
from aka.light.experiment.store import MeasurementStore, RECORD_ID, read_record_header


def latest_gateway_result(
    store: MeasurementStore,
    *,
    record_ids: set[str] | None = None,
) -> dict | None:
    """Return a compact, trusted summary of the newest terminal Gateway Record."""
    candidates: list[tuple[float, str]] = []
    for path in (store.root / "records").glob("gateway-*/record.json"):
        record_id = path.parent.name
        try:
            if not RECORD_ID.fullmatch(record_id) or (
                record_ids is not None and record_id not in record_ids
            ):
                continue
            header = read_record_header(path)
            if header["gateway_record_id"] != record_id:
                raise ValueError("Gateway Record identity differs from its path")
            candidates.append(
                (datetime.fromisoformat(header["created_at"]).timestamp(), record_id)
            )
        except (OSError, ValueError, KeyError, TypeError, RecursionError):
            logging.getLogger(__name__).warning(
                "Skipping invalid latest-result header %s", record_id
            )
    for created_at, record_id in sorted(candidates, reverse=True):
        try:
            record = store.read(record_id)
            public = canonical_result(
                record["response"],
                record["request"],
                record_id=record_id,
                kernels=record["kernels"],
            )
            result = public.get("result")
            result = result if isinstance(result, dict) else {}
            options = record["request"].get("options", {})
            options = options if isinstance(options, dict) else {}
            latencies = result.get("latency_us_by_shape")
            shape_count = len(latencies) if isinstance(latencies, dict) else None
            geomean = result.get("latency_us_geomean")
            latency_values = (
                [
                    float(value)
                    for value in latencies.values()
                    if type(value) in {int, float}
                    and math.isfinite(float(value))
                    and float(value) > 0
                ]
                if isinstance(latencies, dict)
                else []
            )
            if (
                (type(geomean) not in {int, float} or not math.isfinite(float(geomean)))
                and latency_values
            ):
                geomean = math.exp(
                    sum(math.log(value) for value in latency_values) / len(latency_values)
                )
            operation = public.get("operation") or record["operation"]
            scope = {
                "mode": result.get("mode"),
                "input_scope": (
                    result.get("input_scope")
                    if isinstance(result.get("input_scope"), str)
                    else input_scope(options)
                    if operation == "evaluate"
                    else None
                ),
                "measured_shape_count": shape_count,
            }
            return {
                "created_at_epoch_s": created_at,
                "gateway_record_id": record_id,
                "operation": operation,
                "status": public.get("status"),
                "kernel_commit_id": (
                    public.get("candidate_kernel_commit_id")
                    or public.get("kernel_commit_id")
                ),
                "scope": {key: value for key, value in scope.items() if value is not None},
                "correct": result.get("correct"),
                "correctness_status": (
                    result.get("correctness", {}).get("status")
                    if isinstance(result.get("correctness"), dict)
                    else None
                ),
                "latency_us_geomean": geomean,
                "speedup": result.get("speedup"),
                "error": result.get("error"),
                "outcome": result.get("outcome"),
            }
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            logging.getLogger(__name__).warning(
                "Skipping invalid latest-result evidence %s", record_id
            )
    return None


def kernel_pareto_frontier(
    store: MeasurementStore,
    *,
    record_ids: set[str] | None = None,
) -> dict:
    """Read existing facts only; a per-Shape envelope is not a measured composite Kernel.

    Use the same ordinary-full-Evaluate eligibility as nomination, never Profile,
    ABBA, custom-input or subset timings. The store is Campaign-scoped and shared
    across Episodes. Unlike Runtime's Registry, Light does not distinguish Agent
    and controller-origin full Evaluations; both are eligible saved observations.
    """
    winners: dict[str, tuple[float, str, str]] = {}
    best_full: tuple[float, str, str] | None = None
    evaluation_count = 0
    for path in (store.root / "records").glob("gateway-*/record.json"):
        record_id = path.parent.name
        try:
            if not RECORD_ID.fullmatch(record_id) or (
                record_ids is not None and record_id not in record_ids
            ):
                continue
            # Do not load response bodies or source artifacts for other operations.
            header = read_record_header(path)
            if header["operation"] != "evaluate":
                continue
            record = store.read(record_id)
            if (record["operation"] != "evaluate" or record["request"]["operation"] != "evaluate"
                    or record.get("cacheable") is not True):
                continue
            options = record["request"]["options"]
            command = options.get("command", [])
            if (
                (options.get("evaluation_mode") or "full") != "full"
                or options.get("evaluation_policy", {}).get("mode", "full") != "full"
                or options.get("evaluation_input_path")
                or options.get("evaluation_shapes_path")
                or options.get("baseline_path")
                or command[:2] != ["python3", "test_kernel.py"]
                or any(arg == "--shape-id" or arg.startswith("--shape-id=") for arg in command)
            ):
                continue
            response = record["response"]
            if response.get("exit_code") != 0 or response.get("truncated"):
                continue
            result = result_from_response(response, "evaluate")
            if (not result or result.get("all_pass") is not True or result.get("error")
                    or result.get("mode", "full") != "full"):
                continue
            values = result.get("latency_us_by_shape")
            if not isinstance(values, dict):
                continue
            latencies = {
                shape: float(value) for shape, value in values.items()
                if isinstance(shape, str) and type(value) in {int, float}
                and math.isfinite(value) and value > 0
            }
            if not latencies:
                continue
            kernel_id = record["kernels"]["candidate"]["kernel_id"]
            geomean = result.get("latency_us_geomean")
            if type(geomean) not in {int, float} or not math.isfinite(float(geomean)) or geomean <= 0:
                geomean = math.exp(
                    sum(math.log(value) for value in latencies.values()) / len(latencies)
                )
        except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError, OverflowError):
            # An interrupted/corrupt historical record must not break every query;
            # details stay in the Supervisor log, not in the Agent response.
            logging.getLogger(__name__).warning("Skipping invalid frontier evidence %s", record_id)
            continue
        evaluation_count += 1
        full_candidate = (float(geomean), kernel_id, record_id)
        if best_full is None or full_candidate < best_full:
            best_full = full_candidate
        for shape, latency in latencies.items():
            candidate = (latency, kernel_id, record_id)
            if shape not in winners or candidate < winners[shape]:
                winners[shape] = candidate
    return {
        "best_by_shape": {
            shape: {
                "kernel_commit_id": kernel,
                "gateway_record_id": record,
                "latency_us": latency,
            }
            for shape, (latency, kernel, record) in sorted(winners.items())
        },
        "shape_count": len(winners),
        "winner_kernel_commit_ids": sorted({kernel for _, kernel, _ in winners.values()}),
        "evaluation_count": evaluation_count,
        "best_full_evaluation": (
            {
                "latency_us_geomean": best_full[0],
                "kernel_commit_id": best_full[1],
                "gateway_record_id": best_full[2],
            }
            if best_full is not None
            else None
        ),
    }
