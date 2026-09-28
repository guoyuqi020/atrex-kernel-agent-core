"""Keep Runtime CLI calls foreground without changing other Claude background tasks."""

from __future__ import annotations

import json
import os
import shlex
import sys
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

_TOOL_PATH = "agent/optimizer/src/runtime_tools.py"
_TIMEOUT_ENV = "ATREX_RUNTIME_TOOL_BASH_TIMEOUT_MS"


def hook_output(payload: object, timeout_ms: int) -> dict[str, object] | None:
    """Modify only Bash calls that invoke the Runtime CLI."""
    if not isinstance(payload, dict) or payload.get("tool_name") != "Bash":
        return None
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or _TOOL_PATH not in command:
        return None
    updated_input = dict(tool_input)
    updated_input["timeout"] = timeout_ms
    updated_input["run_in_background"] = False
    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "allow",
            "updatedInput": updated_input,
        }
    }


def _read_settings(raw: str, workspace: Path) -> dict[str, object]:
    if not raw:
        return {}
    if raw.lstrip().startswith("{"):
        settings = json.loads(raw)
    else:
        path = Path(raw).expanduser()
        settings = json.loads((path if path.is_absolute() else workspace / path).read_text())
    if not isinstance(settings, dict):
        raise ValueError("Claude session settings must be a JSON object")
    return settings


@contextmanager
def scoped_settings(command: list[str], workspace: Path) -> Iterator[list[str]]:
    """Add our hook to the supplied Claude settings in a private temporary file."""
    settings_index = command.index("--settings") if "--settings" in command else None
    raw = command[settings_index + 1] if settings_index is not None else ""
    settings = _read_settings(raw, workspace)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError("Claude session hooks must be an object")
    pre_tool_use = hooks.setdefault("PreToolUse", [])
    if not isinstance(pre_tool_use, list):
        raise ValueError("Claude PreToolUse hooks must be an array")
    hook_script = Path(__file__).resolve()
    pre_tool_use.append(
        {
            "matcher": "Bash",
            "hooks": [
                {
                    "type": "command",
                    "command": f"{shlex.quote(sys.executable)} {shlex.quote(str(hook_script))}",
                }
            ],
        }
    )
    with tempfile.TemporaryDirectory(prefix="atrex-claude-settings-") as directory:
        settings_path = Path(directory) / "settings.json"
        settings_path.write_text(json.dumps(settings, ensure_ascii=False))
        settings_path.chmod(0o600)
        scoped_command = command.copy()
        if settings_index is None:
            scoped_command[-1:-1] = ["--settings", str(settings_path)]
        else:
            scoped_command[settings_index + 1] = str(settings_path)
        yield scoped_command


if __name__ == "__main__":
    timeout = int(os.environ[_TIMEOUT_ENV])
    result = hook_output(json.load(sys.stdin), timeout)
    if result is not None:
        json.dump(result, sys.stdout)
