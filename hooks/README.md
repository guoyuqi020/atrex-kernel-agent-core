# Reusable hooks index

Store reusable Claude/Codex hook scripts and configuration snippets. Group backend-specific files
under claude/ or codex/ when useful. Keep general-purpose scripts in tools/ and reference them
instead of duplicating them.

Whenever you add, change, rename, or remove a hook, update this README with its path, backend,
trigger/event, purpose, configuration/activation steps, exact command, inputs, outputs, dependencies,
side effects, and limitations. Read this index before adding duplicates. Do not store credentials,
temporary outputs, or host-specific absolute paths.

Runtime preserves this directory with the other adaptive State directories. Storage alone does not
register or activate a hook: the selected backend must explicitly load its configuration. Record
whether activation was verified; do not claim a hook ran just because its files are present.

## Contents

No initial hooks. Maintain the current hook index here.
