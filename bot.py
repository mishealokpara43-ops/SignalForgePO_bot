import os
import logging
from datetime import datetime, timezone

import pandas as pd
import yfinance as yf
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("SignalForgePO")

TOKEN = os.environ.get("BOT_TOKEN", "").strip()

# Yahoo Finance symbols. These are third-party feeds, not Pocket Option/OTC quotes.
PAIRS = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "JPY=X",
    "AUDUSD": "AUDUSD=X",
    "NZDUSD": "NZDUSD=X",
    "USDCAD": "CAD=X",
    "USDCHF": "CHF=X",
    "EURGBP": "EURGBP=X",
    "EURJPY": "EURJPY=X",
    "GBPJPY": "GBPJPY=X",
}

TIMEFRAMES = {
    "1m": ("1mo", "1m"),
    "5m": ("1mo", "5m"),
    "15m": ("3mo", "15m"),
    "1h": ("1y", "1h"),
}

DISCLAIMER = (
    "Data is supplied by Yahoo Finance and may be delayed, incomplete, or different "
    "from Pocket Option prices. OTC prices are not available from this feed. "
    "Signals are experimental and do not guarantee outcomes. No trades are placed."
)


def candles(pair: str, timeframe: str = "5m") -> pd.DataFrame:
    pair = pair.upper().strip()
    timeframe = timeframe.lower().strip()
    if pair not in PAIRS:
        raise ValueError(f"Unknown pair {pair}. Use /pairs.")
    if timeframe not in TIMEFRAMES:
        raise ValueError("Timeframe must be 1m, 5m, 15m, or 1h.")
    period, interval = TIMEFRAMES[timeframe]
    df = yf.download(
        PAIRS[pair],
        period=period,
        interval=interval,
        auto_adjust=False,
        progress=False,
        threads=False,
    )
    if df is None or df.empty:
        raise RuntimeError(
            "No candles returned by the data provider for this pair/timeframe. "
            "Try another pair or timeframe later."
        )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    needed = ["Open", "High", "Low", "Close"]
    if not set(needed).issubset(df.columns):
        raise RuntimeError("Unexpected data format from the provider.")
    df = df.dropna(subset=needed).copy()
    df.index = pd.to_datetime(df.index, utc=True)
    return df


def with_indicators(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    close = out["Close"].astype(float)
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    out["RSI"] = 100 - 100 / (1 + rs)
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    out["MACD"] = ema12 - ema26
    out["MACD_SIGNAL"] = out["MACD"].ewm(span=9, adjust=False).mean()
    out["EMA20"] = close.ewm(span=20, adjust=False).mean()
    out["EMA50"] = close.ewm(span=50, adjust=False).mean()
    out["ATR"] = (out["High"] - out["Low"]).rolling(14).mean()
    return out


def price_fmt(value: float) -> str:
    return f"{value:.3f}" if abs(value) >= 100 else (f"{value:.5f}" if abs(value) >= 1 else f"{value:.6f}")


def evaluate_signal(df: pd.DataFrame) -> dict:
    calc = with_indicators(df).dropna()
    if len(calc) < 3:
        return {"direction": "NO SIGNAL", "score": 0, "reasons": ["Not enough candles to calculate indicators."]}
    row = calc.iloc[-1]
    previous = calc.iloc[-2]
    score_up = 0
    score_down = 0
    reasons = []

    # Trend confirmation
    if row["Close"] > row["EMA20"] > row["EMA50"]:
        score_up += 1
        reasons.append("Price is above rising trend averages.")
    elif row["Close"] < row["EMA20"] < row["EMA50"]:
        score_down += 1
        reasons.append("Price is below falling trend averages.")

    # MACD momentum
    if row["MACD"] > row["MACD_SIGNAL"] and previous["MACD"] <= previous["MACD_SIGNAL"]:
        score_up += 2
        reasons.append("MACD crossed above its signal line.")
    elif row["MACD"] < row["MACD_SIGNAL"] and previous["MACD"] >= previous["MACD_SIGNAL"]:
        score_down += 2
        reasons.append("MACD crossed below its signal line.")
    elif row["MACD"] > row["MACD_SIGNAL"]:
        score_up += 1
        reasons.append("MACD is above its signal line.")
    elif row["MACD"] < row["MACD_SIGNAL"]:
        score_down += 1
        reasons.append("MACD is below its signal line.")

    # RSI momentum; avoid chasing extreme readings
    rsi = float(row["RSI"])
    if 50 <= rsi < 68:
        score_up += 1
        reasons.append(f"RSI momentum is positive ({rsi:.1f}).")
    elif 32 < rsi < 50:
        score_down += 1
        reasons.append(f"RSI momentum is negative ({rsi:.1f}).")
    elif rsi >= 70 or rsi <= 30:
        reasons.append(f"RSI is extreme ({rsi:.1f}); this can indicate an extended move.")

    # Require trend and momentum agreement, plus minimum score.
    direction = "NO SIGNAL"
    score = max(score_up, score_down)
    if score_up >= 2 and score_up > score_down and row["Close"] > row["EMA20"]:
        direction = "UPWARD BIAS"
    elif score_down >= 2 and score_down > score_up and row["Close"] < row["EMA20"]:
        direction = "DOWNWARD BIAS"
    else:
        reasons.append("Confirmation is mixed; the bot is withholding a directional bias.")

    return {
        "direction": direction,
        "score": score,
        "score_up": score_up,
        "score_down": score_down,
        "rsi": rsi,
        "close": float(row["Close"]),
        "ema20": float(row["EMA20"]),
        "ema50": float(row["EMA50"]),
        "macd": float(row["MACD"]),
        "macd_signal": float(row["MACD_SIGNAL"]),
        "timestamp": calc.index[-1].strftime("%Y-%m-%d %H:%M UTC"),
        "reasons": reasons,
    }


def backtest(pair: str, timeframe: str = "15m") -> dict:
    raw = candles(pair, timeframe)
    df = with_indicators(raw).dropna().copy()
    if len(df) < 100:
        raise RuntimeError("Not enough historical candles returned for this test.")

    # A signal formed at candle t is evaluated against the next candle's close.
    # This is a directional price-change test, not a binary-options payout model.
    df["position"] = 0
    up = (df["Close"] > df["EMA20"]) & (df["EMA20"] > df["EMA50"]) & (df["MACD"] > df["MACD_SIGNAL"]) & (df["RSI"] >= 50) & (df["RSI"] < 68)
    down = (df["Close"] < df["EMA20"]) & (df["EMA20"] < df["EMA50"]) & (df["MACD"] < df["MACD_SIGNAL"]) & (df["RSI"] > 32) & (df["RSI"] < 50)
    df.loc[up, "position"] = 1
    df.loc[down, "position"] = -1
    df["next_return"] = df["Close"].shift(-1) / df["Close"] - 1
    trades = df[(df["position"] != 0) & df["next_return"].notna()].copy()
    if trades.empty:
        return {"count": 0, "win_rate": None, "model_return": 0.0, "start": str(df.index[0].date()), "end": str(df.index[-1].date())}
    trades["strategy_return"] = trades["position"] * trades["next_return"]
    wins = int((trades["strategy_return"] > 0).sum())
    total = len(trades)
    cumulative = ((1 + trades["strategy_return"]).prod() - 1) * 100
    return {
        "count": total,
        "wins": wins,
        "losses": total - wins,
        "win_rate": wins / total * 100,
        "model_return": cumulative,
        "start": str(df.index[0].date()),
        "end": str(df.index[-1].date()),
    }


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "⚙️ *SignalForgePO — Telegram Bot*\\n\\n"
        "Commands:\\n"
        "/pairs — available forex pairs\\n"
        "/signal EURUSD — analyze latest candles\\n"
        "/signal EURUSD 5m — choose timeframe\\n"
        "/backtest EURUSD — historical study\\n"
        "/backtest EURUSD 15m — choose timeframe\\n"
        "/help — help\\n\\n"
        "This bot provides experimental directional market analysis only. "
        "It does not execute trades.\\n\\n"
        + DISCLAIMER
    )
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def pairs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = "*Supported data-provider symbols*\\n" + "\\n".join(f"• `{p}`" for p in PAIRS)
    text += "\\n\\nThese are not guaranteed to match Pocket Option quotes. OTC pairs are not included."
    await update.effective_message.reply_text(text, parse_mode="Markdown")


