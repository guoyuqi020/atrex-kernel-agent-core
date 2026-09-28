"""Session instructions for independently enabled Journal tool modules."""
# Prompt prose is kept as complete sentences for the Agent.
# ruff: noqa: E501

from __future__ import annotations

import os

from runtime_contract import load_live_contract

_DEFAULT = frozenset({"directions", "experiments"})
_RUNTIME_TOOL = "agent/optimizer/src/runtime_tools.py"


def active_modules() -> frozenset[str]:
    if not os.environ.get("ATREX_RUNTIME_CONTRACT_PATH"):
        return _DEFAULT
    _, contract = load_live_contract()
    modules = contract["environment"].get("tool_modules", ["directions", "experiments"])
    if (
        not isinstance(modules, list)
        or len(modules) != len(set(modules))
        or set(modules) - _DEFAULT
    ):
        raise ValueError("Runtime contract tool modules are invalid")
    return frozenset(modules)


def modular_tool_instructions(template: str, dsl: str, modules: frozenset[str]) -> str:
    """Retain the shared Gateway contract and render only the enabled Journal tools."""
    anchor = "`record-experiment` sends each validated Experiment to Runtime immediately."
    if anchor not in template:
        raise ValueError("Session tool Prompt is missing its Journal boundary")
    common = template.split(anchor, 1)[0]
    if "directions" not in modules:
        common = "\n".join(
            line for line in common.splitlines()
            if not any(
                f" {tool} " in line
                for tool in (
                    "update-direction", "list-directions", "load-direction", "find-kernel-directions",
                )
            )
        )
    if "experiments" not in modules:
        common = "\n".join(
            line for line in common.splitlines()
            if not any(
                f" {tool} " in line
                for tool in (
                    "record-experiment", "list-experiments", "load-experiment",
                    "find-kernel-experiments",
                )
            )
        )
    adoption_start = "For exact historical source with matching trusted full-Evaluate evidence,"
    adoption_end = "\n\n`evaluate` accepts optional"
    if adoption_start not in common or adoption_end not in common:
        raise ValueError("Session tool Prompt is missing its adoption boundary")
    before, remainder = common.split(adoption_start, 1)
    _, after = remainder.split(adoption_end, 1)
    adoption = (
        "For exact historical source with matching trusted full-Evaluate evidence, record an "
        "`adopt` Experiment using real before/after Result Artifact digests. Runtime validates "
        "whether that evidence qualifies for nomination."
        if "experiments" in modules
        else "Reuse historical evidence for analysis, but nomination requires a newly measured candidate with an ordinary full Evaluate in this Attempt. Exact historical Kernel tasks may be rejected as duplicates, and this Session cannot record an adoption decision."
    )
    common = before + adoption + adoption_end + after
    common = common.replace(
        "or an explicit,\nRuntime-accepted `adopt` decision binding matching historical full-Evaluate evidence.",
        (
            "or a Runtime-accepted `adopt` Experiment binding matching historical full-Evaluate evidence."
            if "experiments" in modules
            else "and cannot use an adoption decision because the Experiment module is disabled."
        ),
    )
    common = common.replace(
        "Record the comparison as\nexperiment evidence. Nomination still requires a successful ordinary full Evaluate",
        "Use the comparison as exploratory evidence. Nomination still requires a successful ordinary full Evaluate",
    )
    if "experiments" not in modules:
        common = common.replace(
            "Never wait for that later ABBA to create evidence for an Experiment\nor to make the Report submittable.",
            "Never wait for that later ABBA to make the Report submittable.",
        )
    common = common.replace("Runtime Journal and local Report errors", "Enabled Journal and local Report errors")
    common = common.replace("{{DSL}}", dsl).replace("{{RUNTIME_TOOL}}", _RUNTIME_TOOL)
    commands = [
        "gateway-execute", "kernel-artifact-read", "result-artifact-read",
        "kernel-pareto-frontier",
    ]
    if "directions" in modules:
        commands += [
            "update-direction", "list-directions", "load-direction", "find-kernel-directions",
        ]
    if "experiments" in modules:
        commands += [
            "record-experiment", "list-experiments", "load-experiment",
            "find-kernel-experiments",
        ]
    commands.append("attempt-report")
    available = ", ".join(f"`{command}`" for command in commands)
    directions = (
        "Use update-direction to propose and start a causal hypothesis before research or edits. "
        "Close every started Direction before handoff with hypothesis_status and analysis. "
        + (
            "When closing, select real supporting_experiment_ids from this Direction's recorded Experiments."
            if "experiments" in modules
            else "No Experiment module is available, so closure needs no supporting_experiment_ids."
        )
        + " For a Kernel Artifact digest, find-kernel-directions returns distinct Direction IDs linked through visible recorded Experiments."
        if "directions" in modules
        else "Direction tools are unavailable; do not create or cite Direction IDs."
    )
    experiments = (
        "Record each meaningful measured candidate decision with record-experiment. "
        "Cite real Kernel-bound Result Artifacts: keep_after, restore_before, and adopt require "
        "both before and after; abandon_direction permits one null side, but not both; "
        "Bootstrap baseline requires before=null and a measured after. "
        + (
            "Supply the visible Direction ID to which the Experiment belongs."
            if "directions" in modules
            else "Omit direction_id: Experiments in this Session do not belong to Directions."
        )
        if "experiments" in modules
        else "Experiment tools are unavailable; do not create or cite Experiment IDs."
    )
    findings = (
        "Each Finding must cite nonempty supporting_experiment_ids from this Attempt's Journal."
        if "experiments" in modules
        else "Findings describe measured facts and decisions without supporting_experiment_ids."
    )
    journal_reads = (
        "Enabled Journal reads and writes are Runtime-local, unmetered, and durable. Read compact "
        "indexes before loading selected records. For an `adopt` decision, restore exact historical "
        "source and cite real Result Artifact digests; Runtime verifies a matching successful "
        "ordinary full Evaluate. To find Experiment IDs for an exact Kernel Artifact digest, "
        "call find-kernel-experiments; it returns only visible Journal links. "
        "Use the live schema for fields and validation limits.\n\n"
        if "experiments" in modules
        else "Use the live contract for enabled Journal fields and validation limits.\n\n"
    )
    return (
        common.rstrip() + "\n\n## Enabled Journal and terminal Report\n\n"
        f"The enabled CLI commands are {available}. Do not call commands absent from the live contract. "
        f"{directions}\n\n{experiments}\n\n"
        f"{journal_reads}"
        "Keep `scratch/attempt-report-draft.json` current with diagnosis, approach, exact candidate, "
        "correctness/performance evidence, analysis, profile evidence, knowledge use, findings, "
        "contributing Result Artifact digests, and any blocker. "
        "Profile evidence must cite real Runtime Profile Result and Kernel Artifact digests; use null "
        "when none exists. Knowledge and contributing digests must cite actually used material. "
        f"{findings} Candidate_ready requires at least one real Finding; blocked or pivot may have none. "
        "Query the live `attempt-report` schema for exact fields and submit once after all enabled "
        "Journal bookkeeping and in-flight Gateway calls finish. The CLI attaches authoritative "
        "Journal snapshots and enabled module names. On validation error, repair `issues` using "
        "`request_schema` and `recovery`, then retry; the first success is write-once. "
        "A final chat message or local report file is not a Runtime acceptance receipt."
    )


