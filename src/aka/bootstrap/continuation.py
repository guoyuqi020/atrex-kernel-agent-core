"""Serialize effective selections; validate them before any plugin setup in a child."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Mapping, Sequence

from aka.core.boot import freeze, token_table
from aka.core.composition import ResolvedComposition, Row
from aka.core.errors import CompositionError
from aka.core.keys import ServiceKey
from aka.core.lock import package_dir, resource_identity
from .profile import Selection, registered_tokens, startup_token

SELECTION_ENV = "AKA_LAUNCH_SELECTION"
DIGEST_ENV = "AKA_LAUNCH_DIGEST"


def encode(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def capture(selection: Selection) -> tuple[Selection, str]:
    effective, identity = freeze(selection.composition, tokens=selection.tokens)
    resources = [{"root": str(root), "paths": list(paths), "submodules": dict(selection.submodules.get(str(root), {})),
                  "identity": resource_identity(root, paths, submodules=selection.submodules.get(str(root), {}))}
                 for root, paths in selection.resources]
    host = {package: resource_identity(package_dir(package), (".",))
            for package in ("aka.core", "aka.bootstrap", "aka.contracts")}
    value = {"version": 1, "target": selection.target,
             "required_services": list(selection.required_services),
             "bindings": dict(selection.bindings), "environment": dict(selection.environment),
             "composition": {"profile": effective.profile, "rows": [asdict(row) for row in effective.rows],
                             "layers": list(effective.layers), "variables": dict(effective.variables)},
             "identity": identity, "resources": resources, "host": host}
    return replace(selection, composition=effective), encode(value)


def read(environment: Mapping[str, str | None]) -> str | None:
    path, expected = environment.get(SELECTION_ENV), environment.get(DIGEST_ENV)
    if path is None and expected is None:
        return None
    if not path or not expected:
        raise CompositionError("continuation", "selection path and digest must travel together")
    try:
        payload = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise CompositionError("continuation", f"cannot read selection: {exc}") from exc
    if digest(payload) != expected:
        raise CompositionError("continuation", "selection digest mismatch")
    return payload


def restore(payload: str, *, tokens: Sequence[ServiceKey] = ()) -> Selection:
    try:
        value = json.loads(payload)
        if (not isinstance(value, dict) or type(value.get("version")) is not int
                or value["version"] != 1
                or set(value) != {"version", "target", "required_services", "composition", "identity", "resources", "host", "bindings", "environment"}):
            raise ValueError("unsupported selection format")
        comp = value["composition"]
        rows = tuple(Row(**{**row, "inject": tuple(row["inject"]), "isolate": tuple(row["isolate"])})
                     for row in comp["rows"])
        composition = ResolvedComposition(comp["profile"], rows, tuple(comp["layers"]), comp["variables"], configs_resolved=True)
        startup = startup_token(value["target"])
        registry = token_table((*registered_tokens(tokens).values(), startup))
        selected = []
        for recorded in value["identity"]["tokens"]:
            token = ServiceKey(**recorded)
            if registry.get(token.name) is None or replace(registry[token.name], doc="") != token:
                raise ValueError(f"token {token.name} is unavailable or changed")
            selected.append(token)
        resources = tuple((Path(item["root"]), tuple(item["paths"])) for item in value["resources"])
        selection = Selection(composition, value["target"], tuple(selected),
                              tuple(value["required_services"]), resources,
                              {item["root"]: item["submodules"] for item in value["resources"] if item["submodules"]},
                              value["bindings"], value["environment"])
        _, current = capture(selection)
        if json.loads(current) != value:
            raise ValueError("effective config, implementation or resource identity mismatch")
        return selection
    except (KeyError, TypeError, ValueError) as exc:
        raise CompositionError("continuation", f"cannot reconstruct selection: {exc}") from exc

