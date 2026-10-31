"""Replays the same Bot over past NSE data, one trading day at a time.

The bot only ever sees candles up to the simulated day. News and fundamentals
aren't available point-in-time for free, so backtests use charts only.
Note: an AI model may already "know" what happened after its training data
starts, so AI backtests look better than reality. Trust rules-only backtests
and the fake-money run more.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time

import pandas as pd

from .agents import Analysts, PortfolioManager
from .bot import Bot
from .broker.paper import PaperBroker
from .ledger import Ledger
from .scorecard import quarterly_results, survival_check


class HistoricalData:
    def __init__(self, frames: dict[str, pd.DataFrame]):
        self.frames = frames
        self.as_of: pd.Timestamp | None = None

    def candles(self, symbol: str) -> pd.DataFrame:
        df = self.frames[symbol]
        return df.loc[: self.as_of] if self.as_of is not None else df

    def headlines(self, symbol: str) -> list[dict]:
        return []

    def fundamentals(self, symbol: str) -> dict | None:
        return None


@dataclass
class BacktestResult:
    equity: pd.Series
    benchmark: pd.Series | None
    fills: pd.DataFrame
    quarters: list
    alive: bool
    survival_note: str

    def summary(self) -> dict:
        eq = self.equity
        dd = (eq / eq.cummax() - 1).min()
        out = {
            "start": str(eq.index[0].date()), "end": str(eq.index[-1].date()),
            "start_equity": round(eq.iloc[0], 2), "end_equity": round(eq.iloc[-1], 2),
            "total_return": round(eq.iloc[-1] / eq.iloc[0] - 1, 4),
            "max_drawdown": round(float(dd), 4),
            "trades": len(self.fills),
            "costs_paid": round(float(self.fills["costs"].sum()), 2) if len(self.fills) else 0.0,
            "alive": self.alive, "survival": self.survival_note,
        }
        if self.benchmark is not None and len(self.benchmark):
            b = self.benchmark.loc[eq.index[0]: eq.index[-1]]
            out["benchmark_return"] = round(float(b.iloc[-1] / b.iloc[0] - 1), 4)
        return out


def run_backtest(cfg: dict, frames: dict[str, pd.DataFrame], start: str, end: str | None = None,
                 every: int = 1, llm=None, benchmark: pd.DataFrame | None = None) -> BacktestResult:
    """`every` = decide every N trading days (stops are still checked daily)."""
    cfg = {**cfg, "watchlist": list(frames)}
    # The benchmark is visible to the bot (for the market-mood check) but never traded.
    data = HistoricalData({**frames, cfg["benchmark"]: benchmark} if benchmark is not None else frames)
    ledger = Ledger(":memory:")
    broker = PaperBroker(ledger, cfg["starting_capital"], cfg["costs"])
    clock_now = {"t": datetime.now()}
    bot = Bot(cfg, ledger, broker, data, Analysts(llm), PortfolioManager(llm), clock=lambda: clock_now["t"])

    days = sorted(set().union(*[df.index for df in frames.values()]))
    days = [d for d in days if d >= pd.Timestamp(start) and (end is None or d <= pd.Timestamp(end))]
    alive = True
    for i, day in enumerate(days):
        data.as_of = day
        clock_now["t"] = datetime.combine(day.date(), time(15, 0))
        if i % every == 0:
            rep = bot.run_cycle()
        else:
            rep = bot.run_stops_only()
        if not rep.alive:
            alive = False
            break

    curve = ledger.equity_curve()
    equity = pd.Series({pd.Timestamp(d): v for d, v in curve}).sort_index()
    fills = pd.read_sql_query("SELECT * FROM fills", ledger.db)
    sv = cfg["survival"]
    last_day = days[-1].date() if days else datetime.now().date()
    quarters = quarterly_results(curve, cfg["starting_capital"], last_day, sv["fiscal_year_start_month"])
    ok, note = survival_check(quarters, sv["min_quarter_return"], sv["max_consecutive_failed_quarters"])
    bench_close = benchmark["close"] if benchmark is not None else None
    return BacktestResult(equity, bench_close, fills, quarters, alive and ok, ledger.get("shutdown") or note)
