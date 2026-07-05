"""Shared aiohttp AppKey definitions for the API layer."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from aiohttp import web


if TYPE_CHECKING:
    from src.app import NautilusTraderApp
else:
    NautilusTraderApp = object


NAUTILUS_TRADER_KEY = web.AppKey("nautilus_trader", NautilusTraderApp)
LOOP_KEY = web.AppKey("loop", asyncio.AbstractEventLoop)
