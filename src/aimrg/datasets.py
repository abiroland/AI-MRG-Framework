"""Synthetic, reproducible datasets for examples, tests and demos.

The credit portfolio below is **entirely simulated**. It exists so that every AI-MRG
feature can be exercised without real customer data. It does not represent any lender.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

FEATURES = [
    "income_k",
    "debt_to_income",
    "utilization",
    "delinquencies_24m",
    "inquiries_6m",
    "months_on_book",
    "home_owner",
]
TARGET = "bad"
SCORE = "pd_score"


@dataclass(frozen=True)
class _Period:
    n: int
    utilization_shift: float = 0.0
    income_shift: float = 0.0
    utilization_effect: float = 2.2
    base_rate_shift: float = 0.0


_PERIODS: dict[str, _Period] = {
    "dev": _Period(n=6000),
    "test": _Period(n=4000),
    "oot": _Period(n=3000, utilization_shift=0.03),
    # The monitored period has a shifted population and a weaker utilization signal,
    # so monitoring checks have something to find.
    "current": _Period(
        n=3000,
        utilization_shift=0.07,
        income_shift=-6.0,
        utilization_effect=0.8,
        base_rate_shift=0.35,
    ),
}


def _simulate(rng: np.random.Generator, period: _Period) -> pd.DataFrame:
    n = period.n
    income = np.clip(
        rng.lognormal(mean=np.log(60), sigma=0.45, size=n) + period.income_shift, 12, None
    )
    dti = np.clip(rng.beta(2.2, 6.0, size=n), 0, 0.95)
    utilization = np.clip(rng.beta(2.0, 3.5, size=n) + period.utilization_shift, 0, 1)
    delinquencies = rng.poisson(0.35, size=n)
    inquiries = rng.poisson(0.9, size=n)
    months_on_book = rng.integers(3, 180, size=n)
    home_owner = rng.choice(["yes", "no"], size=n, p=[0.45, 0.55])

    logit = (
        -3.4
        + period.base_rate_shift
        - 0.012 * (income - 60)
        + 2.0 * dti
        + period.utilization_effect * utilization
        + 0.55 * delinquencies
        + 0.18 * inquiries
        - 0.004 * months_on_book
        - 0.25 * (home_owner == "yes")
    )
    bad = rng.binomial(1, 1 / (1 + np.exp(-logit)))
    frame = pd.DataFrame(
        {
            "income_k": income.round(1),
            "debt_to_income": dti.round(3),
            "utilization": utilization.round(3),
            "delinquencies_24m": delinquencies,
            "inquiries_6m": inquiries,
            "months_on_book": months_on_book,
            "home_owner": home_owner,
            TARGET: bad,
        }
    )
    # A small amount of missing income, as in real application data.
    frame.loc[rng.random(n) < 0.02, "income_k"] = np.nan
    return frame


def _design(frame: pd.DataFrame, medians: pd.Series) -> np.ndarray:
    numeric = frame[FEATURES[:-1]].fillna(medians)
    x = np.column_stack(
        [
            np.ones(len(frame)),
            numeric.to_numpy(dtype=float),
            (frame["home_owner"] == "yes").to_numpy(float),
        ]
    )
    return x


def _fit_logistic(x: np.ndarray, y: np.ndarray, ridge: float = 1e-4) -> np.ndarray:
    """Logistic regression by iteratively reweighted least squares."""
    mean, std = x[:, 1:].mean(axis=0), x[:, 1:].std(axis=0)
    std[std == 0] = 1.0
    xs = x.copy()
    xs[:, 1:] = (x[:, 1:] - mean) / std
    beta = np.zeros(xs.shape[1])
    for _ in range(50):
        p = 1 / (1 + np.exp(-xs @ beta))
        w = p * (1 - p)
        hessian = xs.T @ (xs * w[:, None]) + ridge * np.eye(xs.shape[1])
        step = np.linalg.solve(hessian, xs.T @ (y - p))
        beta += step
        if np.max(np.abs(step)) < 1e-8:
            break
    # Undo the standardization so the coefficients apply to raw inputs.
    raw = beta.copy()
    raw[1:] = beta[1:] / std
    raw[0] = beta[0] - np.sum(beta[1:] * mean / std)
    return raw


def synthetic_credit_portfolio(seed: int = 7) -> dict[str, pd.DataFrame]:
    """Simulated credit-card underwriting data, scored by a logistic model fitted on ``dev``.

    Returns DataFrames keyed by dataset role: ``dev``, ``test``, ``oot`` and ``current``.
    Each has the :data:`FEATURES`, the observed outcome :data:`TARGET` (1 = defaulted) and
    the model's probability of default :data:`SCORE`. The ``current`` period is drifted.
    """
    rng = np.random.default_rng(seed)
    frames = {role: _simulate(rng, period) for role, period in _PERIODS.items()}
    medians = frames["dev"][FEATURES[:-1]].median(numeric_only=True)
    beta = _fit_logistic(_design(frames["dev"], medians), frames["dev"][TARGET].to_numpy(float))
    for frame in frames.values():
        frame[SCORE] = (1 / (1 + np.exp(-_design(frame, medians) @ beta))).round(6)
    return frames
