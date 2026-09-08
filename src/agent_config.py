"""Evolvable Agent behavior configuration owned by the Core revision."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path

from contexts.common import object_value, text_value, within


@dataclass(frozen=True)
class AgentConfig:
    agent_backend: str
    reasoning_effort: str
    session_settings: str
    prompt_paths: Mapping[str, Path]
    runtime_bound: bool = False
    model: str | None = None
    prompt_fragment_paths: Mapping[str, Path] = field(default_factory=dict)
    report_completion_retries: int = 2

    @classmethod
    def load(
        cls,
        repository: Path,
        environment: Mapping[str, str] | None = None,
        *,
        workspace: Path | None = None,
    ) -> AgentConfig:
        config_path = repository / "atrex-agent.json"
        if config_path.is_symlink() or not config_path.is_file():
            raise ValueError("Agent config must be a regular file")
        value = object_value(json.loads(config_path.read_text(encoding="utf-8")), "Agent config")
        if value.get("schema_version") != 2:
            raise ValueError("unsupported Agent config schema_version")
        allowed = {
            "schema_version",
            "agent_backend",
            "reasoning_effort",
            "session_settings",
            "model",
            "prompts",
            "prompt_fragments",
            "prompt_root",
            "report_completion_retries",
        }
        unknown = set(value) - allowed
        if unknown:
            raise ValueError(f"unknown Agent config fields: {sorted(unknown)}")
        report_retries = value.get("report_completion_retries", 2)
        if type(report_retries) is not int or not 0 <= report_retries <= 10:
            raise ValueError("report_completion_retries must be an integer from 0 to 10")
        prompt_root = value.get("prompt_root", "repository")
        if not isinstance(prompt_root, str) or prompt_root not in {"repository", "workspace"}:
            raise ValueError("prompt_root must be repository or workspace")
        if prompt_root == "workspace" and workspace is None:
            raise ValueError("workspace prompt_root requires the Runtime session workspace")

        def resolve_prompt(raw: object, label: str) -> Path:
            relative = text_value(raw, label)
            if prompt_root == "workspace":
                if not relative.startswith("prompts/"):
                    raise ValueError(f"{label} must be under workspace prompts/")
                assert workspace is not None
                return within(workspace.resolve() / "prompts", relative[len("prompts/") :], label)
            return within(repository, relative, label)

        backend = text_value(value.get("agent_backend"), "agent_backend")
        if backend not in {"claude", "codex", "pi", "qodercli"}:
            raise ValueError(f"unsupported Core agent backend: {backend}")
        effort = text_value(value.get("reasoning_effort"), "reasoning_effort")
        if effort not in {"low", "medium", "high", "max"}:
            raise ValueError(f"unsupported reasoning effort: {effort}")
        settings = value.get("session_settings", "")
        if not isinstance(settings, str):
            raise ValueError("session_settings must be a string")
        model_value = value.get("model")
        if model_value is not None:
            model_value = text_value(model_value, "model")
        prompts = object_value(value.get("prompts"), "Agent prompts")
        expected_prompts = {
            "problem_generalization",
            "framework_baseline",
            "optimization_attempt",
        }
        if set(prompts) != expected_prompts:
            raise ValueError("Agent prompts must define every supported session phase")
        prompt_paths: dict[str, Path] = {}
        for phase, prompt_value in prompts.items():
            prompt = resolve_prompt(prompt_value, phase)
            if prompt.is_symlink() or not prompt.is_file():
                raise ValueError(f"Agent prompt is unavailable: {phase}")
            prompt_paths[phase] = prompt
        prompt_fragments = object_value(
            value.get("prompt_fragments"),
            "Agent prompt fragments",
        )
        expected_fragments = {"attempt_tools"}
        if set(prompt_fragments) != expected_fragments:
            raise ValueError("Agent prompt_fragments must define every supported fragment")
        prompt_fragment_paths: dict[str, Path] = {}
        for name, fragment_value in prompt_fragments.items():
            fragment = resolve_prompt(fragment_value, name)
            if fragment.is_symlink() or not fragment.is_file():
                raise ValueError(f"Agent prompt fragment is unavailable: {name}")
            prompt_fragment_paths[name] = fragment
        binding = os.environ if environment is None else environment
        if "ATREX_REPORT_COMPLETION_RETRIES" in binding:
            raw_retries = binding["ATREX_REPORT_COMPLETION_RETRIES"]
            if not raw_retries.isascii() or not raw_retries.isdigit():
                raise ValueError(
                    "Runtime report_completion_retries must be an integer from 0 to 10"
                )
            report_retries = int(raw_retries)
            if not 0 <= report_retries <= 10:
                raise ValueError(
                    "Runtime report_completion_retries must be an integer from 0 to 10"
                )
        binding_keys = {
            "ATREX_AGENT_BACKEND",
            "ATREX_AGENT_MODEL",
            "ATREX_AGENT_REASONING_EFFORT",
            "ATREX_AGENT_SESSION_SETTINGS",
        }
        present = binding_keys.intersection(binding)
        if present and present != binding_keys:
            missing = sorted(binding_keys - present)
            raise ValueError(f"incomplete Runtime Agent binding; missing: {missing}")
        runtime_bound = bool(present)
        if runtime_bound:
            backend = text_value(binding["ATREX_AGENT_BACKEND"], "Runtime agent backend")
            if backend not in {"claude", "codex", "pi", "qodercli"}:
                raise ValueError(f"unsupported Runtime Core agent backend: {backend}")
            effort = text_value(
                binding["ATREX_AGENT_REASONING_EFFORT"],
                "Runtime reasoning effort",
            )
            if effort not in {"low", "medium", "high", "max"}:
                raise ValueError(f"unsupported Runtime reasoning effort: {effort}")
            settings = binding["ATREX_AGENT_SESSION_SETTINGS"]
            if "\x00" in settings:
                raise ValueError("Runtime session settings cannot contain NUL")
            runtime_model = binding["ATREX_AGENT_MODEL"].strip()
            if "\x00" in runtime_model:
                raise ValueError("Runtime model cannot contain NUL")
            model_value = runtime_model or None
        return cls(
            agent_backend=backend,
            reasoning_effort=effort,
            session_settings=settings,
            prompt_paths=prompt_paths,
            runtime_bound=runtime_bound,
            model=model_value,
            prompt_fragment_paths=prompt_fragment_paths,
            report_completion_retries=report_retries,
        )

    def prompt_path(self, phase: str) -> Path:
        try:
            return self.prompt_paths[phase]
        except KeyError as error:
            raise ValueError(f"unsupported Core session phase: {phase}") from error

    def prompt_fragment_path(self, name: str) -> Path:
        try:
            return self.prompt_fragment_paths[name]
        except KeyError as error:
            raise ValueError(f"unsupported Core prompt fragment: {name}") from error
