# Skills index

Store reusable procedures: when to apply a method, its steps, prerequisites, and validation criteria.
Use a Skill's references for static reference material, insights/ for evidence-derived search
conclusions, and tools/ for executable scripts.

Evolver owns changes to this directory. Optimizer and Bootstrap sessions use it read-only and record
new findings in the Runtime Journal; they may add reusable executable helpers only under `tools/`.

For Claude discovery, use `<skill-name>/SKILL.md` with YAML `name` and `description` frontmatter;
place supporting scripts/references inside that Skill directory. Before each Claude
Optimizer or Bootstrap session, Runtime copies these Skill directories into its private CLI Home.
Loose notes and README files are not registered Skills. Edit the originals here for persistence;
installed copies are session-local and refreshed at the next launch.

Whenever you add, change, rename, or remove a Skill, update this README with its path, purpose,
trigger conditions, dependencies, and limitations. Read this index before adding duplicates.
Keep Skills concise and general where possible; do not store credentials or raw traces.

## Contents

No initial Skills. Evolver adds and indexes evidence-backed reusable procedures here.
