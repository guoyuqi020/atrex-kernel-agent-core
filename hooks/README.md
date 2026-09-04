# Reusable hooks index

Store reusable Claude/Codex command hooks. Put backend-native registrations in `claude.json` and
`codex.json`, each shaped as `{"hooks": {"EventName": [{"hooks": [{"type": "command",
"command": "python3 \"$WORKSPACE_ROOT/hooks/example.py\""}]}]}}`. Keep reusable scripts here or
in `tools/`; commands should use the quoted `$WORKSPACE_ROOT` path, not a host-specific path.

Whenever you add, change, rename, or remove a hook, update this README with its path, backend,
trigger/event, purpose, configuration/activation steps, exact command, inputs, outputs, dependencies,
side effects, and limitations. Read this index before adding duplicates. Do not store credentials,
temporary outputs, or host-specific absolute paths.

Before each Optimizer/Bootstrap session, Runtime installs the selected backend's JSON in that
session's private CLI Home. It never edits host/global configuration or executes hook commands
during installation. Codex noninteractive launches trust these hooks for that invocation only;
the CLI must support `--dangerously-bypass-hook-trust`. Other backends only preserve these files.
Definitions are refreshed at the next session start. Edit this directory, not the generated CLI
configuration, to persist changes. Hook event names, matchers and outputs follow the selected CLI's
native API. Record whether execution was verified; installation alone is not evidence that a hook ran.

## Contents

No initial hooks. Maintain the current hook index here.
