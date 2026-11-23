"""Built-in plugin registry."""

from __future__ import annotations

from .base import PluginDefinition
from .mortgage import MORTGAGE_PLUGIN


class PluginRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, PluginDefinition] = {}

    def register(self, plugin: PluginDefinition) -> None:
        if plugin.plugin_id in self._plugins:
            raise ValueError(f"duplicated plugin id: {plugin.plugin_id}")
        self._plugins[plugin.plugin_id] = plugin

    def list(self) -> list[PluginDefinition]:
        return list(self._plugins.values())

    def get(self, plugin_id: str) -> PluginDefinition | None:
        return self._plugins.get(plugin_id)


registry = PluginRegistry()
registry.register(MORTGAGE_PLUGIN)
