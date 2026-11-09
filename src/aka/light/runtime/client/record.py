#!/usr/bin/env python3
"""Read one projected Gateway Record without submitting a Runtime request.

Examples:
  python3 tools/record.py "$GATEWAY_RECORD_ID" --summary
  python3 tools/record.py "$GATEWAY_RECORD_ID" --field result.correct
  python3 tools/record.py "$GATEWAY_RECORD_ID" --shape-id 7
  python3 tools/record.py "$GATEWAY_RECORD_ID" --keys result
  python3 tools/record.py "$GATEWAY_RECORD_ID" --text-field result.stdout --offset 0 --max-bytes 4096

Ordinary field output is bounded. Use --keys to discover structure and
--text-field to read a bounded byte range from long stdout/stderr text.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import NoReturn

RECORD_ID = re.compile(r"gateway-[0-9a-f]{32}")
MAX_RECORD_BYTES = 32 * 1024 * 1024
MAX_FIELD_OUTPUT_BYTES = 64 * 1024
MAX_TEXT_BYTES = 16 * 1024
DEFAULT_TEXT_BYTES = 4096


def fail(message: str) -> NoReturn:
    print(json.dumps({"ok": False, "error": message}, ensure_ascii=False), file=sys.stderr)
    raise SystemExit(2)


def load_record(record_id: str) -> dict:
    if RECORD_ID.fullmatch(record_id) is None:
        fail("record ID must match gateway-<32 lowercase hex characters>")
    root = Path.cwd() / "records"
    path = root / f"{record_id}.json"
    try:
        if stat.S_ISLNK(os.lstat(root).st_mode) or not stat.S_ISDIR(os.lstat(root).st_mode):
            fail("records/ must be a real directory")
        metadata = os.lstat(path)
        if not stat.S_ISREG(metadata.st_mode):
            fail("Gateway Record path must be a regular file")
        if metadata.st_size > MAX_RECORD_BYTES:
            fail(f"Gateway Record exceeds {MAX_RECORD_BYTES} bytes")
        value = json.loads(path.read_bytes())
    except FileNotFoundError:
        fail(f"Gateway Record is not projected at records/{record_id}.json")
    except (OSError, ValueError, UnicodeError) as error:
        fail(f"cannot read Gateway Record: {error}")
    if not isinstance(value, dict) or value.get("gateway_record_id") != record_id:
        fail("Gateway Record identity is invalid")
    return value


def select(value: object, path: str) -> object:
    current = value
    if not path:
        return current
    for segment in path.split("."):
        if isinstance(current, dict) and segment in current:
            current = current[segment]
        elif isinstance(current, list) and segment.isdigit() and int(segment) < len(current):
            current = current[int(segment)]
        else:
            fail(f"field path does not exist: {path}")
    return current


def emit(value: object) -> None:
    payload = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True)
    if len(payload.encode("utf-8")) > MAX_FIELD_OUTPUT_BYTES:
        fail(
            f"selected value exceeds {MAX_FIELD_OUTPUT_BYTES} output bytes; use --keys, "
            "--shape-id, a narrower --field, or bounded --text-field"
        )
    print(payload)


def summary(record: dict) -> dict:
    result = record.get("result")
    result = result if isinstance(result, dict) else {}
    keys = (
        "correct", "passed", "exit_code", "mode", "input_scope",
        "latency_us_geomean", "latency_us_arith_mean", "performance_score",
        "speedup", "improvement_pct", "outcome", "reason",
    )
    compact = {key: result[key] for key in keys if key in result}
    if isinstance(result.get("correctness"), dict):
        compact["correctness"] = {
            key: result["correctness"][key]
            for key in ("status", "rel_err", "max_abs_err", "max_rel_err")
            if key in result["correctness"]
            and isinstance(result["correctness"][key], (str, int, float, bool, type(None)))
        }
    error = result.get("error")
    if isinstance(error, str):
        compact["error"] = {"message": error[:4096]}
    elif isinstance(error, dict):
        compact["error"] = {}
        for key in (
            "code", "reason", "error_class", "message", "timeout_s",
            "stage", "http_status", "outcome", "candidate_repairable",
        ):
            value = error.get(key)
            if isinstance(value, str):
                compact["error"][key] = value[:4096]
            elif isinstance(value, (int, float, bool, type(None))):
                compact["error"][key] = value
    for stream in ("stdout", "stderr"):
        if isinstance(result.get(stream), str):
            compact[f"{stream}_bytes"] = len(result[stream].encode("utf-8"))
    return {
        key: record[key]
        for key in (
            "operation", "status", "gateway_record_id", "kernel_commit_id",
            "candidate_kernel_commit_id", "baseline_kernel_commit_id",
        )
        if key in record
    } | {"result": compact, "result_fields": sorted(str(key) for key in result)}


def shape(record: dict, shape_id: str) -> dict:
    result = record.get("result")
    if not isinstance(result, dict):
        fail("Gateway Record has no structured result")
    latencies = {}
    direct = result.get("latency_us_by_shape")
    if isinstance(direct, dict) and shape_id in direct:
        latencies["candidate"] = direct[shape_id]
    for side in ("baseline", "candidate"):
        side_result = result.get(side)
        side_values = side_result.get("latency_us_by_shape") if isinstance(side_result, dict) else None
        if isinstance(side_values, dict) and shape_id in side_values:
            latencies[side] = side_values[shape_id]
    if not latencies:
        fail(f"Shape {shape_id!r} has no latency in this Gateway Record")
    return {
        "gateway_record_id": record["gateway_record_id"],
        "shape_id": shape_id,
        "latency_us": latencies,
        "correct": result.get("correct"),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("record_id")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--summary", action="store_true", help="Print a compact result summary (default).")
    actions.add_argument("--field", metavar="PATH", help="Print one bounded dotted JSON field.")
    actions.add_argument("--keys", metavar="PATH", help="List keys or indexes below one dotted field.")
    actions.add_argument("--shape-id", metavar="ID", help="Print latency for one Shape ID.")
    actions.add_argument("--text-field", metavar="PATH", help="Read a bounded byte range from a text field.")
    parser.add_argument("--offset", type=int, default=0, help="Byte offset for --text-field (default: 0).")
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_TEXT_BYTES,
                        help=f"Bytes for --text-field (1..{MAX_TEXT_BYTES}; default: {DEFAULT_TEXT_BYTES}).")
    args = parser.parse_args(argv)
    if args.offset < 0:
        parser.error("--offset must be non-negative")
    if not 1 <= args.max_bytes <= MAX_TEXT_BYTES:
        parser.error(f"--max-bytes must be 1..{MAX_TEXT_BYTES}")
    record = load_record(args.record_id)
    if args.field is not None:
        emit(select(record, args.field))
    elif args.keys is not None:
        value = select(record, args.keys)
        if isinstance(value, dict):
            emit(sorted(str(key) for key in value))
        elif isinstance(value, list):
            emit(list(range(len(value))))
        else:
            fail(f"field {args.keys!r} is not an object or list")
    elif args.shape_id is not None:
        emit(shape(record, args.shape_id))
    elif args.text_field is not None:
        value = select(record, args.text_field)
        if not isinstance(value, str):
            fail(f"field {args.text_field!r} is not text")
        raw = value.encode("utf-8")
        chunk = raw[args.offset:args.offset + args.max_bytes]
        emit({
            "field": args.text_field,
            "offset": args.offset,
            "next_offset": args.offset + len(chunk),
            "total_bytes": len(raw),
            "eof": args.offset + len(chunk) >= len(raw),
            "text": chunk.decode("utf-8", errors="replace"),
        })
    else:
        emit(summary(record))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
