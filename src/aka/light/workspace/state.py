"""Workspace state readers: version facts, optional legacy memory and Git state.

Git is the single source of truth for a committed win; everything here reads mechanical
facts rather than model-reported ones.
"""
from __future__ import annotations


import hashlib
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Optional

# Re-export shared facts/fields for existing callers; policy stays local.
from aka.legacy.task.candidate_workspace.state import (
    git_head,
    read_framework_baseline,
    _normalize_commit_hash,
)
from aka.legacy.task.git import git_path_blob, git_worktree_blob

from aka.light.workflow.constants import FRAMEWORK_BASELINE_FILE, STALL_STATE_FILE
from aka.light.workspace.versions import STATE_DIRECTORY, version_record_path


def latest_version(workspace: Path) -> int:
    """Largest STRICT integer version in controller facts or legacy memory.

    Note: int('71_512') == 71512 in Python (PEP 515 underscores-in-numeric-literals).
    Experimental files named like v71_512.json (an experiment branch for the v71 round) must
    NOT count as v71512 — they're scratch variants of v71, not real iterations. We therefore
    accept only stems matching ^v\\d+\\.json$ exactly.
    """
    _STRICT = re.compile(r"^v(\d+)\.json$")
    vs = []
    for directory in (STATE_DIRECTORY, "memory"):
        for p in (workspace / directory).glob("v*.json"):
            m = _STRICT.match(p.name)
            if m is not None:
                vs.append(int(m.group(1)))
    return max(vs) if vs else -1


def read_memory(workspace: Path, n: int) -> Optional[dict]:
    """Read controller facts, falling back to pre-switch memory-only histories."""
    path = version_record_path(workspace, n)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def speedup_vs_reference(
    workspace: Path,
    candidate_latency_us: object,
    reported_speedup: object = None,
) -> Optional[float]:
    """Return a real reported speedup or derive it from canonical V0 latency."""

    def positive(value: object) -> Optional[float]:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        return number if number > 0.0 and math.isfinite(number) else None

    reported = positive(reported_speedup)
    if reported is not None:
        return reported
    candidate = positive(candidate_latency_us)
    baseline = read_memory(workspace, 0)
    performance = (
        baseline.get("performance")
        if isinstance(baseline, dict)
        and isinstance(baseline.get("performance"), dict)
        else {}
    )
    reference = positive(
        performance.get("latency_us_geomean", performance.get("latency_us"))
    )
    if candidate is None or reference is None:
        return None
    return reference / candidate


# ── git is the SINGLE source of truth for a "committed win" ───────────────────
# A managed native Kernel is a source tree whose adapter.py entry point is required and editable. Win/stall
# facts therefore compare canonical source snapshots. SOL retains its separate
# raw kernel.py execution contract through the same API.


def git_kernel_blob(workspace: Path) -> str:
    """Committed evaluator-entry blob for native trees or SOL kernels."""
    from aka.light.workspace.sources import entrypoint, policy_at
    return git_path_blob(workspace, "HEAD", entrypoint(policy_at(workspace, committed=True)))


def v0_baseline_commit(workspace: Path) -> str:
    """Commit that introduced this Campaign's evaluator entry point."""
    from aka.light.workspace.sources import ADAPTER, policy_at
    policy = policy_at(workspace, committed=True)
    path = ADAPTER if policy is not None else "kernel.py"
    result = subprocess.run(
        ["git", "rev-list", "--reverse", "HEAD", "--", path],
        cwd=str(workspace), capture_output=True, text=True,
    )
    if result.returncode != 0:
        return ""
    commits = result.stdout.split()
    return commits[0] if commits else ""