def _section(template: str, start: str, end: str | None) -> str:
    if start not in template or (end is not None and end not in template):
        raise ValueError(f"Phase Prompt section changed: {start!r} or {end!r}")
    return template.split(start, 1)[1].split(end, 1)[0] if end else template.split(start, 1)[1]


def _replace_section(template: str, start: str, end: str | None, body: str) -> str:
    original = _section(template, start, end)
    return template.replace(start + original, start + "\n\n" + body.strip() + "\n\n", 1)


def modular_workflow(template: str, *, bootstrap: bool, modules: frozenset[str]) -> str:
    """Preserve the versioned phase Prompt's common engineering guidance."""
    if bootstrap:
        return _modular_baseline(template, modules)
    return _modular_attempt(template, modules)


def _modular_attempt(template: str, modules: frozenset[str]) -> str:
    if "### 1. " not in template:
        return _modular_kda_attempt(template, modules)
    headings = [
        "### 1. Recover only relevant state",
        "### 2. Choose and plan one causal hypothesis",
        "### 3. Localize before broad changes",
        "### 4. Research progressively",
        "### 5. Implement and repair causally",
        "### 6. Validate the exact candidate",
        "### 7. Record as work proceeds",
        "### 8. Preserve reusable execution helpers",
    ]
    # Keep sections 3 and 5, which contain the concrete profiling and repair protocol.
    planning = _section(template, headings[1], headings[2])
    planning = planning[planning.index("State a falsifiable") :]
    opening = (
        "Continue a useful visible Direction or propose and start a distinct one before its research or edits. "
        if "directions" in modules else "Choose one causal hypothesis before research or edits. "
    )
    template = _replace_section(template, headings[1], headings[2], opening + planning)
    recovery = (
        "Inspect the incumbent and confirm the writable candidate initially matches it. Read the injected "
        "Evidence in order; inspect only relevant visible Journal indexes and records, if enabled. "
        "Do not replay the entire lineage by default. Reuse matching trusted measurements and exact source. "
        + (
            "To nominate exact historical source, record this Attempt's `adopt` Experiment using real "
            "before/after Result Artifact digests. Runtime validates matching full-Evaluate evidence."
            if "experiments" in modules
            else "Historical evidence may guide the work, but nominate a newly measured candidate; an exact historical Kernel can be rejected as a duplicate and cannot be adopted in this Session."
        )
    )
    template = _replace_section(template, headings[0], headings[1], recovery)
    research = (
        "Use visible Journal history from enabled modules for what this lineage already measured, "
        "and the knowledge query command for external architecture-, DSL-, compiler-, and operator-specific facts. "
        "This workspace carries no upstream project checkout. Preserve stable knowledge Record IDs only "
        "for records that materially affect the work. Test every adopted recommendation; stop research "
        "when one actionable hypothesis has adequate support."
    )
    template = _replace_section(template, headings[3], headings[4], research)
    validation = (
        "Development and check operations may accelerate repair, but a nominated candidate requires a "
        "successful ordinary full Evaluate for the exact current `work/kernel/` tree"
        + (
            ", either measured here or bound by a Runtime-accepted historical `adopt` Experiment. "
            if "experiments" in modules else ", measured in this Attempt. "
        )
        + "Require reported correctness, finite positive latency, and credible performance evidence. "
        "Agent ABBA is exploratory and cannot replace that full Evaluate. Runtime's authoritative "
        "ABBA happens after terminal Report handoff and creates no Agent Trial; never wait for it. "
        "Only controller policy decides whether the Kernel or Agent is retained."
    )
    template = _replace_section(template, headings[5], headings[6], validation)
    recording = (
        "After every decisive measured keep, restoration, or ending result, record the Experiment before "
        "another edit. Keep observations in `evidence`, interpretation in `analysis`, and cite exact "
        "Result Artifact digests. Negative results are first-class evidence. "
        if "experiments" in modules else "Preserve factual measurements and decisions in the Report draft. "
    )
    recording += (
        "Close every started Direction before handoff, explicitly selecting relevant supporting "
        "Experiment IDs and `hypothesis_status` (`unresolved`, `supported`, or `refuted`). "
        if modules == _DEFAULT else
        "Close every started Direction before handoff with `hypothesis_status`; no Experiment linkage is required. "
        if "directions" in modules else ""
    )
    recording += (
        "Keep the structured Report draft current. Do not fabricate evidence to finish. "
        "Submit with `attempt-report`; `blocked` or `pivot` may have empty Findings when justified."
    )
    template = _replace_section(template, headings[6], headings[7], recording)
    helper = _section(template, headings[7], "## Terminal behavior").split("Use existing Prompts", 1)[0]
    helper += (
        "Use existing Prompts and Skills when relevant, but do not modify them. Evolver reviews "
        "completed Session evidence and may make only task-independent changes to Prompts, Skills, "
        "Tools, or workflow. It does not select Kernel optimization hypotheses."
    )
    template = _replace_section(template, headings[7], "## Terminal behavior", helper)
    terminal = (
        "Stop only with a mature evaluated candidate, an exhausted or reverted hypothesis, or a "
        "genuine external blocker. Follow the exact status, enabled Journal closure, Finding, and "
        "Report schema in the Session-tool contract. Never invent correctness, performance, profiler "
        "output, or knowledge use merely to terminate; an evidence-backed pivot is valid."
    )
    return _replace_section(template, "## Terminal behavior", None, terminal).rstrip()


