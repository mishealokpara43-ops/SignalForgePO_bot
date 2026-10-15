# SignalForgePO Telegram bot

Python Telegram bot with third-party forex candle data, indicator analysis, and a historical directional backtest.

## Commands
- `/start`
- `/pairs`
- `/signal EURUSD`
- `/signal EURUSD 5m`
- `/backtest EURUSD`
- `/backtest EURUSD 15m`
- `/help`

## Important data limitations
- Market candles are fetched through `yfinance` from Yahoo Finance. Availability, freshness, and intraday history limits are controlled by the provider.
- The feed is not Pocket Option's feed and does not provide OTC quotes. The bot must not label these as exact Pocket Option prices.
- The directional result is experimental and is not a guaranteed prediction or binary-options win-rate estimate.
- No automatic trading is performed.

## Deploy
1. Create the bot in Telegram with `@BotFather` and keep the token private.
2. Upload `bot.py`, `requirements.txt`, and `render.yaml` to a GitHub repository.
3. In Render, create a new **Background Worker** from that repository.
4. Add environment variable `BOT_TOKEN` and paste your BotFather token into the private value field.
5. Deploy and inspect logs for `SignalForgePO starting in long-polling mode`.
6. Open your Telegram bot and send `/start`.

Free plan availability and worker limits may change. If the free worker plan is unavailable, choose a currently available host plan; don't paste your token into public code or chat.

## Local run
Python 3.11+ recommended.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
```

Set `BOT_TOKEN` as an environment variable and run `python bot.py`.

Windows PowerShell:
```powershell
$env:BOT_TOKEN="paste-your-token-here"
python bot.py
```

macOS/Linux:
```bash
export BOT_TOKEN="paste-your-token-here"
python bot.py
```
