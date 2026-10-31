from datetime import datetime

from invbot.agents import Analysts, PortfolioManager, Proposal
from invbot.bot import Bot
from invbot.broker.paper import PaperBroker
from invbot.ledger import Ledger

from .conftest import make_candles


class FakeData:
    def __init__(self, frames):
        self.frames = frames

    def candles(self, symbol):
        return self.frames[symbol]

    def headlines(self, symbol):
        return [{"title": f"{symbol} wins big order", "source": "x", "published": "", "link": ""}]

    def fundamentals(self, symbol):
        return {"pe": 20}


class ScriptedPM(PortfolioManager):
    """Stands in for the AI: returns whatever the test scripts."""

    def __init__(self, script):
        super().__init__(None)
        self.script = script

    def decide(self, symbol, views, position_qty, snap, survival=None):
        action, conf, size, stop = self.script.get(symbol, ("HOLD", 0, 0, None))
        return Proposal(symbol, action, conf, size, stop, "scripted", views)


def make_bot(cfg, frames, script, when=datetime(2026, 10, 28, 10)):
    cfg["watchlist"] = list(frames)
    ledger = Ledger(":memory:")
    broker = PaperBroker(ledger, cfg["starting_capital"], cfg["costs"])
    return Bot(cfg, ledger, broker, FakeData(frames), Analysts(None), ScriptedPM(script), clock=lambda: when)


def test_cycle_buys_logs_and_charges_costs(cfg):
    frames = {"TCS": make_candles(start=3000), "INFY": make_candles(start=1500, seed=1)}
    bot = make_bot(cfg, frames, {"TCS": ("BUY", 0.8, 0.10, 0.05), "INFY": ("BUY", 0.4, 0.10, None)})
    rep = bot.run_cycle()

    assert rep.alive
    assert len(rep.actions) == 1 and rep.actions[0].startswith("BUY") and "TCS" in rep.actions[0]
    assert any("INFY" in n and "confidence" in n for n in rep.notes)
    pos = bot.ledger.positions()["TCS"]
    assert pos["stop_loss"] < pos["avg_price"]
    assert bot.broker.cash() < 100_000 - pos["qty"] * pos["avg_price"] + 1   # costs were paid
    n = bot.ledger.db.execute("SELECT COUNT(*) FROM decisions").fetchone()[0]
    assert n == 2                                                            # every decision logged


def test_stop_loss_sells_before_new_ideas(cfg):
    frames = {"TCS": make_candles(start=3000)}
    bot = make_bot(cfg, frames, {})
    price = float(frames["TCS"]["close"].iloc[-1])
    bot.ledger.upsert_position("TCS", 10, price * 1.2, price * 1.1, "2026-10-01T10:00:00")
    rep = bot.run_cycle()
    assert "stop-loss" in rep.actions[0]
    assert "TCS" not in bot.ledger.positions()


def test_shutdown_after_two_failed_quarters_liquidates(cfg):
    from datetime import date
    frames = {"TCS": make_candles(start=3000)}
    bot = make_bot(cfg, frames, {"TCS": ("BUY", 0.9, 0.1, None)})
    bot.ledger.record_equity(date(2026, 6, 30), 0, 97_000)
    bot.ledger.record_equity(date(2026, 9, 30), 0, 95_000)
    bot.ledger.upsert_position("TCS", 5, 3000, None, "2026-09-01T10:00:00")
    rep = bot.run_cycle()
    assert rep.alive is False
    assert any("shutdown" in a for a in rep.actions)
    assert bot.ledger.positions() == {}
    assert bot.ledger.get("shutdown")
    # stays dead on the next run
    assert bot.run_cycle().alive is False


def test_rules_only_mode_runs_end_to_end(cfg):
    frames = {"TCS": make_candles(start=3000, drift=0.004), "INFY": make_candles(start=1500, drift=-0.003, seed=2)}
    cfg["watchlist"] = list(frames)
    ledger = Ledger(":memory:")
    broker = PaperBroker(ledger, cfg["starting_capital"], cfg["costs"])
    bot = Bot(cfg, ledger, broker, FakeData(frames), Analysts(None), PortfolioManager(None),
              clock=lambda: datetime(2026, 10, 28, 10))
    rep = bot.run_cycle()
    assert rep.alive and rep.equity > 99_000


def test_backtest_runs_without_lookahead(cfg):
    from invbot.backtest import HistoricalData, run_backtest
    frames = {"TCS": make_candles(n=500, start=3000, drift=0.002),
              "INFY": make_candles(n=500, start=1500, drift=-0.001, seed=3)}
    data = HistoricalData(frames)
    data.as_of = frames["TCS"].index[300]
    assert data.candles("TCS").index[-1] == data.as_of

    res = run_backtest(cfg, frames, start=str(frames["TCS"].index[250].date()), every=5)
    s = res.summary()
    assert len(res.equity) > 200
    assert s["trades"] > 0 and s["costs_paid"] > 0
    assert -1 < s["max_drawdown"] <= 0
