"""One trading cycle: check survival, guard stops, analyse, decide, risk-check, act, log."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Callable, Protocol

import pandas as pd

from .agents import Analysts, PortfolioManager, Proposal
from .data.indicators import snapshot
from .ledger import Ledger, now_iso
from .risk import Order, PortfolioState, RiskManager
from .scorecard import quarterly_results, survival_check, fiscal_quarter

log = logging.getLogger("invbot")


class DataSource(Protocol):
    def candles(self, symbol: str) -> pd.DataFrame: ...
    def headlines(self, symbol: str) -> list[dict]: ...
    def fundamentals(self, symbol: str) -> dict | None: ...


class LiveData:
    """Free internet sources. Swap for a historical source in backtests."""

    def candles(self, symbol: str) -> pd.DataFrame:
        from .data.market import fetch_candles
        return fetch_candles(symbol, period="2y")

    def headlines(self, symbol: str) -> list[dict]:
        from .data.news import fetch_headlines
        try:
            return fetch_headlines(symbol)
        except Exception as e:  # news is optional evidence
            log.warning("news failed for %s: %s", symbol, e)
            return []

    def fundamentals(self, symbol: str) -> dict | None:
        from .data.market import fetch_fundamentals
        try:
            return fetch_fundamentals(symbol)
        except Exception as e:
            log.warning("fundamentals failed for %s: %s", symbol, e)
            return None


@dataclass
class CycleReport:
    day: date
    alive: bool
    equity: float
    cash: float
    actions: list[str]
    notes: list[str]


class Bot:
    def __init__(self, cfg: dict, ledger: Ledger, broker, data: DataSource,
                 analysts: Analysts, pm: PortfolioManager,
                 clock: Callable[[], datetime] = datetime.now):
        self.cfg = cfg
        self.ledger = ledger
        self.broker = broker
        self.data = data
        self.analysts = analysts
        self.pm = pm
        self.risk = RiskManager(cfg["risk"])
        self.clock = clock

    # -- state -------------------------------------------------------------
    def _prices(self, symbols, candles: dict[str, pd.DataFrame]) -> dict[str, float]:
        out = {}
        for s in symbols:
            df = candles.get(s)
            if df is None:
                try:
                    df = candles[s] = self.data.candles(s)
                except Exception as e:
                    log.warning("no candles for %s: %s", s, e)
                    continue
            out[s] = float(df["close"].iloc[-1])
        return out

    def _equity(self, prices: dict[str, float]) -> float:
        cash = self.broker.cash()
        value = 0.0
        for s, pos in self.ledger.positions().items():
            value += pos["qty"] * prices.get(s, pos["avg_price"])
        return round(cash + value, 2)

    def _quarter_start_equity(self, today: date, equity: float) -> float:
        fy, q = fiscal_quarter(today, self.cfg["survival"]["fiscal_year_start_month"])
        key = f"quarter_start:{fy}Q{q}"
        start = self.ledger.get(key)
        if start is None:
            curve = self.ledger.equity_curve()
            start = curve[-1][1] if curve else self.cfg["starting_capital"]
            self.ledger.set(key, start)
        return float(start)

    # -- cycle -------------------------------------------------------------
    def run_cycle(self) -> CycleReport:
        now = self.clock()
        today, ts = now.date(), now_iso(now)
        actions: list[str] = []
        notes: list[str] = []

        if self.ledger.get("shutdown"):
            return CycleReport(today, False, 0.0, self.broker.cash(), [], [self.ledger.get("shutdown")])

        sv = self.cfg["survival"]
        alive, why = survival_check(
            quarterly_results(self.ledger.equity_curve(), self.cfg["starting_capital"], today,
                              sv["fiscal_year_start_month"]),
            sv["min_quarter_return"], sv["max_consecutive_failed_quarters"],
        )
        if not alive:
            notes.append(why)
            self._liquidate(ts, "survival rule: " + why, actions)
            self.ledger.set("shutdown", f"{ts}: {why}")
            return CycleReport(today, False, self.broker.cash(), self.broker.cash(), actions, notes)

        watch = list(dict.fromkeys(self.cfg["watchlist"] + list(self.ledger.positions())))
        candles: dict[str, pd.DataFrame] = {}
        prices = self._prices(watch, candles)

        # 1. stop-losses first, before any new idea
        self._run_stops(prices, ts, actions)

        # 2. analyse each stock and act through the risk rules
        for s in watch:
            if s not in prices:
                continue
            try:
                proposal, snap = self._analyse(s, candles[s])
            except Exception as e:
                notes.append(f"{s}: analysis failed ({e}); skipped")
                continue
            equity = self._equity(prices)
            state = PortfolioState(
                cash=self.broker.cash(), equity=equity,
                positions={k: v["qty"] for k, v in self.ledger.positions().items()},
                quarter_start_equity=self._quarter_start_equity(today, equity),
                trades_today=self.ledger.trades_on(today),
            )
            verdict = self.risk.review(proposal, prices[s], state)
            self.ledger.log_decision(ts, proposal.to_dict(), verdict.approved, verdict.note, snap)
            if verdict.approved and verdict.order:
                res = self.broker.place(verdict.order, ts)
                o = verdict.order
                actions.append(f"{o.side} {o.qty} {s} @ {o.price} -> {res['status']}")
            elif proposal.action != "HOLD":
                notes.append(f"{s}: {proposal.action} {verdict.note}")

        equity = self._equity(prices)
        self.ledger.record_equity(today, self.broker.cash(), equity)
        return CycleReport(today, True, equity, self.broker.cash(), actions, notes)

    def run_stops_only(self) -> CycleReport:
        """Cheap daily check between full decision cycles: stops and equity only."""
        now = self.clock()
        if self.ledger.get("shutdown"):
            return CycleReport(now.date(), False, 0.0, self.broker.cash(), [], [self.ledger.get("shutdown")])
        actions: list[str] = []
        held = list(self.ledger.positions())
        prices = self._prices(held, {})
        self._run_stops(prices, now_iso(now), actions)
        equity = self._equity(prices)
        self.ledger.record_equity(now.date(), self.broker.cash(), equity)
        return CycleReport(now.date(), True, equity, self.broker.cash(), actions, [])

    def _run_stops(self, prices: dict[str, float], ts: str, actions: list[str]) -> None:
        stops = {s: p["stop_loss"] for s, p in self.ledger.positions().items() if p["stop_loss"]}
        for s in RiskManager.stop_hits(stops, prices):
            qty = self.ledger.positions()[s]["qty"]
            res = self.broker.place(Order(s, "SELL", qty, prices[s], None, "stop-loss hit"), ts)
            actions.append(f"SELL {qty} {s} @ {prices[s]} (stop-loss) -> {res['status']}")

    def _analyse(self, symbol: str, df: pd.DataFrame) -> tuple[Proposal, dict]:
        snap = snapshot(df)
        views = [
            self.analysts.technical(snap),
            self.analysts.news(symbol, self.data.headlines(symbol)),
            self.analysts.fundamentals(symbol, self.data.fundamentals(symbol)),
        ]
        held = self.ledger.positions().get(symbol)
        return self.pm.decide(symbol, views, held["qty"] if held else 0, snap), snap

    def _liquidate(self, ts: str, reason: str, actions: list[str]) -> None:
        for s, pos in self.ledger.positions().items():
            try:
                price = float(self.data.candles(s)["close"].iloc[-1])
            except Exception:
                price = pos["avg_price"]
            res = self.broker.place(Order(s, "SELL", pos["qty"], price, None, reason), ts)
            actions.append(f"SELL {pos['qty']} {s} @ {price} (shutdown) -> {res['status']}")
