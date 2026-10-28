"""Additional GPU operations, separate from measurement storage and acceptance.

The gateway argument is the running executor module's public API. Passing that
instance keeps job tracking/cancellation shared with the CLI entry point, which
may be loaded as __main__ rather than supervisor.gateway.
"""
from __future__ import annotations

import json
from pathlib import Path
from aka.light.evaluation.policy import PERFORMANCE_TIMEOUT, validate_abba_repeats

from aka.light.evaluation.projection import (
    GATEWAY_ERROR_PREFIX,
    SOURCE_ERROR_PREFIX,
    candidate_source_rejection,
    emit_gateway_error,
    gateway_error_result,
)


def diagnostic(gateway, args, workspace: Path, queue_wait_grace: int) -> int:
    if args.command or args.ssh:
        raise ValueError("Check/Disassemble require the typed Gateway; no command or SSH transport")
    request = gateway.build_typed_request(
        workspace, args.hardware, args.timeout, args.env, [], args.kind,
        arch=args.arch, sanitize=args.sanitize, disassembly_format=args.disassembly_format,
        requirements=args.requirement or (), deps_mode=args.deps_mode,
    )
    if args.dry_run:
        print(json.dumps({"kind": args.kind, "candidate_bytes": len(request["candidate"].encode())}))
        return 0
    from aka.light.evaluation.source_transport import run as run_tree
    process = run_tree(gateway, args, request, args.kind, queue_wait_grace)
    source_error = candidate_source_rejection(process)
    if source_error is not None:
        print(SOURCE_ERROR_PREFIX + json.dumps(source_error))
        return process.returncode or 1
    job = gateway.parse_job_response(process.stdout or "")
    if not job or job.get("status") != "succeeded" or not isinstance(job.get("result"), dict):
        if not emit_gateway_error(process, args.kind):
            print(GATEWAY_ERROR_PREFIX + json.dumps(gateway_error_result({
                "operation": args.kind, "error": process.stderr or "Gateway returned no result", "outcome": "unknown",
            })))
        return process.returncode or 1
    prefix = "[sandbox] CHECK_JSON=" if args.kind == "check" else "[sandbox] DISASSEMBLE_JSON="
    print(prefix + json.dumps(job["result"]))
    return 0 if job["result"].get("passed", True) else 1


def validate_comparison(args) -> None:
    if args.kind != "run" or args.evaluation_mode == "correctness_only":
        raise ValueError("--baseline-path requires --kind run in full mode")
    validate_abba_repeats(args.comparison_repeats)
    if (args.command or args.evaluation_input_path or args.evaluation_shapes_path
            or args.shape_id):
        raise ValueError("ABBA uses the canonical full contract; command/input/shape overrides are unsupported")
    comparison_run_timeout(args)


def comparison_run_timeout(args) -> int:
    available = (args.timeout - 30) // (2 * args.comparison_repeats)
    requested = getattr(args, "comparison_run_timeout", None)
    # Request-level repeat overrides are valid without a matching allocation
    # override. Preserve the ordinary Performance timeout when it fits, and
    # otherwise divide the enclosing allocation across the complete schedule.
    # An explicit per-run timeout remains strict and is never silently reduced.
    seconds = min(PERFORMANCE_TIMEOUT, available) if requested is None else requested
    if seconds <= 0 or seconds > available:
        raise ValueError("--comparison-run-timeout must be positive and fit the full ABBA allocation schedule")
    return seconds


def compare(gateway, args, workspace: Path, queue_wait_grace: int) -> int:
    from aka.light.evaluation.native_abba import compare as native_compare
    return native_compare(gateway, args, workspace, queue_wait_grace)
