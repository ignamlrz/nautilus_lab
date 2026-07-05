"""Top-level orchestrator for the Nautilus Trader lab."""

from __future__ import annotations

import argparse
from collections.abc import Callable

from nautilus_trader.backtest.node import BacktestNode
from nautilus_trader.live.node import TradingNode
from nautilus_trader.system.kernel import NautilusKernel

from src.api.keys import NAUTILUS_TRADER_KEY
from src.api.webapp import app
from src.config import load_yaml
from src.nodes import build_backtest_node
from src.nodes import build_live_node


_BUILDERS: dict[str, Callable] = {
    "live": build_live_node,
    "backtest": build_backtest_node,
}


class NautilusTraderApp:
    """Nautilus Trader lab entry point.

    Owns the two loaded config dicts (system + strategies) and runs the
    appropriate node based on ``system_config["type"]``.
    """

    def __init__(self, system_config: dict, strategies_config: dict) -> None:
        self.system_config = system_config
        self.strategies_config = strategies_config

    @classmethod
    def from_args(cls, args: argparse.Namespace) -> NautilusTraderApp:
        """Build the app by loading YAMLs from paths in ``args`` (or from CLI)."""
        return cls(
            system_config=load_yaml(args.system_config),
            strategies_config=load_yaml(args.strategies_config),
        )

    @property
    def kernel(self) -> NautilusKernel | None:
        if isinstance(self.node, BacktestNode):
            return self.node.get_engines()[0].kernel
        elif isinstance(self.node, TradingNode):
            return self.node.kernel
        return None

    def run(self) -> None:
        """Pick the right builder for ``system_config["type"]`` and run the node."""
        kind = self.system_config.get("type", "backtest")
        build = _BUILDERS.get(kind, build_backtest_node)
        self.node = node = build(self.system_config, self.strategies_config)
        # register nautilus app in aiohttp app for access in subapps
        app[NAUTILUS_TRADER_KEY] = self
        for subapp in app._subapps:
            subapp[NAUTILUS_TRADER_KEY] = self
        node.run()
