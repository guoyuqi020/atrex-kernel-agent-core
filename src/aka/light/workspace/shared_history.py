"""Filtered Epoch Git history exchange between isolated Agent repositories."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from aka.legacy.task.persistence import durable_write_json

MAX_EXPORT_BYTES = 32 * 1024 * 1024
MAX_EXPORT_COMMITS = 512


def _with_sanitized_commit_messages(data: bytes) -> bytes:
    """Strip Agent-authored commit prose while preserving the fast-export graph."""
    output: list[bytes] = []
    offset = 0
    record = b""
    commit_message_pending = False
    while offset < len(data):
        end = data.find(b"\n", offset)
        if end < 0:
            output.append(data[offset:])
            break
        line = data[offset:end + 1]
        offset = end + 1
        if line.startswith(b"commit "):
            record = b"commit"
            commit_message_pending = True
        elif line == b"blob\n":
            record = b"blob"
            commit_message_pending = False
        if line.startswith(b"data "):
            try:
                length = int(line[5:].strip())
            except ValueError as error:
                raise ValueError("Git export contains an unsupported data record") from error
            if length < 0 or offset + length > len(data):
                raise ValueError("Git export contains a truncated data record")
            payload = data[offset:offset + length]
            offset += length
            replacement = payload
            if record == b"commit" and commit_message_pending:
                replacement = b"Agent source checkpoint (message withheld by Runtime)\n"
                commit_message_pending = False
            output.append(b"data " + str(len(replacement)).encode() + b"\n")
            output.append(replacement)
            continue
        output.append(line)
    return b"".join(output)


def _run(workspace: Path, *args: str, input_bytes: bytes | None = None,
         check: bool = True) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0")
    return subprocess.run(
        ["git", *args], cwd=workspace, env=environment, input=input_bytes,
        capture_output=True, check=check,
    )


def export_epoch(
    workspace: Path,
    destination: Path,
    *,
    trajectory: int,
    epoch: int,
    audit_path: Path,
) -> dict:
    """Export only candidate-source history under a namespaced peer ref."""
    head = _run(workspace, "rev-parse", "HEAD").stdout.decode().strip()
    ref = f"refs/atrex/shared/T{trajectory}/e{epoch:04d}/head"
    _run(workspace, "update-ref", ref, head)
    process = _run(
        workspace,
        "fast-export",
        "--signed-tags=strip",
        "--tag-of-filtered-object=drop",
        ref,
        "--",
        "kernel.py",
        "implementation",
        check=False,
    )
    if process.returncode != 0:
        raise RuntimeError(process.stderr.decode(errors="replace").strip())
    data = _with_sanitized_commit_messages(process.stdout)
    commits = data.count(b"\ncommit ") + (1 if data.startswith(b"commit ") else 0)
    if len(data) > MAX_EXPORT_BYTES or commits > MAX_EXPORT_COMMITS:
        raise ValueError(
            f"Shared Git history exceeds {MAX_EXPORT_BYTES} bytes / {MAX_EXPORT_COMMITS} commits"
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_bytes(data)
    os.replace(temporary, destination)
    receipt = {
        "schema_version": 1,
        "trajectory": trajectory,
        "epoch": epoch,
        "source_ref": ref,
        "original_agent_commit": head,
        "bytes": len(data),
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
    """Import filtered streams into a trajectory bundle without exposing peer repos."""
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
            data = path.read_bytes()
            if len(data) > MAX_EXPORT_BYTES:
                raise ValueError("Shared Git export exceeds its size limit")
            result = _run(workspace, "fast-import", "--force", input_bytes=data, check=False)
            if result.returncode != 0:
                raise RuntimeError(result.stderr.decode(errors="replace").strip())
            imported.append(path.name)
            audit = path.with_suffix(".json")
            try:
                metadata = json.loads(audit.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                metadata = {}
            source_ref = metadata.get("source_ref")
            original = metadata.get("original_agent_commit")
            imported_commit = None
            if isinstance(source_ref, str):
                resolved = _run(workspace, "rev-parse", source_ref, check=False)
                if resolved.returncode == 0:
                    imported_commit = resolved.stdout.decode().strip()
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
