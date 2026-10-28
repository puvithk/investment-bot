"""Hard risk rules. The AI proposes; these rules can shrink or veto.

Nothing here calls a model, so the limits hold even if the model misbehaves.
"""
from __future__ import annotations

from dataclasses import dataclass

from .agents import Proposal


@dataclass
class Order:
    symbol: str
    side: str              # BUY | SELL
    qty: int
    price: float           # reference price used for sizing
    stop_loss: float | None
    reason: str


@dataclass
class RiskVerdict:
    approved: bool
    order: Order | None
    note: str


@dataclass
class PortfolioState:
    cash: float
    equity: float
    positions: dict[str, int]          # symbol -> qty
    quarter_start_equity: float
    trades_today: int


class RiskManager:
    def __init__(self, cfg: dict):
        self.cfg = cfg

    def quarter_drawdown(self, state: PortfolioState) -> float:
        if state.quarter_start_equity <= 0:
            return 0.0
        return max(0.0, 1 - state.equity / state.quarter_start_equity)

    def review(self, p: Proposal, price: float, state: PortfolioState) -> RiskVerdict:
        r = self.cfg
        held = state.positions.get(p.symbol, 0)

        if p.action == "HOLD":
            return RiskVerdict(False, None, "hold")

        if p.action == "SELL":
            # Selling to cut risk is always allowed, whatever the confidence.
            if held <= 0:
                return RiskVerdict(False, None, "veto: nothing to sell")
            return RiskVerdict(True, Order(p.symbol, "SELL", held, price, None, p.reasoning), "sell approved")

        # BUY checks
        if state.trades_today >= r["max_trades_per_day"]:
            return RiskVerdict(False, None, "veto: daily trade limit reached")
        if p.confidence < r["min_confidence"]:
            return RiskVerdict(False, None, f"veto: confidence {p.confidence:.2f} below {r['min_confidence']}")
        dd = self.quarter_drawdown(state)
        if dd >= r["max_quarter_drawdown_pct"]:
            return RiskVerdict(False, None, f"veto: quarter drawdown {dd:.1%}, new buys paused")
        if held > 0:
            return RiskVerdict(False, None, "veto: already holding, no averaging up or down")
        open_positions = sum(1 for q in state.positions.values() if q > 0)
        if open_positions >= r["max_open_positions"]:
            return RiskVerdict(False, None, "veto: max open positions reached")

        size_pct = min(p.size_pct, r["max_position_pct"])
        budget = state.equity * size_pct
        spendable = state.cash - state.equity * r["min_cash_reserve_pct"]
        budget = min(budget, spendable)
        qty = int(budget // price) if price > 0 else 0
        if qty <= 0:
            return RiskVerdict(False, None, "veto: not enough free cash after reserve")

        stop_pct = p.stop_loss_pct or r["default_stop_loss_pct"]
        stop_pct = min(max(stop_pct, 0.01), r["max_stop_loss_pct"])
        stop = round(price * (1 - stop_pct), 2)
        note = "buy approved" + (f" (size cut to {size_pct:.0%})" if size_pct < p.size_pct else "")
        return RiskVerdict(True, Order(p.symbol, "BUY", qty, price, stop, p.reasoning), note)

    @staticmethod
    def stop_hits(stops: dict[str, float], prices: dict[str, float]) -> list[str]:
        """Symbols whose latest price is at or below their stop."""
        return [s for s, stop in stops.items() if s in prices and prices[s] <= stop]
