"""Numerical building blocks for checks (NumPy/pandas only, no modelling frameworks)."""

from __future__ import annotations

import numpy as np
import pandas as pd

EPSILON = 1e-6


def _clean(y_true: pd.Series, y_score: pd.Series) -> tuple[np.ndarray, np.ndarray]:
    frame = pd.DataFrame({"y": y_true, "s": y_score}).dropna()
    y = frame["y"].to_numpy()
    if not set(np.unique(y)) <= {0, 1}:
        raise ValueError("target must be binary (0/1)")
    return y.astype(int), frame["s"].to_numpy(dtype=float)


def auc(y_true: pd.Series, y_score: pd.Series) -> float:
    """Area under the ROC curve (Mann-Whitney U with average ranks for ties).

    Assumes a higher score means higher likelihood of the positive class (y = 1).
    """
    y, s = _clean(y_true, y_score)
    n_pos = int(y.sum())
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        raise ValueError("AUC needs both classes present")
    ranks = pd.Series(s).rank(method="average").to_numpy()
    return float((ranks[y == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def gini(y_true: pd.Series, y_score: pd.Series) -> float:
    return 2.0 * auc(y_true, y_score) - 1.0


def ks(y_true: pd.Series, y_score: pd.Series) -> float:
    """Kolmogorov-Smirnov: max distance between the score CDFs of the two classes."""
    y, s = _clean(y_true, y_score)
    order = np.argsort(s, kind="mergesort")
    y_sorted, s_sorted = y[order], s[order]
    cum_pos = np.cumsum(y_sorted) / max(y_sorted.sum(), 1)
    cum_neg = np.cumsum(1 - y_sorted) / max((1 - y_sorted).sum(), 1)
    # Evaluate only at the last index of each distinct score so ties are not split.
    last_of_value = np.r_[s_sorted[1:] != s_sorted[:-1], True]
    return float(np.max(np.abs(cum_pos - cum_neg)[last_of_value]))


def discrimination(y_true: pd.Series, y_score: pd.Series) -> dict[str, float]:
    return {
        "auc": auc(y_true, y_score),
        "gini": gini(y_true, y_score),
        "ks": ks(y_true, y_score),
        "n": float(pd.Series(y_true).notna().sum()),
        "event_rate": float(pd.Series(y_true).mean()),
    }


def _is_categorical(series: pd.Series) -> bool:
    return not pd.api.types.is_numeric_dtype(series) or series.nunique(dropna=True) <= 5


def distribution_table(expected: pd.Series, actual: pd.Series, bins: int = 10) -> pd.DataFrame:
    """Share of each sample per bin. Numeric bins come from ``expected``'s quantiles."""
    if _is_categorical(expected):
        exp_labels = expected.astype("string").fillna("<missing>")
        act_labels = actual.astype("string").fillna("<missing>")
    else:
        edges = np.unique(
            np.nanquantile(expected.to_numpy(dtype=float), np.linspace(0, 1, bins + 1))
        )
        if len(edges) < 2:
            edges = np.array([-np.inf, np.inf])
        edges[0], edges[-1] = -np.inf, np.inf
        exp_labels = pd.cut(expected, bins=edges.tolist(), include_lowest=True).astype("string")
        act_labels = pd.cut(actual, bins=edges.tolist(), include_lowest=True).astype("string")
        exp_labels = exp_labels.fillna("<missing>")
        act_labels = act_labels.fillna("<missing>")
    exp_share = exp_labels.value_counts(normalize=True)
    act_share = act_labels.value_counts(normalize=True)
    table = pd.concat([exp_share, act_share], axis=1, keys=["expected", "actual"]).fillna(0.0)
    table.index.name = "bin"
    return table


def psi(expected: pd.Series, actual: pd.Series, bins: int = 10) -> tuple[float, pd.DataFrame]:
    """Population stability index and the per-bin contribution table."""
    table = distribution_table(expected, actual, bins)
    e = table["expected"].clip(lower=EPSILON)
    a = table["actual"].clip(lower=EPSILON)
    table["contribution"] = (a - e) * np.log(a / e)
    return float(table["contribution"].sum()), table


def calibration_table(y_true: pd.Series, y_prob: pd.Series, bins: int = 10) -> pd.DataFrame:
    """Predicted vs observed event rate by score band (bands from ``y_prob`` quantiles)."""
    frame = pd.DataFrame({"y": y_true, "p": y_prob}).dropna()
    frame["band"] = pd.qcut(frame["p"], q=bins, duplicates="drop")
    grouped = frame.groupby("band", observed=True).agg(
        n=("y", "size"), predicted=("p", "mean"), observed=("y", "mean")
    )
    grouped["difference"] = grouped["observed"] - grouped["predicted"]
    grouped.index = grouped.index.astype("string")
    grouped.index.name = "band"
    return grouped.reset_index()
