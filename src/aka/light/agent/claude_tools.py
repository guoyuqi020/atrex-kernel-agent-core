"""Invocation-local Claude hooks for policy and Supervisor HTTP clients."""
import json
import math
import os
from pathlib import Path
import shlex
import sys
import time

try:
    from .dependency_policy import (
        canonical_sandbox_entrypoint,
        dependency_command_violation,
        protected_gateway_identity,
    )
except ImportError:  # Executed directly by Claude's system-Python hook.
    from dependency_policy import (
        canonical_sandbox_entrypoint,
        dependency_command_violation,
        protected_gateway_identity,
    )


def preflight_dependency_violation(command):
    """Reject only violations proven by the same policy as the live guard."""
    canonical = canonical_sandbox_entrypoint()
    try:
        cwd = Path.cwd().resolve(strict=True)
    except (OSError, RuntimeError):
        cwd = None
    return dependency_command_violation(
        ["bash", "-lc", command],
        cwd=cwd,
        trusted_sandbox_entrypoints=(canonical,),
        canonical_sandbox_entrypoint=canonical,
        protected_gateway=protected_gateway_identity(),
    )


def _format_duration(seconds):
    minutes = max(1, int(math.ceil(seconds / 60)))
    if minutes < 60:
        return f"{minutes} minute{'s' if minutes != 1 else ''}"
    hours, remainder = divmod(minutes, 60)
    if not remainder:
        return f"{hours} hour{'s' if hours != 1 else ''}"
    return f"{hours}h {remainder}m"


def _budget_reminder(state_path):
    """Return one advisory reminder per crossed active-time threshold."""
    if state_path is None:
        return None
    path = Path(state_path)
    try:
        state = json.loads(path.read_text())
        budget_id = state["budget_id"]
        total = float(state["total_seconds"])
        remaining = float(state["remaining_seconds"])
        updated = float(state["updated_monotonic"])
        paused = state["paused"]
        stage_name = state.get("stage_name", "Agent stage")
        if (
            not isinstance(budget_id, str)
            or not budget_id
            or not isinstance(stage_name, str)
            or not stage_name
            or not isinstance(paused, bool)
            or not all(math.isfinite(value) for value in (total, remaining, updated))
            or total <= 0
        ):
            return None
        if not paused:
            remaining -= max(0.0, time.monotonic() - updated)
        remaining = max(0.0, remaining)
        thresholds = sorted({
            value for value in (
                total * 0.5,
                total * 0.25,
                60 * 60,
                30 * 60,
                15 * 60,
                5 * 60,
            )
            if 0 < value < total
        }, reverse=True)

        notice_path = path.with_name("agent-active-budget-notifications.json")
        try:
            notices = json.loads(notice_path.read_text())
        except (OSError, ValueError, TypeError):
            notices = {}
        notified = (
            set(notices.get("notified_thresholds", []))
            if notices.get("budget_id") == budget_id else set()
        )
        crossed = [value for value in thresholds if remaining <= value and value not in notified]
        if not crossed:
            return None
        notified.update(crossed)
        document = {
            "schema_version": 1,
            "budget_id": budget_id,
            "notified_thresholds": sorted(notified, reverse=True),
        }
        notice_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = notice_path.with_name(f".{notice_path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(document, sort_keys=True))
        os.replace(temporary, notice_path)
    except (OSError, KeyError, TypeError, ValueError):
        # Reminders are advisory and must always fail open.
        return None
    return (
        f"Active-time reminder: {stage_name} has approximately "
        f"{_format_duration(remaining)} remaining. Waiting for Runtime or Agate does "
        "not consume this budget. Prioritize a complete, verifiable result and publish "
        "the required handoff before the budget expires."
    )


def _with_reminder(output, reminder):
    if reminder:
        output.setdefault("hookSpecificOutput", {})["additionalContext"] = reminder
    return output


def hook_output(payload, timeout_ms, budget_state_path=None):
    reminder = _budget_reminder(budget_state_path)
    if not isinstance(payload, dict):
        return None
    if payload.get("tool_name") != "Bash":
        return _with_reminder({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
        }}, reminder) if reminder else None
    value = payload.get("tool_input")
    if not isinstance(value, dict) or not isinstance(value.get("command"), str):
        return _with_reminder({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
        }}, reminder) if reminder else None
    violation = preflight_dependency_violation(value["command"])
    if violation:
        return _with_reminder({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Dependency policy rejected this command before execution. "
                f"Reason: {violation}. Use provisioned dependencies and permitted "
                "Runtime tools, then continue the same task without repeating it."
            ),
        }}, reminder)
    if not any(name in value["command"] for name in ("tools/sandbox.py", "tools/plugin.py")):
        return _with_reminder({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
        }}, reminder) if reminder else None
    return _with_reminder({"hookSpecificOutput": {
        "hookEventName": "PreToolUse", "permissionDecision": "allow",
        "updatedInput": {**value, "run_in_background": False, "timeout": timeout_ms},
    }}, reminder)


def foreground_command(command, workspace, timeout_seconds, budget_state_path=None):
    if Path(command[0]).name != "claude":
        return command
    result = list(command)
    index = result.index("--settings") if "--settings" in result else None
    raw = (result[index + 1] if index is not None else "") or "{}"
    if not raw.lstrip().startswith("{"):
        path = Path(raw).expanduser()
        raw = (path if path.is_absolute() else workspace / path).read_text()
    settings = json.loads(raw)
    if not isinstance(settings, dict):
        raise ValueError("Claude settings must be a JSON object")
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict) or not isinstance(hooks.setdefault("PreToolUse", []), list):
        raise ValueError("Claude PreToolUse hooks must be an array")
    # A Supervisor virtualenv path is deliberately absent from the Agent view.
    # This hook is stdlib-only: use the mounted system interpreter on Linux.
    interpreter = Path("/usr/bin/python3") if sys.platform == "linux" else Path(sys.executable).resolve()
    if not interpreter.is_file():
        raise ValueError("Runtime foreground hooks require a system Python interpreter")
    hook_command = (
        f"{shlex.quote(str(interpreter))} "
        f"{shlex.quote(str(Path(__file__).resolve()))} "
        f"{int(timeout_seconds * 1000)}"
    )
    if budget_state_path is not None:
        hook_command += f" {shlex.quote(str(budget_state_path))}"
    hooks["PreToolUse"].append({"matcher": "*", "hooks": [{
        "type": "command",
        "command": hook_command,
    }]})
    raw = json.dumps(settings)
    if index is None:
        result[1:1] = ["--settings", raw]
    else:
        result[index + 1] = raw
    return result


if __name__ == "__main__":
    result = hook_output(
        json.load(sys.stdin),
        int(sys.argv[1]),
        Path(sys.argv[2]) if len(sys.argv) > 2 else None,
    )
    if result is not None:
        json.dump(result, sys.stdout)
