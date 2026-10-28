"""Quarterly scorecard and the survival rule."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass
class QuarterResult:
    label: str             # e.g. "FY27 Q1"
    start_equity: float
    end_equity: float
    complete: bool

    @property
    def ret(self) -> float:
        return self.end_equity / self.start_equity - 1 if self.start_equity else 0.0


def fiscal_quarter(d: date, fy_start_month: int = 4) -> tuple[int, int]:
    """(fiscal year, quarter). Indian FY27 runs Apr 2026 - Mar 2027."""
    shifted = (d.month - fy_start_month) % 12
    fy = d.year + 1 if d.month >= fy_start_month and fy_start_month != 1 else d.year
    return fy, shifted // 3 + 1


def _quarter_end(fy: int, q: int, fy_start_month: int) -> date:
    start_year = fy - 1 if fy_start_month != 1 else fy
    months = (fy_start_month - 1) + q * 3                 # months from Jan of start_year to the day after
    first_after = date(start_year + months // 12, months % 12 + 1, 1)
    return date.fromordinal(first_after.toordinal() - 1)


def quarterly_results(curve: list[tuple[date, float]], starting_capital: float,
                      today: date, fy_start_month: int = 4) -> list[QuarterResult]:
    results: list[QuarterResult] = []
    prev_end = starting_capital
    buckets: dict[tuple[int, int], list[tuple[date, float]]] = {}
    for d, eq in sorted(curve):
        buckets.setdefault(fiscal_quarter(d, fy_start_month), []).append((d, eq))
    for (fy, q), points in sorted(buckets.items()):
        end_eq = points[-1][1]
        complete = today > _quarter_end(fy, q, fy_start_month)
        results.append(QuarterResult(f"FY{fy % 100:02d} Q{q}", prev_end, end_eq, complete))
        prev_end = end_eq
    return results


def survival_check(results: list[QuarterResult], min_return: float, max_failed: int) -> tuple[bool, str]:
    """Returns (alive, explanation). Counts failed complete quarters in a row, newest first."""
    streak = 0
    for r in reversed([r for r in results if r.complete]):
        if r.ret <= min_return:
            streak += 1
        else:
            break
    if streak >= max_failed:
        return False, f"{streak} failed quarters in a row (limit {max_failed}); shutting down"
    return True, f"{streak} failed quarter(s) in a row (limit {max_failed})"
