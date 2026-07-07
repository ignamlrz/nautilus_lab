# Telegram alerts

Outbound-only wrapper around `python-telegram-bot` for sending trading alerts
to your phone. No polling, no command handlers in the bot itself — just
fire-and-forget text.

## One-time setup

1. **Create a bot** — in Telegram, talk to [@BotFather](https://t.me/BotFather),
   send `/newbot`, follow the prompts. You'll get a **bot token** like
   `123456:ABC-DEF...`.

2. **Discover your chat_id** — run the helper script with your token, then open
   Telegram and send `/start` to your new bot:

   ```bash
   export TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   uv run python scripts/get_telegram_chat_id.py
   ```

   The script prints your numeric `chat_id` (a positive integer for users,
   negative for groups). Save it.

3. **Set env vars** — put both in your shell or in a `.env` loaded by your
   trading runner:

   ```bash
   export TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
   export TELEGRAM_CHAT_IDS=987654321         # single recipient
   # or for several recipients:
   export TELEGRAM_CHAT_IDS=987654321,-1001234567890
   ```

   Optional knobs:
   - `TELEGRAM_PARSE_MODE` — `HTML` (default), `MarkdownV2`, or empty for plain
   - `TELEGRAM_SILENT` — set to `1`/`true` to send without notification sound

## Usage

### From async code (the trading engine)

```python
from src.notifications import TelegramNotifier

notifier = TelegramNotifier.from_env()

await notifier.send("🚀 BTC-USDT broke 100k resistance")
await notifier.send(
    TelegramNotifier.format_trade(
        side="BUY", symbol="BTC-USDT", price=100_123.4, size=0.01,
        extra="reason: bull trap breakout",
    ),
)
```

The notifier logs and skips any recipient that fails, so a flaky network or a
deleted chat won't crash the trading loop.

### From a sync script

```python
from src.notifications import TelegramNotifier

TelegramNotifier.from_env().send_sync("Backtest finished — sharpe 2.1")
```

### Multiple recipients

`TELEGRAM_CHAT_IDS` is comma-separated. The same message fans out to every
listed chat. Useful for: personal phone + shared group + a backup account.

## Where to call it from

This module is intentionally decoupled from the strategy. Wire it in wherever
you have a meaningful event:

- **`on_trade_tick`** / signal hooks in `src/strategies/orderbooks/strategy.py`
  — alert on detected spoofing or trend signals
- **`on_start` / `on_stop`** — startup and shutdown heartbeat
- **API server** — alert on connection drops in `src/api/server/`
- **A separate watcher** — pull PnL periodically and DM yourself

Because Nautilus strategies are sync and the trading loop is async, the
easiest place is usually **outside** the actor — wrap the notifier in an
asyncio task from `main.py` and push events onto a queue, or call it from the
async API layer.
