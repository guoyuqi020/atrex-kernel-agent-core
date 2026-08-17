#!/usr/bin/env python3
"""ATREX Kernel Agent Core entrypoint for one Runtime-selected session phase."""

from __future__ import annotations

import os

from sessions.attempt import main as attempt_main
from sessions.lineage_bootstrap import main as baseline_main
from sessions.problem_generalization import main as generalization_main


def main() -> int:
    phase = os.environ.get("ATREX_CORE_PHASE")
    if not phase:
        raise ValueError("ATREX_CORE_PHASE is required")
    if phase == "optimization_attempt":
        return attempt_main()
    if phase == "framework_baseline":
        return baseline_main()
    if phase == "problem_generalization":
        return generalization_main()
    raise ValueError(f"unsupported Core session phase: {phase}")


if __name__ == "__main__":
    raise SystemExit(main())