def resolve_framework_baseline_commit(workspace: Path) -> tuple[str, int]:
    """Verify a framework pin against its recorded raw or tree entry point."""
    marker = read_framework_baseline(workspace)
    if marker is None:
        return "", 0
    commit = _normalize_commit_hash(marker.get("commit"))
    if not commit:
        raise RuntimeError("framework_baseline.json has no usable commit hash")
    exists = subprocess.run(
        ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=str(workspace),
        capture_output=True, text=True,
    )
    ancestry = subprocess.run(
        ["git", "merge-base", "--is-ancestor", commit, "HEAD"], cwd=str(workspace),
        capture_output=True, text=True,
    )
    if exists.returncode != 0 or ancestry.returncode != 0:
        raise RuntimeError(f"framework_baseline.json points at unusable commit {commit[:12]}")
    source_entry = marker.get("source_entry", "kernel.py")
    source_blob = marker.get("source_blob", marker.get("kernel_blob"))
    if not isinstance(source_entry, str) or not source_entry or not isinstance(source_blob, str):
        raise RuntimeError("framework_baseline.json has no usable source identity")
    blob = subprocess.run(
        ["git", "rev-parse", f"{commit}:{source_entry}"], cwd=str(workspace),
        capture_output=True, text=True,
    )
    if blob.returncode != 0 or blob.stdout.strip() != source_blob:
        raise RuntimeError(
            f"framework_baseline.json source blob does not match commit {commit[:12]}"
        )
    source_digest = marker.get("source_digest")
    if source_digest is not None:
        try:
            from aka.light.workspace.sources import from_git
            actual_digest = hashlib.sha256(from_git(workspace, commit)).hexdigest()
        except (OSError, RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise RuntimeError(
                f"framework_baseline.json cannot read source at {commit[:12]}"
            ) from exc
        if source_digest != actual_digest:
            raise RuntimeError(
                f"framework_baseline.json source digest does not match commit {commit[:12]}"
            )
    version_text = str(marker.get("version") or "")
    match = re.fullmatch(r"v(\d+)", version_text)
    if match is None:
        raise RuntimeError(f"framework_baseline.json has an unusable version {version_text!r}")
    return commit, int(match.group(1))


def head_kernel_is_initial_baseline(workspace: Path) -> bool:
    """Whether committed HEAD still uses the repository's original V0 kernel.

    Production V0 is intentionally allowed to be a PyTorch reference wrapper.
    Later failed-policy/dead-end records may advance ``memory/vN.json`` and Git
    metadata without changing Kernel source. Resume must recognize that state
    by whole-tree provenance, not by assuming ``latest_version > 0`` means an
    optimized kernel was accepted.

    Once the framework-baseline stage lands v1 this returns False, because the
    accepted framework kernel is a real kernel change.  The framework baseline
    is tracked by ``framework_baseline.json``, not by this predicate.
    """
    baseline_commit = v0_baseline_commit(workspace)
    if not baseline_commit:
        return False
    try:
        from aka.light.workspace.sources import from_git
        baseline = from_git(workspace, baseline_commit)
        current = from_git(workspace, "HEAD")
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return False
    return baseline == current


def commit_changed_source(workspace: Path, commit: str) -> bool:
    """Whether a commit changes the canonical Kernel source, not metadata."""
    parent = subprocess.run(
        ["git", "rev-parse", f"{commit}^"], cwd=str(workspace),
        capture_output=True, text=True,
    )
    if parent.returncode != 0:
        return True
    try:
        from aka.light.workspace.sources import from_git
        return from_git(workspace, commit) != from_git(workspace, parent.stdout.strip())
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        return False


def read_stall(workspace: Path) -> Optional[int]:
    """Persisted live stall counter, or None when absent (caller reconstructs)."""
    p = workspace / STALL_STATE_FILE
    if not p.exists():
        return None
    try:
        v = json.loads(p.read_text(encoding="utf-8")).get("stall")
    except (OSError, ValueError):
        return None
    return int(v) if isinstance(v, int) else None


def write_stall(workspace: Path, stall: int) -> None:
    """Persist the live stall counter so a restart resumes the exact value (survives git reset —
    the file is gitignored). This is the single source of truth for the stall->convert cooldown."""
    try:
        (workspace / STALL_STATE_FILE).write_text(
            json.dumps({"stall": int(stall)}, indent=2), encoding="utf-8")
    except OSError:
        pass


def reconstruct_stall(workspace: Path) -> int:
    """Best-effort rebuild of the live stall counter from git when no persisted state exists yet
    (e.g. a workspace from before state was tracked). Counts trailing commits from HEAD that did
    NOT change Kernel source; a source-changing win stops the count. read_stall() is authoritative —
    this only bootstraps it, so it does not attempt to replay convert-issued resets."""
    try:
        r = subprocess.run(["git", "rev-list", "HEAD"], cwd=str(workspace),
                           capture_output=True, text=True)
    except OSError:
        return 0
    if r.returncode != 0:
        return 0
    trailing = 0
    for h in r.stdout.split():
        if commit_changed_source(workspace, h):   # a win -> stop counting
            break
        trailing += 1
    return trailing


def peak_util(mem: Optional[dict]) -> float:
    """Max of tflops / bandwidth peak utilization (%), 0 if unknown."""
    if not mem:
        return 0.0
    perf = mem.get("performance") or {}
    vals = [perf.get("tflops_peak_utilization_pct"), perf.get("bandwidth_peak_utilization_pct")]
    return max([float(v) for v in vals if isinstance(v, (int, float))] or [0.0])


def best_validated_latency_us(workspace: Path, *, from_version: Optional[int] = None) -> Optional[float]:
    """Best correctness-passing measured latency in a workspace.

    Versions before a pinned framework baseline are excluded by default: production cannot ship
    the PyTorch V0 wrapper, so treating its (typically much faster, library-backed) latency as the
    incumbent would reject every candidate a from-scratch DSL kernel can produce.
    """
    if from_version is None:
        _pinned_commit, pinned_version = resolve_framework_baseline_commit(workspace)
        from_version = pinned_version
    best: Optional[float] = None
    for version in range(from_version, latest_version(workspace) + 1):
        memory = read_memory(workspace, version)
        if not memory:
            continue
        gate = (memory.get("quality_gate") or {}).get("result")
        correctness = (memory.get("correctness") or {}).get("status")
        if gate != "PASS" and correctness != "PASS":
            continue
        latency = (memory.get("performance") or {}).get("latency_us")
        if isinstance(latency, (int, float)):
            best = float(latency) if best is None else min(best, float(latency))
    return best
