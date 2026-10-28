import numpy as np
import pandas as pd
import pytest

from invbot.config import load_config


@pytest.fixture
def cfg():
    c = load_config("config.example.yaml")
    c["storage"]["db_path"] = ":memory:"
    c["llm"]["provider"] = "none"
    return c


def make_candles(n=260, start=100.0, drift=0.002, seed=0):
    rng = np.random.default_rng(seed)
    close = start * np.cumprod(1 + drift + rng.normal(0, 0.01, n))
    idx = pd.bdate_range(end="2026-10-27", periods=n)
    return pd.DataFrame({
        "open": close * 0.998, "high": close * 1.01, "low": close * 0.99,
        "close": close, "volume": rng.integers(1e5, 2e5, n),
    }, index=idx)
