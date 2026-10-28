"""Fake-money broker. Fills at the given price plus slippage and real Indian charges."""
from __future__ import annotations

from ..costs import trade_cost
from ..ledger import Ledger
from ..risk import Order


class PaperBroker:
    name = "paper"

    def __init__(self, ledger: Ledger, starting_capital: float, cost_cfg: dict, slippage_pct: float = 0.0005):
        self.ledger = ledger
        self.costs = cost_cfg
        self.slippage = slippage_pct
        if ledger.get("cash") is None:
            ledger.set("cash", starting_capital)

    def cash(self) -> float:
        return float(self.ledger.get("cash"))

    def holdings(self) -> dict[str, int]:
        return {s: r["qty"] for s, r in self.ledger.positions().items()}

    def place(self, order: Order, ts: str) -> dict:
        positions = self.ledger.positions()
        cash = self.cash()
        if order.side == "BUY":
            fill = round(order.price * (1 + self.slippage), 2)
            value = fill * order.qty
            cost = trade_cost("BUY", value, self.costs)
            if value + cost > cash:
                return {"status": "rejected", "reason": "insufficient cash"}
            self.ledger.set("cash", round(cash - value - cost, 2))
            self.ledger.upsert_position(order.symbol, order.qty, fill, order.stop_loss, ts)
            pnl = -cost
        else:
            pos = positions.get(order.symbol)
            if not pos:
                return {"status": "rejected", "reason": "no position"}
            qty = min(order.qty, pos["qty"])
            fill = round(order.price * (1 - self.slippage), 2)
            value = fill * qty
            cost = trade_cost("SELL", value, self.costs)
            self.ledger.set("cash", round(cash + value - cost, 2))
            remaining = pos["qty"] - qty
            self.ledger.upsert_position(order.symbol, remaining, pos["avg_price"], pos["stop_loss"], pos["opened_at"])
            pnl = (fill - pos["avg_price"]) * qty - cost
            order.qty = qty
        self.ledger.log_fill(ts, order.symbol, order.side, order.qty, fill, cost, round(pnl, 2), "paper", order.reason)
        return {"status": "filled", "price": fill, "costs": cost}