def _modular_kda_attempt(template: str, modules: frozenset[str]) -> str:
    """Adapt the KDA task-oriented phase Prompt without dropping its planning rules."""
    workflow = _section(template, "## Workflow", "## Plan Draft Requirements")
    workflow = workflow.replace(
        "For exact historical source, register an explicit `adopt` decision with real before/after Result Artifact digests; Runtime validates its successful ordinary full-Evaluate evidence without changing the original Trial's ownership.",
        (
            "For exact historical source, record an `adopt` Experiment with real before/after "
            "Result Artifact digests; Runtime validates matching full-Evaluate evidence."
            if "experiments" in modules
            else "Historical results may guide the work, but nominate a newly measured candidate; an exact historical Kernel may be rejected as a duplicate and cannot be adopted in this Session."
        ),
    )
    workflow = workflow.replace(
        "Record candidate results, parent relationships, and evidence through the supplied Journal tools as work proceeds.",
        "Record candidate results and evidence through enabled Journal tools and the Report draft as work proceeds.",
    )
    template = _replace_section(template, "## Workflow", "## Plan Draft Requirements", workflow)
    execution = _section(template, "## Execution and evidence", "## Terminal handoff")
    execution = execution.replace(
        "- Register and start a Direction with `update-direction` when beginning its research or exploration, not only when editing the Kernel. Follow the shared tool contract for the single in-progress Direction and per-Attempt limits.\n",
        "" if "directions" not in modules else "- Propose and start a Direction before its research or exploration; close it before handoff.\n",
    )
    execution = execution.replace(
        "- After every decisive measured keep, restoration, or direction-ending result, call `record-experiment` before another edit. Supply the exact before/after Result Artifact digests; Runtime resolves their Kernel and Result Artifacts. Separate factual evidence from analysis. Negative results are first-class evidence.\n",
        (
            "- After every decisive measured decision, call `record-experiment` before another edit. "
            "Cite exact Result Artifact digests, separate factual evidence from analysis, and retain negative results.\n"
            if "experiments" in modules else "- Preserve decisive measured decisions and negative results in the Report draft.\n"
        ),
    )
    execution = execution.split("- Treat `prompts/` and `skills/`", 1)[0] + (
        "- Treat `prompts/` and `skills/` as read-only Agent Revision content. Use applicable Skills, "
        "but record task-specific hypotheses and evidence through enabled Journal tools and the Report. "
        "Only reusable executable helpers belong in `tools/`; keep its index current. Evolver may "
        "curate task-independent Prompts, Skills, and Tools from completed Session evidence.\n"
    )
    template = _replace_section(template, "## Execution and evidence", "## Terminal handoff", execution)
    terminal = (
        "Build the terminal Report incrementally. Submit it with `attempt-report` using the live schema; "
        "repair validation errors and resubmit when needed. "
        + ("Close every started Direction before handoff. " if "directions" in modules else "")
        + "Stop with an evaluated candidate, an exhausted hypothesis, or a genuine blocker. "
        "An ordinary full Evaluate of the exact candidate is required for nomination"
        + (" unless Runtime accepts a matching historical `adopt` Experiment. " if "experiments" in modules else ". ")
        + "Agent ABBA is exploratory; Runtime's authoritative ABBA runs after terminal handoff. "
        "Do not fabricate evidence to finish; chat text is not a terminal Report."
    )
    return _replace_section(template, "## Terminal handoff", None, terminal).rstrip()


