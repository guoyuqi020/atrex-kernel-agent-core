"""Local source-contract repair, independent of Runtime tools and report submission."""
from __future__ import annotations

import json
import runpy
from pathlib import Path
from typing import Callable


def source_boundary_prompt(workspace: Path) -> str:
    path = workspace / ".runtime/source-boundaries.md"
    return path.read_text(encoding="utf-8") if path.exists() else ""


class SourceCompletionCheck:
    """Use the controller's read-only validator before accepting a terminal handoff."""

    def __init__(self, workspace: Path, next_check: Callable[[float], str | None] | None = None):
        self.workspace = workspace
        self.next_check = next_check
        self.invalid = False
        policy = workspace / ".runtime/source-policy.json"
        self.policy = json.loads(policy.read_text()) if policy.exists() else None
        self.validate = (
            runpy.run_path(str(workspace / ".runtime/source-validator.py"))["validate_source_tree"]
            if self.policy is not None else None
        )

    @property
    def exhausted_exit_status(self) -> int:
        # Invalid source is a rejected candidate, not permission for an unbounded retry.
        return 128 if self.invalid else 127

    def __call__(self, remaining_timeout_seconds: float) -> str | None:
        self.invalid = False
        if self.validate is not None:
            try:
                self.validate(self.workspace / "work/kernel", **self.policy)
            except ValueError as error:
                self.invalid = True
                return (
                    "Your candidate source failed final validation; the attempt has not completed.\n"
                    f"Exact error: {error}\n"
                    + source_boundary_prompt(self.workspace)
                    + "\nCorrect the source layout in work/kernel and leave the valid final candidate there. "
                    "Move unauthorized notes/build products to tools/memory/ or scratch/. "
                    "Do not bypass read-only protection or broaden editable_roots. "
                    "Retain valid kernel changes inside the allowed roots. "
                    + ("Then submit the corrected terminal attempt report. " if self.next_check else "")
                    + "This continuation shares the original remaining time and token budget."
                )
        return self.next_check(remaining_timeout_seconds) if self.next_check else None
