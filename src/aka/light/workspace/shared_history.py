"""Exact candidate-source Git history exchange between isolated Agent repositories."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from aka.legacy.task.persistence import durable_write_json

def _run(workspace: Path, *args: str, input_bytes: bytes | None = None,
         check: bool = True) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", *args], cwd=workspace, env=environment, input=input_bytes,
        capture_output=True, check=check,
    )


def _validate_source_history(workspace: Path, ref: str) -> int:
    """Ensure an exact-ID bundle contains only valid candidate-source commits."""
    from aka.light.workspace.sources import Artifact, from_git

    commits = _run(workspace, "rev-list", ref).stdout.decode().splitlines()
    for commit in commits:
        artifact = Artifact(from_git(workspace, commit))
        allowed = {*artifact.materialized_files, ".gitignore"}
        tree = set(
            _run(workspace, "ls-tree", "-r", "--name-only", commit)
            .stdout.decode()
            .splitlines()
        )
        unexpected = sorted(tree - allowed)
        if unexpected:
            raise ValueError(
                "Shared Git history contains non-Kernel paths: "
                + ", ".join(unexpected[:8])
            )
    return len(commits)


def export_epoch(
    workspace: Path,
    destination: Path,
    *,
    trajectory: int,
    epoch: int,
    audit_path: Path,
) -> dict:
    """Export exact Git objects so peer kernel_commit_id values remain valid."""
    head = _run(workspace, "rev-parse", "HEAD").stdout.decode().strip()
    ref = f"refs/atrex/shared/T{trajectory}/e{epoch:04d}/head"
    _run(workspace, "update-ref", ref, head)
    commits = _validate_source_history(workspace, ref)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.unlink(missing_ok=True)
    result = _run(workspace, "bundle", "create", str(temporary), ref, check=False)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    size = temporary.stat().st_size
    os.replace(temporary, destination)
    receipt = {
        "schema_version": 1,
        "trajectory": trajectory,
        "epoch": epoch,
        "source_ref": ref,
        "original_agent_commit": head,
        "bytes": size,
        "commit_count": commits,
    }
    durable_write_json(audit_path, receipt)
    return receipt


def import_exports(
    bundle: Path,
    exports: Iterable[Path],
    *,
    notes: Mapping[str, Mapping[str, object]] | None = None,
    audit_path: Path,
) -> dict:
    """Import exact, source-scoped peer commits without exposing peer repositories."""
    exports = tuple(exports)
    if not exports:
        return {"schema_version": 1, "imports": []}
    with tempfile.TemporaryDirectory(prefix="aka-shared-git-") as temporary:
        workspace = Path(temporary) / "repo"
        workspace.mkdir()
        _run(workspace, "init", "-b", "work")
        _run(workspace, "config", "user.name", "Atrex Runtime")
        _run(workspace, "config", "user.email", "runtime@localhost")
        if bundle.is_file():
            _run(
                workspace,
                "-c",
                "protocol.file.allow=always",
                "fetch",
                str(bundle),
                "+refs/heads/work:refs/remotes/atrex-local/work",
                "+refs/tags/*:refs/tags/*",
                "+refs/atrex/*:refs/atrex/*",
                "+refs/notes/*:refs/notes/*",
            )
            if _run(
                workspace,
                "show-ref",
                "--verify",
                "refs/remotes/atrex-local/work",
                check=False,
            ).returncode == 0:
                _run(workspace, "checkout", "-B", "work", "refs/remotes/atrex-local/work")
        imported = []
        mappings = []
        for path in exports:
            audit = path.with_suffix(".json")
            try:
                metadata = json.loads(audit.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metadata = {}
            source_ref = metadata.get("source_ref")
            original = metadata.get("original_agent_commit")
            if not isinstance(source_ref, str) or not source_ref.startswith("refs/atrex/shared/"):
                raise ValueError("Shared Git bundle has no valid namespaced source ref")
            result = _run(
                workspace,
                "-c",
                "protocol.file.allow=always",
                "fetch",
                str(path),
                f"+{source_ref}:{source_ref}",
                check=False,
            )
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode(errors="replace").strip())
            imported.append(path.name)
            resolved = _run(workspace, "rev-parse", source_ref, check=False)
            imported_commit = (
                resolved.stdout.decode().strip() if resolved.returncode == 0 else None
            )
            if imported_commit != original:
                raise ValueError(
                    "Shared Git import changed the peer commit ID; Kernel identity is invalid"
                )
            mappings.append({
                "export": path.name,
                "source_ref": source_ref,
                "original_agent_commit": original,
                "imported_commit": imported_commit,
            })
        for ref, note in (notes or {}).items():
            target = _run(workspace, "rev-parse", ref, check=False)
            if target.returncode:
                continue
            _run(
                workspace,
                "notes",
                "--ref=atrex-measurements",
                "add",
                "-f",
                "-m",
                json.dumps(note, ensure_ascii=False, sort_keys=True),
                target.stdout.decode().strip(),
            )
        bundle.parent.mkdir(parents=True, exist_ok=True)
        staged = bundle.with_suffix(".tmp")
        staged.unlink(missing_ok=True)
        _run(workspace, "bundle", "create", str(staged), "--all")
        os.replace(staged, bundle)
        refs = _run(workspace, "for-each-ref", "--format=%(refname)", "refs/atrex/shared").stdout.decode().splitlines()
    receipt = {
        "schema_version": 1,
        "imports": imported,
        "commit_mappings": mappings,
        "shared_refs": sorted(refs),
    }
    durable_write_json(audit_path, receipt)
    return receipt
