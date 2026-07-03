"""YAML loading + recursive conversion of dict trees into Nautilus config objects."""

from pathlib import Path
from typing import Any

import msgspec
import yaml
from nautilus_trader.common.config import msgspec_encoding_hook
from nautilus_trader.config import ImportableActorConfig
from nautilus_trader.config import ImportableStrategyConfig
from nautilus_trader.config import resolve_config_path

from src.api.server.agent import ServerAgentActor
from src.api.server.agent import ServerAgentActorConfig


def load_yaml(path: Path) -> dict:
    """Read a YAML file, returning an empty dict when the file is empty."""
    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path.resolve()}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def build_config(data: Any) -> Any:
    """Recursively convert a dict tree into Nautilus config objects.

    Rules:

    * Lists are walked recursively, returning a list of converted items.
    * Dicts with ``actor_path`` (and no ``strategy_path``) become
      :class:`ImportableActorConfig`.
    * Dicts with ``strategy_path`` become
      :class:`ImportableStrategyConfig`.
    * Dicts with ``config_path`` have their ``config`` sub-dict resolved
      via :func:`resolve_config_path` and parsed into the target class with msgspec.
    * Plain dicts are walked recursively, returning a dict of converted values.
    * All other values are returned unchanged.

    The function is pure: it never mutates its input.
    """
    if "server" in data and data["server"]["enabled"]:
        server_agent_actor_config = ImportableActorConfig(
            ServerAgentActor.fully_qualified_name(),
            config_path=ServerAgentActorConfig.fully_qualified_name(),
            config=data["server"],
        ).dict()
        if data["type"] == "live":
            data["config"]["actors"] = data["config"].get("actors", []) + [
                server_agent_actor_config
            ]
        elif data["type"] == "backtest":
            engine_config = data["config"]["engine"]["config"]
            engine_config["actors"] = engine_config.get("actors", []) + [server_agent_actor_config]
    return build_property(data["config"])


def build_property(data: Any) -> Any:
    if isinstance(data, list):
        return [build_property(item) for item in data]

    if isinstance(data, dict):
        nested = {key: build_property(value) for key, value in data.items()}
        if "config_path" in nested:
            if "actor_path" in nested:
                return ImportableActorConfig(**nested)
            if "strategy_path" in nested:
                return ImportableStrategyConfig(**nested)
            config_cls = resolve_config_path(nested["config_path"])
            encoded = msgspec.json.encode(nested["config"], enc_hook=msgspec_encoding_hook)
            return config_cls.parse(encoded)
        return nested

    return data