def _modular_baseline(template: str, modules: frozenset[str]) -> str:
    template = template.replace(
        "Record new hypotheses, evidence, and conclusions through the Runtime Direction and\n  Experiment Journal.",
        "Record new hypotheses, evidence, and conclusions through enabled Journal tools and the Report.",
    )
    headings = [
        "### 1. Reconstruct the operator contract",
        "### 2. Learn only what is needed",
        "### 3. Establish the first self-contained DSL Kernel",
        "### 4. Validate and repair",
    ]
    learning = (
        "After the minimal operator-contract review, choose one baseline-construction hypothesis. "
        + ("Propose and start it before direction-specific work. " if "directions" in modules else "")
        + "Use the seed, included Skills, and public contract. Select knowledge relevant to the actual "
        "architecture, DSL, operator, and mechanism. Stop once one viable approach has adequate support "
        "and keep its actionable constraints in `scratch/`."
    )
    template = _replace_section(template, headings[1], headings[2], learning)
    construction = _section(template, headings[2], headings[3])
    construction = construction.replace("Under the already-started baseline-construction Direction, inspect", "Inspect")
    construction = construction.replace(
        "Use the shared Direction and Experiment Journal to preserve every\ndecisive construction, repair, retained change, and reverted failure for later optimization\nAttempts, but do not expand framework bring-up into an unbounded performance search.",
        (
            "Record each decisive construction, repair, retained change, and reverted failure with "
            "the enabled Journal tools and Report for later Attempts. Do not expand framework bring-up "
            "into an unbounded performance search."
        ),
    )
    template = _replace_section(template, headings[2], headings[3], construction)
    validation = _section(template, headings[3], "## Terminal contract")
    validation = validation.split("Record each meaningful repair as an Experiment", 1)[0]
    validation += (
        "Record each meaningful repair as an Experiment with real before/after Result Artifact digests. "
        "The first measured construction requires exactly one `baseline` Experiment with `before=null` "
        "and the measured Result as `after`. "
        + ("Supply its started Direction ID. " if "directions" in modules else "Omit `direction_id`. ")
        if "experiments" in modules else "Preserve measured repair evidence in the Report draft. "
    )
    validation += "\n\nBefore nomination," + _section(
        template, "Before nomination,", "## Terminal contract"
    )
    template = _replace_section(template, headings[3], "## Terminal contract", validation)
    terminal = _section(template, "## Terminal contract", None)
    terminal = terminal.split("Do not use `pivot`", 1)[0]
    terminal += (
        "Do not use `pivot` during Bootstrap. Before handoff, "
        + ("close every started Direction. " if "directions" in modules else "")
        + ("Record exactly one baseline Experiment for `candidate_ready`. " if "experiments" in modules else "")
        + "Do not fabricate evidence to satisfy a schema. Use the shared Report fields with Bootstrap "
        "semantics: `diagnosis` names the bring-up or correctness issue, `approach` explains construction "
        "or repair, and `expected_impact` states the expected correctness or compatibility effect. "
        "Set `profile_evidence` to null unless profiling was actually needed. An accepted "
        "`candidate_ready` Report nominates a candidate for the private Bootstrap Gate; it does not "
        "itself register the baseline."
    )
    return _replace_section(template, "## Terminal contract", None, terminal).rstrip()


