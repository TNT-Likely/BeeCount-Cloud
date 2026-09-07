"""Minimal plugin runtime contracts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel


@dataclass(frozen=True)
class PluginRunResult:
    transactions: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


PluginRunner = Callable[[BaseModel], PluginRunResult]


@dataclass(frozen=True)
class PluginDefinition:
    plugin_id: str
    name: str
    description: str
    input_model: type[BaseModel]
    run: PluginRunner
    name_i18n: dict[str, str] = field(default_factory=dict)
    description_i18n: dict[str, str] = field(default_factory=dict)

    def manifest(self) -> dict[str, Any]:
        return {
            "id": self.plugin_id,
            "name": self.name,
            "description": self.description,
            "name_i18n": self.name_i18n,
            "description_i18n": self.description_i18n,
            "input_schema": self.input_model.model_json_schema(),
        }
