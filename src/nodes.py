"""Factories that build live / backtest Nautilus nodes from raw config dicts."""

from copy import deepcopy

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.config import BacktestRunConfig
from nautilus_trader.config import TradingNodeConfig
from nautilus_trader.config import resolve_path
from nautilus_trader.live.node import TradingNode

from src.config import build_config


def build_live_node(system_config: dict, strategies_config: dict) -> TradingNode:
    """Build a configured :class:`TradingNode` for live trading."""
    system_config = deepcopy(system_config)
    strategies_config = deepcopy(strategies_config)
    config = system_config["config"]
    config.update(strategies_config)
    built = build_config(system_config)

    # TradingNodeConfig expects data_clients / exec_clients as dicts keyed by venue.
    for key in ("data_clients", "exec_clients"):
        built[key] = {str(client.venue): client for client in built[key]}

    node = TradingNode(config=TradingNodeConfig(**built))
    _register_client_factories(node, config)
    node.build()
    return node


def build_backtest_node(system_config: dict, strategies_config: dict) -> BacktestNode:
    """Build a configured :class:`BacktestNode`.

    Strategies/actors are merged into ``engine.config`` — that's where
    :class:`BacktestRunConfig` expects them to live.
    """
    system_config = deepcopy(system_config)
    strategies_config = deepcopy(strategies_config)
    config = system_config["config"]
    config["engine"]["config"].update(strategies_config)
    built = build_config(system_config)
    node = BacktestNode(configs=[BacktestRunConfig(**built)])
    node.build()
    return node


def _register_client_factories(node: TradingNode, config: dict) -> None:
    """Wire up data/exec client factories on a live :class:`TradingNode`."""
    for client in config.get("data_clients") or []:
        node.add_data_client_factory(
            client["config"]["venue"], resolve_path(client["factory_path"])
        )
    for client in config.get("exec_clients") or []:
        node.add_exec_client_factory(
            client["config"]["venue"], resolve_path(client["factory_path"])
        )