def modular_evidence_prompt(prompt: str, modules: frozenset[str]) -> str:
    """Keep trusted workspace/history facts while replacing coupled Journal instructions."""
    if modules == _DEFAULT:
        return prompt.rstrip()
    prefix = prompt.split("## Direction ancestry", 1)[0]
    prefix = prefix.replace(
        "Record task hypotheses, evidence, and conclusions through the Direction\n"
        "and Experiment Journal. Evolver may use that evidence to improve task-independent Agent behavior,\n"
        "but it does not publish task knowledge or choose future Kernel optimization Directions.",
        "Use only the Journal modules enabled by the live Session contract.",
    )
    prefix = prefix.replace(
        "Exact historical\nKernel, Trial, Result, Direction, and Experiment records remain in controller storage and are\n"
        "retrieved through the supplied Runtime-local query commands. Every Direction update and Experiment\n"
        "record is durably appended by Runtime before its tool call returns; a Worker crash or recovery\n"
        "generation does not roll the logical Attempt Journal back. Journal queries may include every\n"
        "completed Active and Challenger path from a frozen Epoch, without exposing branch-control\n"
        "provenance. No Journal history file exists under `input/evidence/` or the internal control area.",
        "Exact historical Kernel and Result records remain in controller storage and are "
        "retrieved through enabled Runtime-local commands. Enabled Journal writes are durable "
        "across a Worker crash or recovery generation.",
    )
    if "## Trust and measurement reuse" not in prompt:
        return prefix.rstrip()
    ancestry = (
        "## Direction ancestry\n\n"
        "Resume an unfinished hypothesis with its existing Direction ID. For a new hypothesis "
        "derived from earlier work, propose a Direction with the appropriate `relationship` "
        "(`retry`, `refinement`, `reimplementation`, `correction`, `port`, `combination`, or "
        "`adoption`), cite visible `derived_from_direction_ids`, and explain the link in "
        "`rationale`. A combination needs two distinct parent Directions. A correction may set "
        "`supersedes_direction_id` to one of its parents. Read real IDs with `list-directions` "
        "and `load-direction` first. Historical suggestions remain readable, but no new suggestion "
        "can be created. Ancestry records an interpretation, not proof of a performance gain.\n\n"
        if "directions" in modules else ""
    )
    trust = prompt.split("## Trust and measurement reuse", 1)[1]
    trust_prefix = trust.split("To select an unchanged Kernel", 1)[0]
    trust_tail = trust.split("Agent-requested ABBA is exploratory", 1)[1]
    trust_tail = "Agent-requested ABBA is exploratory" + trust_tail.split("`complete`, `abandon`", 1)[0]
    trust_tail = trust_tail.replace("before recording an Experiment or submitting", "before submitting")
    trust_private = "Private evaluator inputs remain hidden; opaque Shape identifiers and measurements must not be used to reconstruct them."
    reuse = (
        "To select an unchanged Kernel from visible history, record an `adopt` Experiment with real "
        "before and after Result Artifact digests. Runtime verifies matching successful ordinary "
        "full-Evaluate evidence for the exact source."
        if "experiments" in modules
        else "Reuse historical measurements for analysis. Nomination needs a newly measured candidate "
        "with an ordinary full Evaluate in this Attempt; an exact historical Kernel task may be "
        "rejected as a duplicate and cannot be adopted in this Session."
    )
    return (
        prefix.rstrip() + "\n\n" + ancestry + "## Trust and measurement reuse\n\n"
        + trust_prefix.strip() + "\n\n" + reuse + "\n\n"
        + trust_tail.strip() + "\n\n" + trust_private + "\n"
    )
