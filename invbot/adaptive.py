"""Lets the bot size itself to the money it has and to the market's mood.

- Money: works out how many positions the capital can carry without charges
  eating the profit, and how big each one should be.
- Market: reads the Nifty trend and decides how much of the money to put to work.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .costs import trade_cost

REGIME_EXPOSURE = {"risk_on": 1.0, "neutral": 0.5, "risk_off": 0.0}


@dataclass
class Plan:
    regime: str
    exposure: float          # share of the usual position count allowed in this market
    max_positions: int
    position_pct: float      # target fraction of equity per position
    min_trade_value: float   # smallest trade worth making after charges

    def describe(self) -> str:
        return (f"market {self.regime}, up to {self.max_positions} "
                f"positions of ~{self.position_pct:.0%} each, min trade Rs {self.min_trade_value:,.0f}")


def market_regime(bench: pd.DataFrame | None) -> str:
    """risk_on: Nifty in an uptrend. risk_off: below its 200-day average and falling. Else neutral."""
    if bench is None or len(bench) < 200:
        return "neutral"
    close = bench["close"]
    last = close.iloc[-1]
    sma50, sma200 = close.rolling(50).mean().iloc[-1], close.rolling(200).mean().iloc[-1]
    change_20d = last / close.iloc[-21] - 1
    if last > sma200 and sma50 > sma200:
        return "risk_on"
    if last < sma200 and change_20d < -0.03:
        return "risk_off"
    return "neutral"


def min_trade_value(cost_cfg: dict, max_cost_pct: float) -> float:
    """Smallest trade whose round-trip charges stay under max_cost_pct."""
    for value in range(500, 1_000_001, 250):
        rt = trade_cost("BUY", value, cost_cfg) + trade_cost("SELL", value, cost_cfg)
        if rt / value <= max_cost_pct:
            return float(value)
    return 1_000_000.0


def make_plan(equity: float, bench: pd.DataFrame | None, risk_cfg: dict, cost_cfg: dict,
              adaptive_cfg: dict) -> Plan:
    regime = market_regime(bench)
    exposure = REGIME_EXPOSURE[regime]
    investable = equity * (1 - risk_cfg["min_cash_reserve_pct"])
    floor = min_trade_value(cost_cfg, adaptive_cfg.get("max_round_trip_cost_pct", 0.01))
    by_money = int(investable // floor)
    max_positions = max(1, min(risk_cfg["max_open_positions"], by_money))
    # Small accounts concentrate; big ones spread out, never above the hard cap per stock.
    position_pct = min(adaptive_cfg.get("max_position_pct_small", 0.45),
                       (1 - risk_cfg["min_cash_reserve_pct"]) / max_positions)
    if equity >= adaptive_cfg.get("small_account_below", 50_000):
        position_pct = min(position_pct, risk_cfg["max_position_pct"])
    # A cautious market means fewer positions, not smaller ones, so trades stay above the cost floor.
    if exposure == 0:
        max_positions = 0
    else:
        max_positions = max(1, round(max_positions * exposure))
    return Plan(regime, exposure, max_positions, round(position_pct, 4), floor)


def apply_plan(risk_cfg: dict, plan: Plan) -> dict:
    """Risk settings for this cycle. Never loosens the cash reserve or stop-loss limits."""
    out = dict(risk_cfg)
    out["max_open_positions"] = plan.max_positions
    out["max_position_pct"] = plan.position_pct
    return out


def affordable(price: float, equity: float, plan: Plan) -> bool:
    budget = equity * plan.position_pct
    return plan.max_positions > 0 and price <= budget and budget >= plan.min_trade_value
