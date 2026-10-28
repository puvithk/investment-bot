"""Daily OHLCV candles for NSE stocks.

Uses Yahoo Finance (free, no key) for research and backtests. Live order
prices come from the broker through OpenAlgo, not from here.
"""
from __future__ import annotations

import pandas as pd


def yahoo_symbol(symbol: str) -> str:
    if symbol.startswith("^") or "." in symbol:
        return symbol
    return f"{symbol}.NS"


def fetch_candles(symbol: str, period: str = "1y", interval: str = "1d") -> pd.DataFrame:
    import yfinance as yf

    df = yf.download(
        yahoo_symbol(symbol),
        period=period,
        interval=interval,
        auto_adjust=True,
        progress=False,
        multi_level_index=False,
    )
    if df is None or df.empty:
        raise ValueError(f"No candles returned for {symbol}")
    df = df.rename(columns=str.lower)[["open", "high", "low", "close", "volume"]]
    return df.dropna()


def fetch_fundamentals(symbol: str) -> dict:
    """A few headline numbers. Missing values come back as None."""
    import yfinance as yf

    info = yf.Ticker(yahoo_symbol(symbol)).info or {}
    keys = {
        "name": "longName",
        "sector": "sector",
        "market_cap": "marketCap",
        "pe": "trailingPE",
        "forward_pe": "forwardPE",
        "pb": "priceToBook",
        "roe": "returnOnEquity",
        "debt_to_equity": "debtToEquity",
        "profit_margin": "profitMargins",
        "revenue_growth": "revenueGrowth",
        "dividend_yield": "dividendYield",
    }
    return {k: info.get(v) for k, v in keys.items()}
