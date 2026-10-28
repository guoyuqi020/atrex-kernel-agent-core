"""Public, model-free checks for the Light Agent execution environment.

Run ``python -m aka.light.agent.preflight`` in the Agent's execution container.
This checks projected-proc shim compilation, not Bubblewrap namespace admission.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Mapping


ATREX_BENCH_VERSION_ENV = "ATREX_SANDBOX_ATREX_BENCH_VERSION"

def check_projected_proc() -> None:
    """Verify the packaged shim and compiler without launching an Agent."""
    if sys.platform != "linux":
        raise RuntimeError("projected-proc requires a Linux execution container")
    from .proc_view import _install_maps_shim

    with tempfile.TemporaryDirectory(prefix="aka-proc-preflight-") as directory:
        # A valid cache must not hide a missing compiler/header in this container.
        _install_maps_shim(Path(directory), force_rebuild=True)


def _agate_version(*, executable: str = "agate") -> str:
    """Return the installed public Agate SDK version.

    ``executable`` remains as a compatibility keyword for callers of the first
    version-contract implementation; Gateway execution no longer depends on or
    invokes the CLI binary.
    """
    del executable
    try:
        from atrex_gateway_client import __version__
    except ModuleNotFoundError as exc:
        raise RuntimeError("cannot import the Agate SDK") from exc
    if not isinstance(__version__, str) or not __version__.strip():
        raise RuntimeError("Agate SDK reported no package version")
    return __version__.strip()


def check_required_versions(
    required_agate_version: str,
    required_atrex_bench_version: str,
    *,
    environment: Mapping[str, str] | None = None,
    agate_executable: str = "agate",
) -> dict[str, str]:
    """Fail unless the local client and remote evaluator pin match exactly."""
    required_agate_version = required_agate_version.strip()
    required_atrex_bench_version = required_atrex_bench_version.strip()
    if not required_agate_version:
        raise ValueError("required Agate version must not be empty")
    if not required_atrex_bench_version:
        raise ValueError("required Atrex-Bench version must not be empty")

    actual_agate_version = _agate_version(executable=agate_executable)
    if actual_agate_version != required_agate_version:
        raise RuntimeError(
            "Agate version mismatch: "
            f"required {required_agate_version}, found {actual_agate_version}"
        )

    configured_atrex_bench_version = (os.environ if environment is None else environment).get(
        ATREX_BENCH_VERSION_ENV, ""
    ).strip()
    if not configured_atrex_bench_version:
        raise RuntimeError(
            f"{ATREX_BENCH_VERSION_ENV} is required; refusing an unpinned remote evaluator"
        )
    if configured_atrex_bench_version != required_atrex_bench_version:
        raise RuntimeError(
            "Atrex-Bench version mismatch: "
            f"required {required_atrex_bench_version}, "
            f"configured {configured_atrex_bench_version}"
        )
    return {
        "agate": actual_agate_version,
        "atrex_bench": configured_atrex_bench_version,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--require-agate-version")
    parser.add_argument("--require-atrex-bench-version")
    args = parser.parse_args()
    if bool(args.require_agate_version) != bool(args.require_atrex_bench_version):
        parser.error(
            "--require-agate-version and --require-atrex-bench-version must be supplied together"
        )
    try:
        check_projected_proc()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(
            f"Projected-proc preflight failed ({type(exc).__name__}); "
            "check Linux, cc/C development headers, packaged proc_maps.c and "
            "writable temporary storage in this execution container",
            file=sys.stderr,
        )
        return 1
    versions = None
    if args.require_agate_version:
        try:
            versions = check_required_versions(
                args.require_agate_version,
                args.require_atrex_bench_version,
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
            print(
                f"Light installation version preflight failed ({type(exc).__name__}): {exc}",
                file=sys.stderr,
            )
            return 1
    result: dict[str, object] = {"check": "projected_proc_compile", "status": "ready"}
    if versions is not None:
        result["versions"] = versions
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
