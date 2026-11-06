"""Controlled local Git for one Agent trajectory.

The repository is an engineering aid and its full clean HEAD commit is the
Agent-visible Kernel identity. Promotion still requires sealed Measurement Records
whose stored source bytes match that commit exactly.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

IGNORED = (
    "scratch/", "tools/", "skills/", "memory/", "input/", "eval/", "profiles/",
    "plans/", "directions/", "experiments/", "records/", "reference/",
    "reference-projects/", "references/", "gpu-wiki/", "atrex-bench/", "input/evidence/",
    ".atrex_*/", ".orchestrator_mode.json", ".orchestrator_state.json",
    ".claude/", ".qoder/", ".agents/",
    "README.md", "CLAUDE.md", "WORKSPACE.md", "PROGRESS.md", "frontier.json", "agent_problem.json",
    "reference.py", "input.py", "shapes.json", "metadata.json", "roofline.json", "valid.py",
    "definition.json", "workload.jsonl", "solution.json", "config.json", "test_kernel.py",
    "profile_driver.py", "*.jsonl",
)

TRACKED_IGNORED = (
    "__pycache__/",
    "*.pyc",
    "*.ncu-rep",
    "profiles/*/att/*.att",
    "profiles/*/att/*.out",
    "profiles/*/att/*.pftrace",
    "profiles/*/att/*.otf2",
    "/scratch",
    "/tools",
    "/skills",
    "/reference",
    "/reference-projects",
    "/references",
    "/gpu-wiki",
    "/.claude",
    "/.qoder",
    "/.agents",
    "/.orchestrator_mode.json",
    "/.orchestrator_state.json",
    "/.atrex_long_horizon/",
    "/verification_artifacts/.atrex_long_horizon_verify/",
    "/memory/live.json",
    "/atrex-bench",
)


def gitignore_contents() -> str:
    """Return the stable tracked ignore policy installed at workspace creation."""
    return (
        "# Runtime-managed Agent repository exclusions\n"
        + "\n".join(TRACKED_IGNORED)
        + "\n"
    )


def initialize_gitignore(workspace: Path) -> None:
    """Install the tracked ignore policy before the initial Kernel commit."""
    (workspace / ".gitignore").write_text(gitignore_contents(), encoding="utf-8")

_RESUME_REMOTE_PREFIX = "refs/remotes/atrex-resume/heads/"
_RESUME_BRANCH_PREFIX = "refs/atrex/resume-branch/"
GIT_COMMIT_ID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


class DirtyKernelWorktree(ValueError):
    """The Agent repository has no exact committed identity for its current Kernel."""

    def __init__(self, paths: list[str], message: str = "Kernel worktree is not clean"):
        super().__init__(message)
        self.paths = tuple(paths)


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

    def _ensure_private_excludes(self) -> None:
        """Upgrade Runtime exclusions without dirtying the tracked repository."""
        result = _run(
            self.workspace,
            "rev-parse",
            "--git-path",
            "info/exclude",
        )
        path = Path(result.stdout.strip())
        if not path.is_absolute():
            path = self.workspace / path
        path.parent.mkdir(parents=True, exist_ok=True)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        present = set(existing.splitlines())
        missing = [entry for entry in IGNORED if entry not in present]
        if not missing:
            return
        suffix = "" if not existing or existing.endswith("\n") else "\n"
        suffix += "\n# Atrex Runtime private workspace exclusions\n"
        suffix += "\n".join(missing) + "\n"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(suffix)

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

    def _fetch_bundle(self) -> None:
        """Import recovery and peer refs without replacing live local branches."""
        if not self.bundle.is_file():
            return
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
        initialized = (self.workspace / ".git").is_dir()
        if not initialized:
            _run(self.workspace, "init", "-b", "work")
            self._configure()
            initialize_gitignore(self.workspace)
            self._fetch_bundle()
            if self.bundle.is_file():
                self._restore_branches()
        else:
            self._configure()
            # Epoch sharing updates the durable bundle between Sessions. A
            # stable repository must import those refs in place instead of
            # waiting for a future workspace reconstruction.
            self._fetch_bundle()
        # The tracked .gitignore is created before the initial Kernel commit.
        # Runtime upgrades belong in Git's private exclude file so resuming an
        # older trajectory never mutates Agent-visible source history.
        self._ensure_private_excludes()
        self.checkpoint("Runtime: materialize trusted trajectory source")
        head = self.head()
        if head:
            _run(self.workspace, "tag", "-f", "atrex/episode-start", head)

    def install_runtime_ref(
        self,
        ref: str,
        source: bytes,
        *,
        message: str,
        commit_id: str | None = None,
    ) -> str:
        """Create one source-only commit without changing the Agent checkout."""
        from aka.light.workspace.sources import Artifact

        if not ref.startswith("refs/") or ".." in ref or any(char.isspace() for char in ref):
            raise ValueError("Runtime Git ref is invalid")
        if commit_id is not None:
            if GIT_COMMIT_ID.fullmatch(commit_id) is None:
                raise ValueError("Runtime Kernel identity is not a Git commit ID")
            present = _run(
                self.workspace, "cat-file", "-e", f"{commit_id}^{{commit}}", check=False
            )
            if present.returncode != 0:
                raise ValueError(
                    "Runtime cannot expose a private source snapshot as a different Git commit"
                )
            from aka.light.workspace.sources import from_git

            if from_git(self.workspace, commit_id) != source:
                raise ValueError("Runtime Kernel commit does not match its saved source")
            _run(self.workspace, "update-ref", ref, commit_id)
            return commit_id
        existing = _run(
            self.workspace,
            "rev-parse",
            "--verify",
            f"{ref}^{{commit}}",
            check=False,
        )
        if existing.returncode == 0:
            existing_commit = existing.stdout.strip()
            from aka.light.workspace.sources import from_git

            if from_git(self.workspace, existing_commit) == source:
                return existing_commit
        files = Artifact(source).materialized_files
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

    def commit_reachable(self, commit_id: str) -> bool:
        """Return whether a complete commit is reachable from Agent-visible Git state."""
        if GIT_COMMIT_ID.fullmatch(commit_id) is None:
            return False
        ancestor = _run(
            self.workspace,
            "merge-base",
            "--is-ancestor",
            commit_id,
            "HEAD",
            check=False,
        )
        if ancestor.returncode == 0:
            return True
        refs = _run(
            self.workspace,
            "for-each-ref",
            "--format=%(refname)",
            "--contains",
            commit_id,
            "refs/heads",
            "refs/tags",
            "refs/atrex",
            check=False,
        )
        return refs.returncode == 0 and bool(refs.stdout.strip())

    def head(self) -> str | None:
        result = _run(self.workspace, "rev-parse", "HEAD", check=False)
        value = result.stdout.strip()
        return value if result.returncode == 0 and value else None

    def kernel_commit(self, expected_source: bytes | None = None) -> str:
        """Return HEAD only when it exactly identifies the visible Kernel source.

        Runtime-managed/public files are ignored by the repository.  Any
        remaining status entry is therefore an Agent-owned change that must be
        committed before a Kernel-bound request can acquire a stable identity.
        """
        status = _run(
            self.workspace,
            "status",
            "--porcelain=v1",
            "--untracked-files=all",
            check=False,
        )
        if status.returncode != 0:
            raise DirtyKernelWorktree([], status.stderr.strip() or "Git status failed")
        changes = [line[3:] if len(line) > 3 else line for line in status.stdout.splitlines()]
        if changes:
            raise DirtyKernelWorktree(changes)
        head = self.head()
        if head is None or GIT_COMMIT_ID.fullmatch(head) is None:
            raise DirtyKernelWorktree([], "Kernel worktree has no valid HEAD commit")
        from aka.light.workspace.sources import from_git, snapshot

        try:
            committed = from_git(self.workspace, head)
            visible = snapshot(self.workspace)
        except (OSError, UnicodeError, ValueError) as error:
            raise DirtyKernelWorktree(
                [], "HEAD or the visible worktree is not a complete Kernel source tree"
            ) from error
        if committed != visible:
            raise DirtyKernelWorktree(
                [], "HEAD does not contain the exact visible Kernel source tree"
            )
        if expected_source is not None and committed != expected_source:
            raise DirtyKernelWorktree(
                [], "HEAD does not contain the exact Kernel source submitted for evaluation"
            )
        return head

    def seal_into(
        self,
        supervisor_workspace: Path,
        commit_id: str,
        expected_source: bytes,
    ) -> str:
        """Import the exact Agent commit into the Supervisor's protected refs.

        The Agent HEAD is the Kernel identity.  The Supervisor therefore imports
        that object instead of manufacturing a second commit for the same source.
        Only the canonical Kernel source and Runtime-owned ``.gitignore`` may be
        tracked by the imported commit; evidence and scratch state stay outside
        the trusted Kernel identity.
        """
        if GIT_COMMIT_ID.fullmatch(commit_id) is None:
            raise ValueError("Agent Kernel identity is not a Git commit ID")
        if self.kernel_commit(expected_source) != commit_id:
            raise DirtyKernelWorktree(
                [], "Agent HEAD changed before the Kernel commit could be sealed"
            )

        from aka.light.workspace.sources import commit_paths, from_git, install

        allowed = set(commit_paths(self.workspace, normalize=False)) | {".gitignore"}
        listing = _run(
            self.workspace,
            "ls-tree",
            "-r",
            "-z",
            commit_id,
        ).stdout
        tracked: set[str] = set()
        for entry in listing.split("\0"):
            if not entry:
                continue
            metadata, name = entry.split("\t", 1)
            mode, object_type, _ = metadata.split(" ", 2)
            if object_type != "blob" or mode not in {"100644", "100755"}:
                raise ValueError(
                    f"Agent Kernel commit contains a link or special object: {name}"
                )
            tracked.add(name)
        unexpected = sorted(tracked - allowed)
        if unexpected:
            raise ValueError(
                "Agent Kernel commit contains non-Kernel tracked paths: "
                + ", ".join(unexpected[:20])
            )

        supervisor_workspace = supervisor_workspace.resolve()
        protected_ref = f"refs/atrex/accepted/{commit_id}"
        existing = subprocess.run(
            ["git", "rev-parse", "--verify", f"{protected_ref}^{{commit}}"],
            cwd=supervisor_workspace,
            env={
                **os.environ,
                "GIT_CONFIG_NOSYSTEM": "1",
                "GIT_CONFIG_GLOBAL": "/dev/null",
                "GIT_TERMINAL_PROMPT": "0",
            },
            text=True,
            capture_output=True,
            check=False,
        )
        created = existing.returncode != 0
        if not created and existing.stdout.strip() != commit_id:
            raise RuntimeError("Protected Agent Kernel ref resolves to a different commit")
        if created:
            export_ref = f"refs/atrex/export/{commit_id}"
            _run(self.workspace, "update-ref", export_ref, commit_id)
            try:
                result = subprocess.run(
                    [
                        "git",
                        "-c",
                        "protocol.file.allow=always",
                        "-c",
                        "core.hooksPath=/dev/null",
                        "fetch",
                        "--no-tags",
                        "--no-write-fetch-head",
                        str(self.workspace),
                        f"{export_ref}:{protected_ref}",
                    ],
                    cwd=supervisor_workspace,
                    env={
                        **os.environ,
                        "GIT_CONFIG_NOSYSTEM": "1",
                        "GIT_CONFIG_GLOBAL": "/dev/null",
                        "GIT_TERMINAL_PROMPT": "0",
                        "GIT_ASKPASS": "/usr/bin/false",
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
            finally:
                _run(self.workspace, "update-ref", "-d", export_ref, check=False)
            if result.returncode != 0:
                raise RuntimeError(
                    "Supervisor could not import the Agent Kernel commit: "
                    + (result.stderr.strip() or "git fetch failed")
                )

        try:
            resolved = subprocess.run(
                ["git", "rev-parse", "--verify", f"{protected_ref}^{{commit}}"],
                cwd=supervisor_workspace,
                text=True,
                capture_output=True,
                check=True,
            ).stdout.strip()
            if resolved != commit_id:
                raise RuntimeError("Imported Agent Kernel ref changed identity")
            if from_git(supervisor_workspace, commit_id) != expected_source:
                raise RuntimeError("Imported Agent Kernel commit does not match measured source")
            install(supervisor_workspace, expected_source)
        except Exception:
            if created:
                subprocess.run(
                    ["git", "update-ref", "-d", protected_ref],
                    cwd=supervisor_workspace,
                    check=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            raise
        return commit_id

    def checkpoint(self, message: str) -> str | None:
        from aka.light.workspace.sources import commit_paths

        paths = commit_paths(self.workspace, normalize=self.head() is not None)
        paths.append(".gitignore")
        _run(self.workspace, "add", "--", *paths)
        staged = _run(self.workspace, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 1 or self.head() is None:
            _run(self.workspace, "commit", "--no-gpg-sign", "-m", message)
        elif staged.returncode != 0:
            raise RuntimeError(f"Agent Git could not inspect staged source: {staged.stderr.strip()}")
        return self.head()

    def capture(self) -> dict[str, Any]:
        from aka.light.workspace.sources import commit_paths

        head = self.head()
        paths = commit_paths(self.workspace, normalize=False)
        status = _run(
            self.workspace,
            "status", "--porcelain=v1", "--untracked-files=no", "--",
            *paths,
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
