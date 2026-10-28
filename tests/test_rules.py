from datetime import date

from invbot.agents import Proposal
from invbot.costs import trade_cost
from invbot.data.indicators import candle_patterns, rsi, snapshot, technical_score
from invbot.llm import parse_json
from invbot.risk import PortfolioState, RiskManager
from invbot.scorecard import fiscal_quarter, quarterly_results, survival_check

from .conftest import make_candles


def state(**kw):
    base = dict(cash=100_000, equity=100_000, positions={}, quarter_start_equity=100_000, trades_today=0)
    base.update(kw)
    return PortfolioState(**base)


def buy(conf=0.8, size=0.10, stop=None, symbol="TCS"):
    return Proposal(symbol, "BUY", conf, size, stop, "test")


def test_costs_buy_and_sell_delivery(cfg):
    c = cfg["costs"]
    # 1 lakh delivery buy: STT 100 + stamp 15 + exchange ~2.97 + sebi 0.1 + GST ~0.55
    assert 118 < trade_cost("BUY", 100_000, c) < 120
    # sell adds DP charge, no stamp duty
    assert 118 < trade_cost("SELL", 100_000, c) < 121


def test_risk_caps_position_size(cfg):
    v = RiskManager(cfg["risk"]).review(buy(size=0.50), 1000, state())
    assert v.approved and v.order.qty == 15          # 15% of 1L at Rs 1000
    assert "size cut" in v.note


def test_risk_keeps_cash_reserve(cfg):
    v = RiskManager(cfg["risk"]).review(buy(size=0.15), 1000, state(cash=12_000))
    assert v.approved and v.order.qty == 2            # 12k - 10k reserve


def test_risk_vetoes(cfg):
    rm = RiskManager(cfg["risk"])
    assert "confidence" in rm.review(buy(conf=0.3), 1000, state()).note
    assert "drawdown" in rm.review(buy(), 1000, state(equity=90_000)).note
    assert "already holding" in rm.review(buy(), 1000, state(positions={"TCS": 5})).note
    assert "daily" in rm.review(buy(), 1000, state(trades_today=4)).note
    full = {f"S{i}": 1 for i in range(6)}
    assert "max open" in rm.review(buy(), 1000, state(positions=full)).note
    assert "nothing to sell" in rm.review(Proposal("TCS", "SELL", 0.9, 0, None, ""), 1000, state()).note


def test_stop_loss_clipped_to_max(cfg):
    v = RiskManager(cfg["risk"]).review(buy(stop=0.5), 1000, state())
    assert v.order.stop_loss == 900.0                 # clipped to 10%


def test_sell_allowed_even_with_low_confidence(cfg):
    v = RiskManager(cfg["risk"]).review(Proposal("TCS", "SELL", 0.1, 0, None, ""), 1000, state(positions={"TCS": 7}))
    assert v.approved and v.order.qty == 7


def test_indicators_on_uptrend():
    df = make_candles(drift=0.004)
    snap = snapshot(df)
    assert snap["above_sma200"] is True
    assert 0 <= snap["rsi14"] <= 100
    assert technical_score(snap) > 0


def test_rsi_bounds():
    df = make_candles()
    r = rsi(df["close"]).dropna()
    assert r.between(0, 100).all()


def test_bullish_engulfing_detected():
    df = make_candles(n=5)
    df.iloc[-2, df.columns.get_loc("open")] = 105
    df.iloc[-2, df.columns.get_loc("close")] = 100
    df.iloc[-1, df.columns.get_loc("open")] = 99
    df.iloc[-1, df.columns.get_loc("close")] = 107
    df.iloc[-1, df.columns.get_loc("high")] = 108
    df.iloc[-1, df.columns.get_loc("low")] = 98
    assert "bullish_engulfing" in candle_patterns(df)


def test_fiscal_quarters_indian_fy():
    assert fiscal_quarter(date(2026, 4, 1)) == (2027, 1)
    assert fiscal_quarter(date(2026, 10, 28)) == (2027, 3)
    assert fiscal_quarter(date(2027, 3, 31)) == (2027, 4)


def test_survival_rule_two_losing_quarters():
    curve = [(date(2026, 6, 30), 105_000), (date(2026, 9, 30), 101_000), (date(2026, 12, 31), 99_000)]
    res = quarterly_results(curve, 100_000, today=date(2027, 1, 5))
    assert [round(r.ret, 3) for r in res] == [0.05, -0.038, -0.020]
    alive, why = survival_check(res, 0.0, 2)
    assert not alive and "2 failed" in why


def test_in_progress_quarter_does_not_count():
    curve = [(date(2026, 6, 30), 95_000), (date(2026, 8, 1), 90_000)]
    res = quarterly_results(curve, 100_000, today=date(2026, 8, 2))
    assert res[-1].complete is False
    assert survival_check(res, 0.0, 2)[0] is True


def test_parse_json_with_code_fence():
    assert parse_json('```json\n{"action": "BUY", "confidence": 0.7}\n```')["action"] == "BUY"