async def signal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = context.args[0].upper() if context.args else "EURUSD"
    timeframe = context.args[1].lower() if len(context.args) > 1 else "5m"
    try:
        raw = candles(pair, timeframe)
        result = evaluate_signal(raw)
        reasons = "\\n".join(f"• {r}" for r in result.get("reasons", []))
        msg = (
            f"📊 *{pair} — {timeframe} analysis*\\n"
            f"Candle time: `{result.get('timestamp', 'unknown')}`\\n"
            f"Provider close: `{price_fmt(result.get('close', 0.0))}`\\n"
            f"Result: *{result['direction']}*\\n"
            f"Indicator score: `{result.get('score', 0)}` (not a probability)\\n"
        )
        if "rsi" in result:
            msg += (
                f"RSI(14): `{result['rsi']:.1f}`\\n"
                f"EMA20: `{price_fmt(result['ema20'])}`\\n"
                f"EMA50: `{price_fmt(result['ema50'])}`\\n"
                f"MACD: `{result['macd']:.6f}` / signal `{result['macd_signal']:.6f}`\\n"
            )
        msg += f"\\n*Why:*\\n{reasons}\\n\\n{DISCLAIMER}"
        await update.effective_message.reply_text(msg, parse_mode="Markdown")
    except Exception as exc:
        log.exception("Signal analysis failed")
        await update.effective_message.reply_text(f"Unable to analyze {pair}: {exc}")


async def backtest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    pair = context.args[0].upper() if context.args else "EURUSD"
    timeframe = context.args[1].lower() if len(context.args) > 1 else "15m"
    try:
        result = backtest(pair, timeframe)
        if result["count"] == 0:
            text = f"🧪 *Backtest: {pair} {timeframe}*\\nNo qualifying observations in the returned data."
        else:
            text = (
                f"🧪 *Backtest: {pair} {timeframe}*\\n"
                f"Data dates: `{result['start']}` to `{result['end']}`\\n"
                f"Observations: `{result['count']}`\\n"
                f"Positive next-candle outcomes: `{result['wins']}`\\n"
                f"Negative/flat outcomes: `{result['losses']}`\\n"
                f"Positive outcome ratio: `{result['win_rate']:.1f}%`\\n"
                f"Compounded model return before costs: `{result['model_return']:.2f}%`\\n\\n"
                "This is a candle-to-candle directional study, not a Pocket Option binary-options win rate. "
                "It excludes payout structure, spread, slippage, latency, and execution effects."
            )
        await update.effective_message.reply_text(text, parse_mode="Markdown")
    except Exception as exc:
        log.exception("Backtest failed")
        await update.effective_message.reply_text(f"Unable to backtest {pair}: {exc}")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await start(update, context)


def main():
    if not TOKEN:
        raise SystemExit("Missing BOT_TOKEN environment variable. Set it in your hosting dashboard.")
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("pairs", pairs))
    app.add_handler(CommandHandler("signal", signal))
    app.add_handler(CommandHandler("backtest", backtest_cmd))
    log.info("SignalForgePO starting in long-polling mode.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
