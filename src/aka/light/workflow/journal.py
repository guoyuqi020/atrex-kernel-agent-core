"""Bind the existing durable Journal protocol to one controller-owned Episode."""
from dataclasses import dataclass
from aka.light.workflow.journal_service import (
    SupervisorJournalService,
    initialize_journal,
    load_journal,
    journal_lock,
)


@dataclass(frozen=True)
class JournalFactory:
    tool_modules: tuple[str, ...] | list[str] = ("directions", "experiments")

    def __post_init__(self):
        modules = tuple(self.tool_modules)
        if len(set(modules)) != len(modules) or set(modules) - {"directions", "experiments"}:
            raise ValueError("tool_modules may contain only unique directions and experiments")
        object.__setattr__(self, "tool_modules", modules)

    @property
    def research_records(self) -> bool:
        return bool(self.tool_modules)

    @property
    def directions_enabled(self) -> bool:
        return "directions" in self.tool_modules

    @property
    def experiments_enabled(self) -> bool:
        return "experiments" in self.tool_modules

    def initialize(self, path, *, episode, base_commit, branch, memory_version):
        expected = dict(episode=episode, base_commit=base_commit, episode_branch=branch,
                        memory_version=memory_version)
        with journal_lock(path):
            if path.exists():
                value = load_journal(path)
                if any(value.get(key) != item for key, item in expected.items()):
                    raise RuntimeError("Private Runtime Journal does not match the controller Episode")
                if tuple(value.get("tool_modules", ("directions", "experiments"))) != self.tool_modules:
                    raise RuntimeError(
                        "Cannot change tool_modules for an existing Episode; resume with its "
                        "original configuration and change the option at an Episode boundary"
                    )
            else:
                initialize_journal(path, episode=episode, base_commit=base_commit,
                                   branch=branch, memory_version=memory_version,
                                   tool_modules=self.tool_modules)

    def open(self, **options):
        return SupervisorJournalService(**options)
