import json
from datetime import datetime

from invbot.agents import Analysts, PortfolioManager, View, survival_briefing
from invbot.bot import Bot
from invbot.broker.paper import PaperBroker
from invbot.ledger import Ledger

from .conftest import make_candles
from .test_bot import FakeData


class FakeLLM:
    """Plays every committee role and records what each one was told."""

    def __init__(self, risk_verdict="OK", pm_action="BUY"):
        self.calls = []
        self.risk_verdict = risk_verdict
        self.pm_action = pm_action

    def chat_json(self, system, user):
        self.calls.append((system, user))
        if "BULL researcher" in system:
            return {"argument": "Breakout on volume, earnings beat.", "conviction": 0.8}
        if "BEAR researcher" in system:
            return {"argument": "RSI stretched, sector weak.", "conviction": 0.6}
        if "RISK OFFICER" in system:
            return {"assessment": "Acceptable downside.", "max_size_pct": 0.12,
                    "stop_loss_pct": 0.04, "verdict": self.risk_verdict}
        if "PORTFOLIO MANAGER" in system:
            return {"action": self.pm_action, "confidence": 0.75, "size_pct": 0.3,
                    "stop_loss_pct": 0.06, "winner": "bull", "reasoning": "Bull's volume point stands."}
        return {"stance": 0.5, "summary": "Looks constructive."}


SURVIVAL = {"starting_capital": 100000, "equity": 98000, "quarter": "FY27 Q3",
            "quarter_return": -0.02, "days_left_in_quarter": 61, "failed_in_row": 1, "max_failed": 2}
VIEWS = [View("technical", 0.6, "uptrend"), View("news", 0.2, "ok"), View("fundamentals", 0.0, "fair")]


def test_briefing_explains_shutdown_and_warns_on_last_chance():
    text = survival_briefing(SURVIVAL)
    assert "switched off for good" in text
    assert "1 of 2" in text and "-2.00%" in text
    assert "one more losing quarter ends the fund" in text
    assert survival_briefing(None) == ""


def test_debate_runs_rounds_then_risk_then_ruling():
    llm = FakeLLM()
    p = PortfolioManager(llm, debate_rounds=2).decide("TCS", VIEWS, 0, {"price": 3000}, survival=SURVIVAL)
    speakers = [d["speaker"] for d in p.debate]
    assert speakers == ["bull", "bear", "bull", "bear", "risk_officer", "portfolio_manager"]
    assert all("SURVIVAL BRIEFING" in system for system, _ in llm.calls)
    # later speakers see what was said before them
    assert "RSI stretched" in llm.calls[2][1]
    assert p.action == "BUY"
    assert p.size_pct == 0.12 and p.stop_loss_pct == 0.04   # risk officer's limits win


def test_risk_officer_can_block_a_buy_but_not_a_sell():
    blocked = PortfolioManager(FakeLLM("BLOCK", "BUY"), 1).decide("TCS", VIEWS, 0, {}, SURVIVAL)
    assert blocked.action == "HOLD"
    exit_ = PortfolioManager(FakeLLM("BLOCK", "SELL"), 1).decide("TCS", VIEWS, 10, {}, SURVIVAL)
    assert exit_.action == "SELL"


def test_debate_skipped_when_nobody_is_positive():
    llm = FakeLLM()
    bearish = [View("technical", -0.4, ""), View("news", 0.0, ""), View("fundamentals", -0.1, "")]
    p = PortfolioManager(llm).decide("TCS", bearish, 0, {}, SURVIVAL)
    assert p.action == "HOLD" and llm.calls == []


def test_bot_logs_full_debate_and_briefs_analysts(cfg):
    frames = {"TCS": make_candles(start=3000, drift=0.003)}
    cfg["watchlist"] = list(frames)
    cfg["adaptive"]["enabled"] = False
    llm = FakeLLM()
    ledger = Ledger(":memory:")
    bot = Bot(cfg, ledger, PaperBroker(ledger, cfg["starting_capital"], cfg["costs"]), FakeData(frames),
              Analysts(llm), PortfolioManager(llm), clock=lambda: datetime(2026, 10, 31, 16))
    rep = bot.run_cycle()
    assert rep.actions and rep.actions[0].startswith("BUY")
    row = ledger.db.execute("SELECT proposal_json FROM decisions").fetchone()
    debate = json.loads(row[0])["debate"]
    assert debate[-1]["speaker"] == "portfolio_manager" and debate[-1]["winner"] == "bull"
    assert "FY27 Q3" in llm.calls[0][0]                      # analysts got the briefing too
