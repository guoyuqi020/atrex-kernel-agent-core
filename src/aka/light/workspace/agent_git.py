"""Controlled local Git for one Agent trajectory.

The repository is an engineering aid. Runtime source snapshots remain the only
Kernel identity and all promotion continues to use sealed Measurement Records.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any

IGNORED = (
    "scratch/", "tools/", "skills/", "memory/", "input/", "eval/", "profiles/",
    "plans/", "reference/", "input/evidence/", ".atrex_*/", ".claude/", ".agents/",
    "README.md", "CLAUDE.md", "WORKSPACE.md", "agent_problem.json", "public_shapes.json",
    "reference.py", "input.py", "shapes.json", "metadata.json", "roofline.json", "valid.py",
    "definition.json", "workload.jsonl", "solution.json", "config.json", "test_kernel.py",
    "profile_driver.py", "*.jsonl",
)

_RESUME_REMOTE_PREFIX = "refs/remotes/atrex-resume/heads/"
_RESUME_BRANCH_PREFIX = "refs/atrex/resume-branch/"


def _run(
    workspace: Path, *args: str, check: bool = True, extra_env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    environment = dict(os.environ)
    environment.update(
        GIT_CONFIG_NOSYSTEM="1",
        GIT_TERMINAL_PROMPT="0",
        GIT_ASKPASS="/usr/bin/false",
    )
    environment.update(extra_env or {})
    return subprocess.run(
        ["git", *args], cwd=workspace, env=environment,
        text=True, capture_output=True, check=check,
    )


class AgentGit:
    def __init__(self, workspace: Path, bundle: Path):
        self.workspace = workspace
        self.bundle = bundle

    def _configure(self) -> None:
        settings = {
            "user.name": "Atrex Agent",
            "user.email": "agent@localhost",
            "core.hooksPath": ".git/disabled-hooks",
            "credential.helper": "",
            "commit.gpgSign": "false",
            "tag.gpgSign": "false",
            "protocol.file.allow": "never",
            "fetch.recurseSubmodules": "false",
            "submodule.recurse": "false",
            "advice.detachedHead": "false",
        }
        for key, value in settings.items():
            _run(self.workspace, "config", "--local", key, value)
        hooks = self.workspace / ".git" / "disabled-hooks"
        hooks.mkdir(parents=True, exist_ok=True)

    def _restore_branches(self) -> None:
        """Restore every Agent branch and the previously checked-out branch."""
        rows = _run(
            self.workspace,
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            _RESUME_REMOTE_PREFIX,
        ).stdout.splitlines()
        branches: list[str] = []
        for row in rows:
            remote_ref, object_name = row.split(" ", 1)
            branch = remote_ref.removeprefix(_RESUME_REMOTE_PREFIX)
            if not branch:
                continue
            local_ref = "refs/heads/" + branch
            _run(self.workspace, "check-ref-format", local_ref)
            _run(self.workspace, "update-ref", local_ref, object_name)
            branches.append(branch)

        active = ""
        markers = _run(
            self.workspace,
            "for-each-ref",
            "--format=%(refname)",
            _RESUME_BRANCH_PREFIX,
        ).stdout.splitlines()
        if len(markers) == 1:
            active = markers[0].removeprefix(_RESUME_BRANCH_PREFIX)
        if active not in branches:
            active = "work" if "work" in branches else (sorted(branches)[0] if branches else "")
        if active:
            _run(self.workspace, "checkout", "-f", active)

    def _record_active_branch(self) -> None:
        """Store the symbolic checkout in a bundled private ref."""
        markers = _run(
            self.workspace,
            "for-each-ref",
            "--format=%(refname)",
            _RESUME_BRANCH_PREFIX,
        ).stdout.splitlines()
        for marker in markers:
            _run(self.workspace, "update-ref", "-d", marker)
        branch = _run(
            self.workspace, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        ).stdout.strip()
        head = self.head()
        if branch and head:
            marker = _RESUME_BRANCH_PREFIX + branch
            _run(self.workspace, "check-ref-format", marker)
            _run(self.workspace, "update-ref", marker, head)

    def prepare(self) -> None:
        if not (self.workspace / ".git").is_dir():
            _run(self.workspace, "init", "-b", "work")
            self._configure()
            if self.bundle.is_file():
                _run(
                    self.workspace,
                    "-c",
                    "protocol.file.allow=always",
                    "fetch",
                    str(self.bundle),
                    "+refs/heads/*:refs/remotes/atrex-resume/heads/*",
                    "+refs/tags/*:refs/tags/*",
                    "+refs/atrex/*:refs/atrex/*",
                    "+refs/notes/*:refs/notes/*",
                )
                self._restore_branches()
            (self.workspace / ".gitignore").write_text(
                "# Runtime-managed Agent repository exclusions\n" + "\n".join(IGNORED) + "\n",
                encoding="utf-8",
            )
        else:
            self._configure()
        self.checkpoint("Runtime: materialize trusted trajectory source")
        head = self.head()
        if head:
            _run(self.workspace, "tag", "-f", "atrex/episode-start", head)

    def install_runtime_ref(self, ref: str, source: bytes, *, message: str) -> str:
        """Create one source-only commit without changing the Agent checkout."""
        from aka.light.workspace.sources import unpack

        if not ref.startswith("refs/") or ".." in ref or any(char.isspace() for char in ref):
            raise ValueError("Runtime Git ref is invalid")
        _, files = unpack(source)
        with tempfile.TemporaryDirectory(prefix="aka-agent-index-") as directory:
            index = str(Path(directory) / "index")
            environment = {"GIT_INDEX_FILE": index}
            _run(self.workspace, "read-tree", "--empty", extra_env=environment)
            for name, data in sorted(files.items()):
                blob = subprocess.run(
                    ["git", "hash-object", "-w", "--stdin"],
                    cwd=self.workspace,
                    env={**os.environ, "GIT_CONFIG_NOSYSTEM": "1"},
                    input=data,
                    capture_output=True,
                    check=True,
                ).stdout.decode().strip()
                _run(
                    self.workspace,
                    "update-index",
                    "--add",
                    "--cacheinfo",
                    f"100644,{blob},{name}",
                    extra_env=environment,
                )
            tree = _run(self.workspace, "write-tree", extra_env=environment).stdout.strip()
        commit = _run(self.workspace, "commit-tree", tree, "-m", message).stdout.strip()
        _run(self.workspace, "update-ref", ref, commit)
        return commit

    def head(self) -> str | None:
        result = _run(self.workspace, "rev-parse", "HEAD", check=False)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    def checkpoint(self, message: str) -> str | None:
        paths = ["kernel.py", "implementation", ".gitignore"]
        _run(self.workspace, "add", "--", *paths)
        staged = _run(self.workspace, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 1 or self.head() is None:
            _run(self.workspace, "commit", "--no-gpg-sign", "-m", message)
        elif staged.returncode != 0:
            raise RuntimeError(f"Agent Git could not inspect staged source: {staged.stderr.strip()}")
        return self.head()

    def capture(self) -> dict[str, Any]:
        head = self.head()
        status = _run(
            self.workspace,
            "status", "--porcelain=v1", "--untracked-files=no", "--",
            "kernel.py", "implementation",
            check=False,
        )
        clean = status.returncode == 0 and not status.stdout.strip()
        tree = None
        if clean and head:
            result = _run(self.workspace, "rev-parse", f"{head}^{{tree}}", check=False)
            if result.returncode == 0:
                tree = result.stdout.strip() or None
        return {"head": head, "clean": clean, "tree": tree}

    def persist(self) -> str | None:
        head = self.checkpoint("Runtime: preserve Episode WIP checkpoint")
        if head is None:
            return None
        self._record_active_branch()
        self.bundle.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.bundle.with_suffix(".tmp")
        temporary.unlink(missing_ok=True)
        result = _run(
            self.workspace, "bundle", "create", str(temporary), "--all", check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"Agent Git bundle failed: {result.stderr.strip()}")
        os.replace(temporary, self.bundle)
        return head

    def audit(self) -> str:
        return json.dumps(self.capture(), sort_keys=True)
