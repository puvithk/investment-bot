from datetime import datetime

from invbot.adaptive import affordable, make_plan, market_regime, min_trade_value

from .conftest import make_candles
from .test_bot import make_bot


def test_regimes():
    assert market_regime(make_candles(drift=0.003)) == "risk_on"
    assert market_regime(make_candles(drift=-0.006)) == "risk_off"
    assert market_regime(None) == "neutral"


def test_cost_floor_keeps_charges_under_1pct(cfg):
    floor = min_trade_value(cfg["costs"], 0.01)
    assert 1500 <= floor <= 3000          # the Rs 16 DP charge dominates small trades


def test_small_account_concentrates(cfg):
    up = make_candles(drift=0.003)
    p = make_plan(5000, up, cfg["risk"], cfg["costs"], cfg["adaptive"])
    assert p.max_positions == 2 and p.position_pct == 0.45
    assert affordable(280, 5000, p)        # NIFTYBEES-priced
    assert not affordable(3000, 5000, p)   # TCS-priced: can't buy one share sensibly


def test_large_account_spreads_out_and_respects_cap(cfg):
    p = make_plan(1_000_000, make_candles(drift=0.003), cfg["risk"], cfg["costs"], cfg["adaptive"])
    assert p.max_positions == 6 and p.position_pct == 0.15


def test_falling_market_blocks_new_buys(cfg):
    p = make_plan(100_000, make_candles(drift=-0.006), cfg["risk"], cfg["costs"], cfg["adaptive"])
    assert p.regime == "risk_off" and p.max_positions == 0
    assert not affordable(100, 100_000, p)


def test_bot_with_5000_buys_only_what_it_can_afford(cfg):
    cfg["starting_capital"] = 5000
    cfg["benchmark"] = "NIFTY"
    frames = {"TCS": make_candles(start=3000, drift=0.003),
              "NIFTYBEES": make_candles(start=280, drift=0.003, seed=4)}
    bot = make_bot(cfg, frames, {"TCS": ("BUY", 0.9, 0.1, None), "NIFTYBEES": ("BUY", 0.9, 0.1, None)})
    bot.data.frames["NIFTY"] = make_candles(drift=0.003, seed=5)
    rep = bot.run_cycle()
    assert any("plan: market risk_on" in n for n in rep.notes)
    assert len(rep.actions) == 1 and "NIFTYBEES" in rep.actions[0]
    pos = bot.ledger.positions()["NIFTYBEES"]
    assert pos["qty"] * pos["avg_price"] > 2000   # a meaningful position, not Rs 750
