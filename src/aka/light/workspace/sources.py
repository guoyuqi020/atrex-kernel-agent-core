"""Whole-Kernel identities and task-owned source boundaries.

Every newly initialized native optimization Kernel becomes a canonical UTF-8
source tree before its first Episode. ``kernel.py`` is the evaluator entry
point and is ordinary editable Kernel source. An Agent may keep the whole
implementation there or split helpers and implementations below
``implementation/``. Single-file and multi-file Kernels use this same layout.

The layout itself is the contract. There is no per-workspace manifest: the
controller recognizes required editable ``kernel.py`` plus optional sources
below ``implementation/``.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import tarfile
import time

from aka.light.agent.session_tail import read_regular_bytes
from aka.light.workspace.files import publish

ENTRYPOINT = "kernel.py"
IMPLEMENTATION = "implementation"
TREE_MARKER = f"{IMPLEMENTATION}/__init__.py"
MAGIC = b"ATREX-SOURCE-TREE-v1\n"
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
MAX_SNAPSHOT = 6 * MAX_TOTAL
CACHES = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
PROTECTED = {"atrex_bench", "torch", "cutlass", "cuda", "triton", "site", "sitecustomize",
             "usercustomize", "importlib", "packaging", "json", "subprocess", "os", "sys"}
# Controller/public inputs and runtime state are not Candidate source files.
WORKSPACE_ONLY = {"tools", "skills", "scratch", "memory", "input", "profiles", "eval", "plans",
    "reference", "reference-projects", "gpu-wiki", "atrex-bench", "verification_artifacts",
    "README.md", "CLAUDE.md", "WORKSPACE.md", "agent_problem.json", "public_shapes.json", "reference.py", "input.py",
    "shapes.json", "metadata.json", "roofline.json", "valid.py", "definition.json", "workload.jsonl",
    "solution.json", "config.json", "test_kernel.py", "profile_driver.py", "evaluations.jsonl",
    "numerical_probe.py", "probe-request.json", "policy_review_request.json",
    ".gitignore", ".claude", ".codex", ".qoder", ".agents", ".pi", ".orchestrator_mode.json",
    ".gpu_wiki_profile", ".atrex_long_horizon", ".atrex_versions", ".atrex_plugins",
    ".atrex_stall_state.json", ".orchestrator_state.json", "framework_baseline.json",
    "baseline_report.md", "trace-retention-manifest.json", ".atrex_environment", ".qodersec", ".venv", "node_modules"}


def relative(value: str, *, dot=False) -> str:
    if dot and value == ".":
        return value
    if (not isinstance(value, str) or not value or value.startswith(("/", "-"))
            or any(c in value for c in ("\\", "\x00", "\n", "\r"))
            or any(part in {"", ".", ".."} for part in value.split("/"))):
        raise ValueError(f"Unsafe source path: {value!r}")
    return value


def validate_import_options(options):
    if (not isinstance(options, dict)
            or set(options) not in ({"manifest", "repository"}, {"manifest", "directory"})
            or any(not isinstance(value, str) or not value for value in options.values())):
        raise ValueError("source_tree requires manifest and exactly one of repository or directory")
    return options


def ignored(name):
    return any(p in CACHES for p in Path(name).parts) or Path(name).suffix in {".pyc", ".pyo"}


def source_name(name):
    return not ignored(name) and (
        name == ENTRYPOINT or name.startswith(IMPLEMENTATION + "/")
    )


def source_directory(name):
    return not ignored(name) and (
        name == IMPLEMENTATION or name.startswith(IMPLEMENTATION + "/")
    )


def editable(policy, name):
    return name == ENTRYPOINT or (
        name.startswith(IMPLEMENTATION + "/") and source_name(name)
    )


def _implicit_policy(files, *, revision=None):
    """Build the canonical in-memory contract from the fixed layout."""
    entry = files.get(ENTRYPOINT)
    if not isinstance(entry, bytes):
        raise ValueError(f"Kernel source tree requires fixed root {ENTRYPOINT}")
    if not isinstance(files.get(TREE_MARKER), bytes):
        raise ValueError(f"Kernel source tree requires package marker {TREE_MARKER}")
    for name, data in sorted(files.items()):
        relative(name)
        if not source_name(name) or not isinstance(data, bytes):
            raise ValueError(f"Kernel source must live in {ENTRYPOINT} or {IMPLEMENTATION}/: {name}")
    return {
        "schema_version": 6,
        # These fields remain inside the opaque Kernel artifact format, but are
        # fixed by the layout rather than published as workspace configuration.
        "package_root": ".",
        "editable_paths": [ENTRYPOINT, IMPLEMENTATION],
        "runtime_requirements": [],
    }


def default_tree(files):
    """Turn an ordinary one-file Kernel into the canonical editable source tree."""
    if not isinstance(files, dict) or set(files) != {ENTRYPOINT}:
        raise ValueError("Implicit source-tree initialization requires exactly kernel.py")
    files = {ENTRYPOINT: files[ENTRYPOINT], TREE_MARKER: b""}
    policy = _implicit_policy(files)
    validate_files(files, policy)
    return policy, files


def entrypoint(policy):
    """Return the evaluator-facing module for every Kernel source tree."""
    return ENTRYPOINT


def implementation_entry(policy):
    """Return the initial single-file implementation edited by framework bring-up."""
    return ENTRYPOINT


def requires_archive(policy, files):
    """Whether wire execution needs a source archive instead of a raw inline entry."""
    return policy is not None


def validate_policy(policy):
    common = {
        "schema_version", "package_root", "editable_paths", "runtime_requirements",
    }
    if (not isinstance(policy, dict) or policy.get("schema_version") != 6
            or set(policy) != common):
        raise ValueError("Invalid source contract")
    if policy["package_root"] != ".":
        raise ValueError("Kernel package_root is fixed at '.'")
    if policy["editable_paths"] != [ENTRYPOINT, IMPLEMENTATION]:
        raise ValueError("Kernel editable paths are fixed at kernel.py and implementation/")
    requirements = policy["runtime_requirements"]
    if requirements != []:
        raise ValueError("Kernel runtime requirements belong to the task, not the source tree")
    return policy


def _committed_file(workspace: Path, path: str, revision="HEAD"):
    """Return one committed file, or None when revision/path is absent."""
    if not workspace.is_dir():
        return None
    result = subprocess.run(
        ["git", "show", f"{revision}:{path}"], cwd=workspace, capture_output=True
    )
    return result.stdout if result.returncode == 0 else None


def policy_at(workspace: Path, *, committed=False):
    workspace = Path(workspace)
    committed_marker = _committed_file(workspace, TREE_MARKER)
    if committed:
        if committed_marker is None:
            return None
        entry = _committed_file(workspace, ENTRYPOINT)
        return validate_policy(_implicit_policy({ENTRYPOINT: entry, TREE_MARKER: committed_marker}))
    marker = workspace / TREE_MARKER
    if not marker.is_file() and committed_marker is None:
        return None
    if not (workspace / ENTRYPOINT).is_file():
        # A managed Agent may accidentally remove the required entry point.
        # Preserve tree-mode detection so validation reports that error instead
        # of silently reinterpreting the workspace as a raw SOL Kernel.
        entry = _committed_file(workspace, ENTRYPOINT)
        if entry is None:
            return None
    else:
        entry = read_regular_bytes(workspace / ENTRYPOINT, limit=MAX_FILE + 1)
    marker_bytes = (
        read_regular_bytes(marker, limit=MAX_FILE + 1)
        if marker.is_file()
        else committed_marker
    )
    return validate_policy(_implicit_policy({ENTRYPOINT: entry, TREE_MARKER: marker_bytes}))


def validate_files(files, policy):
    validate_policy(policy)
    entry = entrypoint(policy)
    if entry not in files:
        raise ValueError(f"Kernel source tree requires {entry} as its fixed entry point")
    if TREE_MARKER not in files:
        raise ValueError(f"Kernel source tree requires {TREE_MARKER}")
    if len(files) > 4096 or sum(len(data) for data in files.values()) > MAX_TOTAL:
        raise ValueError("Source tree exceeds 4096 files / 64 MiB")
    for name, data in files.items():
        relative(name)
        if not source_name(name):
            raise ValueError(f"Source path collides with workspace infrastructure: {name}")
        if len(data) > MAX_FILE:
            raise ValueError(f"Source file exceeds 16 MiB: {name}")
        data.decode("utf-8")
        package = Path(name)
        if package.parts[0].removesuffix(".py") in PROTECTED:
            raise ValueError(f"Source shadows a protected import: {name}")
        if not editable(policy, name):
            raise ValueError(f"Source file is outside the editable Kernel paths: {name}")
        if Path(name).suffix in {".so", ".a", ".o", ".cubin", ".whl", ".pth"}:
            raise ValueError(f"Prebuilt/loading artifact is not permitted: {name}")
    return files


def pack(files, policy):
    validate_files(files, policy)
    return MAGIC + json.dumps({"contract": policy, "files": {k: v.decode("utf-8") for k, v in sorted(files.items())}},
                              sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def from_inputs(inputs, *, prefix=""):
    """Use the already frozen measurement bytes, never reread a mutable draft."""
    if prefix + TREE_MARKER not in inputs:
        return inputs[prefix + ENTRYPOINT]
    files = {name[len(prefix):]: data for name, data in inputs.items()
             if name.startswith(prefix) and source_name(name[len(prefix):])}
    return pack(files, _implicit_policy(files))


def unpack(source):
    if not source.startswith(MAGIC):
        return None, {"kernel.py": source}
    if len(source) > MAX_SNAPSHOT:
        raise ValueError("Source snapshot exceeds the bounded limit")
    value = json.loads(source[len(MAGIC):])
    files = {name: data.encode() for name, data in value["files"].items()}
    validate_files(files, value["contract"])
    if pack(files, value["contract"]) != source:
        raise ValueError("Source snapshot is not canonical")
    return value["contract"], files


def collect(workspace, policy, *, validate=True):
    files = {}
    for parent, dirs, names in os.walk(workspace, followlinks=False):
        rel = Path(parent).relative_to(workspace)
        for name in (*dirs, *names):
            key = (rel / name).as_posix()
            if (key.split("/")[0] not in WORKSPACE_ONLY | {".git"}
                    and (Path(parent) / name).is_symlink()):
                raise ValueError("Source tree cannot contain symlinks")
        dirs[:] = [n for n in dirs if source_directory((rel / n).as_posix())]
        for name in dirs:
            if (Path(parent) / name).is_symlink():
                raise ValueError("Source tree cannot contain symlink directories")
        for name in names:
            relative_name = (rel / name).as_posix()
            if not source_name(relative_name):
                continue
            files[relative_name] = read_regular_bytes(Path(parent) / name, limit=MAX_FILE + 1)
            if len(files) > 4096 or sum(map(len, files.values())) > MAX_TOTAL:
                raise ValueError("Source tree exceeds 4096 files / 64 MiB")
    return validate_files(files, policy) if validate else files


_DISCOVER = object()


def snapshot(workspace, *, policy=_DISCOVER):
    policy = policy_at(workspace) if policy is _DISCOVER else policy
    if policy is None:
        return read_regular_bytes(workspace / "kernel.py", limit=MAX_FILE + 1)
    files = collect(workspace, policy)
    return pack(files, _implicit_policy(files))


def from_git(workspace, revision):
    from aka.light.workspace.git import git_blob
    if _committed_file(workspace, TREE_MARKER, revision) is None:
        return git_blob(workspace, revision, ENTRYPOINT)
    listing = subprocess.run(["git", "ls-tree", "-r", "-z", revision], cwd=workspace, capture_output=True, check=True).stdout
    files = {}
    for entry in listing.split(b"\0"):
        if not entry:
            continue
        meta, name = entry.split(b"\t", 1)
        name = name.decode()
        if source_name(name):
            if meta.split()[0] not in {b"100644", b"100755"}:
                raise ValueError("Committed source contains a link or special object")
            files[name] = git_blob(workspace, revision, name)
    policy = _implicit_policy(files)
    return pack(files, policy)


def install(workspace, source, *, replace=True):
    workspace = Path(workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    policy, files = unpack(source)
    if policy is not None:
        # Only remove prior source files after a complete new snapshot validates.
        # Keep a recoverable private copy at the caller's transaction boundary.
        previous = policy_at(workspace)
        if replace and previous:
            # Recovery may be replacing a partially published tree. Validate
            # the new snapshot, not the completeness of the obsolete one.
            for name in collect(workspace, previous, validate=False).keys() - files.keys():
                from aka.light.agent.home import open_private_directory
                with open_private_directory((workspace / name).parent) as parent:
                    os.unlink(Path(name).name, dir_fd=parent)
    for name, data in files.items():
        publish(workspace, name, data)


def instructions(policy):
    if policy is None:
        return ("\n## Kernel source\nThis archived task uses the legacy raw kernel.py contract.\n")
    return ("\n## Kernel source tree\nAll source files form one Kernel. kernel.py is the required "
            "editable evaluator entry point. Keep a single-file implementation there, or add, edit and "
            "delete helper/source files inside implementation/ and import them from kernel.py. "
            "Keep implementation/__init__.py present so the package and tree identity remain valid. "
            "Files outside kernel.py and implementation/ are not Kernel source. "
            "Keep notes, reusable tools and temporary builds in tools/, skills/ and scratch/; "
            "they are not Kernel source. Evaluate, Profile, Check, Disassemble, sealed reports "
            "and broadcasts bind the complete source tree. kernel-read writes a directory; pass "
            "that directory to --baseline-path.\n")


def allowed_changes(paths, policy):
    return all(editable(policy, name) for name in paths)


def commit_tree_candidate(handle, source):
    """Seal measured additions/modifications/deletions with a private Git index."""
    from aka.light.workspace.git import (
        git_head,
        git_text,
        changed_paths,
        TIMELINE_PROBE_MARKERS,
        protected_violation,
        working_changes,
    )
    import tempfile
    policy, files = unpack(source)
    baseline_policy, _ = unpack(from_git(handle.path, handle.base_commit))
    if policy != baseline_policy:
        raise ValueError("Candidate source contract differs from its Episode baseline")
    if any(marker.encode() in data for data in files.values() for marker in TIMELINE_PROBE_MARKERS):
        raise ValueError("Candidate source still contains timeline profiling probes")
    head = git_head(handle.path)
    violation = protected_violation(working_changes(handle.path))
    if violation:
        raise ValueError(violation)
    if git_text(handle.path, "symbolic-ref", "--quiet", "--short", "HEAD") != handle.branch:
        raise RuntimeError("Candidate branch differs from its Episode")
    subprocess.run(["git", "merge-base", "--is-ancestor", handle.base_commit, head], cwd=handle.path, check=True)
    staged = [p for p in git_text(handle.path, "diff", "--cached", "--name-only", "--no-renames", "-z").split("\0") if p]
    if not allowed_changes(changed_paths(handle.path, handle.base_commit, head) + staged, policy):
        raise ValueError("Candidate may change only declared editable source paths")
    previous = from_git(handle.path, head)
    _, old_files = unpack(previous)
    if source != previous:
        with tempfile.TemporaryDirectory(prefix="aka-tree-index-") as temporary:
            env = dict(os.environ, GIT_INDEX_FILE=str(Path(temporary) / "index"),
                       GIT_AUTHOR_NAME="AKA Supervisor", GIT_COMMITTER_NAME="AKA Supervisor",
                       GIT_AUTHOR_EMAIL="supervisor@atrex.local", GIT_COMMITTER_EMAIL="supervisor@atrex.local")
            def run(*args, data=None):
                return subprocess.run(["git", *args], cwd=handle.path, env=env, input=data,
                                      capture_output=True, check=True).stdout.decode().strip()
            run("read-tree", head)
            for name in old_files.keys() - files.keys():
                run("update-index", "--force-remove", "--", name)
            for name, data in files.items():
                if old_files.get(name) != data:
                    blob = run("hash-object", "-w", "--stdin", data=data)
                    run("update-index", "--add", "--cacheinfo", "100644", blob, name)
            tree = run("write-tree")
            commit = run("-c", "commit.gpgSign=false", "commit-tree", tree, "-p", head,
                         "-m", f"Episode {handle.episode}: selected source tree")
            run("update-ref", "HEAD", commit, head)
            head = commit
    install(handle.path, source)
    subprocess.run(["git", "read-tree", head], cwd=handle.path, check=True)
    return head


def _git_sources(repository, revision, paths):
    if not isinstance(revision, str) or len(revision) not in {40, 64} or any(c not in "0123456789abcdef" for c in revision):
        raise ValueError("Source revision must be an exact Git commit hash")
    kind = subprocess.run(["git", "cat-file", "-t", revision], cwd=repository,
                          capture_output=True, timeout=30)
    if kind.returncode or kind.stdout.strip() != b"commit":
        raise ValueError("Source revision must identify an existing Git commit")
    # Bound disk usage and elapsed time even if git never closes stdout.
    import tempfile
    with tempfile.TemporaryFile() as archive:
        process = subprocess.Popen(["git", "archive", "--format=tar", revision, "--", *paths], cwd=repository, stdout=archive, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 120
            while process.poll() is None:
                if os.fstat(archive.fileno()).st_size > 2 * MAX_TOTAL:
                    raise ValueError("Source archive exceeds its bounded limit")
                if time.monotonic() >= deadline:
                    raise ValueError("Pinned source archive timed out")
                time.sleep(0.02)
            if process.returncode:
                raise ValueError("Cannot archive the pinned source revision")
            if os.fstat(archive.fileno()).st_size > 2 * MAX_TOTAL:
                raise ValueError("Source archive exceeds its bounded limit")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
        archive.seek(0)
        files = {}
        with tarfile.open(fileobj=archive) as tar:
            for member in tar:
                if member.isdir():
                    relative(member.name.rstrip("/"))
                    continue
                name = relative(member.name)
                if not member.isfile() or name in files or member.size > MAX_FILE:
                    raise ValueError("Source archive contains an unsafe/duplicate/oversized entry")
                data = tar.extractfile(member).read(MAX_FILE + 1)
                if not ignored(name):
                    files[name] = data
                if len(files) > 4096 or sum(map(len, files.values())) > MAX_TOTAL:
                    raise ValueError("Source archive exceeds 4096 files / 64 MiB")
    return files


def _directory_sources(directory, paths):
    """Read selected local bytes without Git, links, special files or silent omissions."""
    directory = Path(directory).expanduser().resolve(strict=True)
    if not directory.is_dir():
        raise ValueError("Source directory must be a directory")
    files, total, entries = {}, 0, 0

    def add(path):
        nonlocal total
        name = path.relative_to(directory).as_posix()
        if name in files or ignored(name):
            return
        before = path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise ValueError(f"Source entry must be a regular non-symlink file: {name}")
        if before.st_size > MAX_FILE:
            raise ValueError(f"Source file exceeds 16 MiB: {name}")
        data = read_regular_bytes(path, limit=MAX_FILE + 1)
        if len(data) > MAX_FILE:
            raise ValueError(f"Source file exceeds 16 MiB: {name}")
        after = path.lstat()
        def identity(value):
            return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns
        if identity(before) != identity(after) or len(data) != after.st_size:
            raise ValueError(f"Source file changed while taking the initial snapshot: {name}")
        total += len(data)
        files[name] = data
        if len(files) > 4096 or total > MAX_TOTAL:
            raise ValueError("Source directory exceeds 4096 files / 64 MiB")

    def failed(error):
        raise error

    for selected in paths:
        path = directory / selected
        # Validate every selected component, including directories that might
        # otherwise resolve through a link before os.walk starts.
        for parent in (path, *path.parents):
            if parent == directory:
                break
            if parent.is_symlink():
                raise ValueError(f"Source tree cannot contain symlinks: {selected}")
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            for current, dirs, names in os.walk(path, followlinks=False, onerror=failed):
                entries += len(dirs) + len(names) + 1
                if entries > 16384:
                    raise ValueError("Source directory exceeds its traversal limit")
                for name in dirs:
                    if (Path(current) / name).is_symlink():
                        raise ValueError("Source tree cannot contain symlink directories")
                dirs[:] = [name for name in dirs if not ignored((Path(current) / name).relative_to(directory).as_posix())]
                for name in names:
                    add(Path(current) / name)
        else:
            add(path)
    return files


def import_tree(manifest_path, repository=None, *, directory=None):
    if (repository is None) == (directory is None):
        raise ValueError("Choose exactly one source repository or directory")
    manifest_path = Path(manifest_path).expanduser().resolve()
    manifest = json.loads(read_regular_bytes(manifest_path, limit=MAX_FILE))
    if manifest.get("schema_version", 1) != 1 or manifest.get("runtime_support") or manifest.get("repository_search", {}).get("mode", "snapshot") != "snapshot":
        raise ValueError("Only snapshot source manifests without runtime_support are supported")
    spec = manifest["source"]
    paths = [relative(p) for p in spec["archive_paths"]]
    if not paths:
        raise ValueError("Source archive_paths cannot be empty")
    # Directory mode intentionally snapshots working bytes, including dirty and
    # untracked files. A manifest revision cannot describe these bytes.
    revision = spec.get("revision") if repository is not None else None
    imported = (_git_sources(Path(repository).expanduser().resolve(), revision, paths)
                if repository is not None else _directory_sources(directory, paths))
    if spec.get("package_root", ".") != ".":
        raise ValueError("Native Eval source trees require package_root='.'")
    if manifest.get("runtime_requirements"):
        raise ValueError("Declare runtime requirements on the task, not the Kernel source tree")
    # The repository may keep sources anywhere; the Agent workspace does not.
    # Preserve their relative layout underneath the one editable root.
    files = {
        name if name.startswith(IMPLEMENTATION + "/") else f"{IMPLEMENTATION}/{name}": data
        for name, data in imported.items()
    }
    files.setdefault(TREE_MARKER, b"")
    files[ENTRYPOINT] = read_regular_bytes(
        manifest_path.parent / relative(manifest["adapter"]), limit=MAX_FILE + 1
    )
    return pack(files, _implicit_policy(files, revision=revision))


def initial_snapshot(workspace, options):
    """Seal once before V0; failed bootstrap/restarts never reread mutable inputs."""
    import fcntl
    from aka.light.agent.home import open_private_directory
    from aka.legacy.task.persistence import durable_write_json
    from aka.light.runtime.paths import supervisor_campaign_root
    validate_import_options(options)
    configuration = {key: os.path.abspath(Path(value).expanduser()) for key, value in options.items()}
    root = supervisor_campaign_root(workspace) / "initial-source"
    path = root / "snapshot.json"
    with open_private_directory(root) as parent:
        try:
            lock = os.open("lock", os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        except FileExistsError:
            lock = os.open("lock", os.O_RDWR | os.O_NOFOLLOW, dir_fd=parent)
        try:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                raw = read_regular_bytes(path, limit=MAX_SNAPSHOT + MAX_FILE + 1)
            except FileNotFoundError:
                source = import_tree(configuration["manifest"], configuration.get("repository"),
                                     directory=configuration.get("directory"))
                value = json.loads(source[len(MAGIC):])
                durable_write_json(path, {"schema_version": 1, "configuration": configuration,
                    "snapshot_digest": hashlib.sha256(source).hexdigest(), **value})
                return source
            if len(raw) > MAX_SNAPSHOT + MAX_FILE:
                raise ValueError("Initial source snapshot exceeds its size limit")
            value = json.loads(raw)
            if value.get("schema_version") != 1 or value.get("configuration") != configuration:
                raise ValueError("Initial source configuration changed; use a new Campaign workspace")
            source = pack({name: text.encode() for name, text in value["files"].items()}, value["contract"])
            if hashlib.sha256(source).hexdigest() != value.get("snapshot_digest"):
                raise ValueError("Initial source snapshot failed integrity verification")
            return source
        finally:
            os.close(lock)
