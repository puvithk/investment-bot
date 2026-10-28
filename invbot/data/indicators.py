"""Technical indicators and candlestick patterns, in plain pandas."""
from __future__ import annotations

import numpy as np
import pandas as pd


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    rs = gain / loss.replace(0, np.nan)
    return (100 - 100 / (1 + rs)).fillna(100)


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[pd.Series, pd.Series]:
    line = close.ewm(span=fast, adjust=False).mean() - close.ewm(span=slow, adjust=False).mean()
    return line, line.ewm(span=signal, adjust=False).mean()


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    prev_close = df["close"].shift()
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def candle_patterns(df: pd.DataFrame) -> list[str]:
    """Patterns on the most recent candle (and the one before it)."""
    if len(df) < 2:
        return []
    prev, cur = df.iloc[-2], df.iloc[-1]
    body = abs(cur.close - cur.open)
    rng = max(cur.high - cur.low, 1e-9)
    upper = cur.high - max(cur.close, cur.open)
    lower = min(cur.close, cur.open) - cur.low
    found = []
    if body / rng < 0.1:
        found.append("doji")
    if lower > 2 * body and upper < body:
        found.append("hammer")
    if upper > 2 * body and lower < body:
        found.append("shooting_star")
    if prev.close < prev.open and cur.close > cur.open and cur.close >= prev.open and cur.open <= prev.close:
        found.append("bullish_engulfing")
    if prev.close > prev.open and cur.close < cur.open and cur.open >= prev.close and cur.close <= prev.open:
        found.append("bearish_engulfing")
    return found


def snapshot(df: pd.DataFrame) -> dict:
    """Everything the analysts need about one stock's chart, as plain numbers."""
    close = df["close"]
    macd_line, macd_signal = macd(close)
    last = float(close.iloc[-1])

    def pct_change(days: int) -> float | None:
        return float(last / close.iloc[-days - 1] - 1) if len(close) > days else None

    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    return {
        "price": round(last, 2),
        "change_5d": _r(pct_change(5)),
        "change_20d": _r(pct_change(20)),
        "change_60d": _r(pct_change(60)),
        "rsi14": _r(rsi(close).iloc[-1]),
        "macd": _r(macd_line.iloc[-1]),
        "macd_signal": _r(macd_signal.iloc[-1]),
        "sma50": _r(sma50),
        "sma200": _r(sma200),
        "above_sma50": bool(last > sma50) if pd.notna(sma50) else None,
        "above_sma200": bool(last > sma200) if pd.notna(sma200) else None,
        "atr14_pct": _r(atr(df).iloc[-1] / last),
        "volume_vs_20d_avg": _r(df["volume"].iloc[-1] / df["volume"].rolling(20).mean().iloc[-1]),
        "high_52w": _r(df["high"].tail(252).max()),
        "low_52w": _r(df["low"].tail(252).min()),
        "patterns": candle_patterns(df),
    }


def technical_score(snap: dict) -> float:
    """Simple rules score in [-1, 1]. Used on its own when no AI model is set."""
    score = 0.0
    if snap.get("above_sma200"):
        score += 0.3
    elif snap.get("above_sma200") is False:
        score -= 0.3
    if snap.get("above_sma50"):
        score += 0.2
    elif snap.get("above_sma50") is False:
        score -= 0.2
    if snap["macd"] is not None and snap["macd_signal"] is not None:
        score += 0.2 if snap["macd"] > snap["macd_signal"] else -0.2
    rsi_v = snap.get("rsi14")
    if rsi_v is not None:
        if rsi_v > 75:
            score -= 0.2
        elif rsi_v < 30:
            score += 0.1
    pats = snap.get("patterns", [])
    if "bullish_engulfing" in pats or "hammer" in pats:
        score += 0.1
    if "bearish_engulfing" in pats or "shooting_star" in pats:
        score -= 0.1
    return max(-1.0, min(1.0, round(score, 3)))


def _r(x, nd: int = 4):
    if x is None or (isinstance(x, float) and np.isnan(x)) or pd.isna(x):
        return None
    return round(float(x), nd)
