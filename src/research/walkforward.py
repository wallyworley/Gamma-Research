"""Locked expanding-walk-forward scorer for EXP-2026-001."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


DEFAULT_CONTROLS = (
    "atm_iv_30d",
    "put_call_skew_25d_30d",
    "log_option_volume_notional",
    "quoted_relative_spread_median",
    "return_5d",
    "return_20d",
    "lagged_market_return",
    "har_abs_daily",
    "har_abs_weekly",
    "har_abs_monthly",
    "days_to_monthly_opex",
    "month_end",
)


@dataclass(frozen=True)
class _Fit:
    beta: np.ndarray
    lo: pd.Series
    hi: pd.Series
    mean: pd.Series
    scale: pd.Series
    columns: tuple[str, ...]


def next_day_absolute_log_return(bars: pd.DataFrame) -> pd.Series:
    """Outcome on row t uses close t+1; caller must pass development-only bars."""
    close = bars["close"].astype("float64")
    return np.log(close.shift(-1) / close).abs().rename("target_abs_log_return")


def _design(frame: pd.DataFrame, columns: tuple[str, ...]) -> pd.DataFrame:
    x = frame.loc[:, list(columns)].astype("float64").copy()
    # Day-of-week is categorical, with Monday as the omitted reference category.
    if "day_of_week" in frame:
        dow = pd.get_dummies(frame["day_of_week"].astype("Int64"), prefix="dow", dtype=float)
        for day in range(1, 5):
            x[f"dow_{day}"] = dow.get(f"dow_{day}", pd.Series(0.0, index=frame.index))
    return x


def _fit(train: pd.DataFrame, columns: tuple[str, ...], target: str) -> _Fit:
    x = _design(train, columns)
    lo = x.quantile(0.005)
    hi = x.quantile(0.995)
    clipped = x.clip(lower=lo, upper=hi, axis=1)
    mean = clipped.mean()
    scale = clipped.std(ddof=0).replace(0.0, 1.0)
    z = (clipped - mean) / scale
    matrix = np.column_stack([np.ones(len(z)), z.to_numpy(dtype=float)])
    beta = np.linalg.lstsq(matrix, train[target].to_numpy(dtype=float), rcond=None)[0]
    return _Fit(beta, lo, hi, mean, scale, tuple(z.columns))


def _predict(fit: _Fit, test: pd.DataFrame, columns: tuple[str, ...]) -> np.ndarray:
    x = _design(test, columns)
    for col in fit.columns:
        if col not in x:
            x[col] = 0.0
    x = x.loc[:, list(fit.columns)]
    z = (x.clip(lower=fit.lo, upper=fit.hi, axis=1) - fit.mean) / fit.scale
    matrix = np.column_stack([np.ones(len(z)), z.to_numpy(dtype=float)])
    return matrix @ fit.beta


def _moving_block_p(loss_gain: np.ndarray, *, block_length: int, samples: int,
                    seed: int) -> tuple[float, list[float]]:
    if len(loss_gain) == 0:
        return float("nan"), [float("nan"), float("nan")]
    rng = np.random.default_rng(seed)
    starts = np.arange(max(1, len(loss_gain) - block_length + 1))
    means = np.empty(samples)
    n_blocks = int(np.ceil(len(loss_gain) / block_length))
    for i in range(samples):
        chunks = [loss_gain[s:s + block_length] for s in rng.choice(starts, n_blocks)]
        means[i] = np.concatenate(chunks)[:len(loss_gain)].mean()
    return float(np.mean(means <= 0.0)), [float(x) for x in np.quantile(means, [0.025, 0.975])]


def _block_permute(values: pd.Series, *, block_length: int,
                   rng: np.random.Generator) -> pd.Series:
    a = values.to_numpy(copy=True)
    blocks = [a[i:i + block_length] for i in range(0, len(a), block_length)]
    order = rng.permutation(len(blocks))
    return pd.Series(np.concatenate([blocks[i] for i in order]), index=values.index)


def _score_once(frame: pd.DataFrame, *, signal: str, controls: tuple[str, ...],
                target: str, first_test_year: int, minimum_train: int) -> dict:
    required = list(controls) + [signal, target, "day_of_week"]
    clean = frame.dropna(subset=required).sort_index()
    rows = []
    folds = []
    for year in sorted(set(clean.index.year)):
        if year < first_test_year:
            continue
        train = clean[clean.index.year < year]
        test = clean[clean.index.year == year]
        if len(train) < minimum_train or len(test) < 5:
            continue
        base = _fit(train, controls, target)
        augmented_columns = controls + (signal,)
        aug = _fit(train, augmented_columns, target)
        base_pred = _predict(base, test, controls)
        aug_pred = _predict(aug, test, augmented_columns)
        signal_position = 1 + list(aug.columns).index(signal)
        coef = float(aug.beta[signal_position])
        fold = pd.DataFrame({
            "actual": test[target], "base": base_pred, "augmented": aug_pred,
            "fold_year": year,
        }, index=test.index)
        rows.append(fold)
        folds.append({
            "year": int(year), "train_rows": int(len(train)), "test_rows": int(len(test)),
            "signal_coefficient_standardized": coef,
            "coefficient_negative": bool(coef < 0),
        })
    if not rows:
        raise ValueError("no eligible walk-forward folds")
    scored = pd.concat(rows).sort_index()
    base_sse = float(np.square(scored["actual"] - scored["base"]).sum())
    aug_sse = float(np.square(scored["actual"] - scored["augmented"]).sum())
    improvement = 1.0 - aug_sse / base_sse if base_sse > 0 else float("nan")
    loss_gain = (np.square(scored["actual"] - scored["base"])
                 - np.square(scored["actual"] - scored["augmented"])).to_numpy()
    return {
        "n_complete_rows": int(len(clean)),
        "n_oos_predictions": int(len(scored)),
        "oos_span": [str(scored.index.min().date()), str(scored.index.max().date())],
        "baseline_sse": base_sse,
        "augmented_sse": aug_sse,
        "oos_squared_error_improvement": float(improvement),
        "annual_fold_sign_consistency": float(np.mean([x["coefficient_negative"] for x in folds])),
        "folds": folds,
        "loss_gain": loss_gain,
    }


def walk_forward_score(frame: pd.DataFrame, *, signal: str,
                       controls: tuple[str, ...] = DEFAULT_CONTROLS,
                       target: str = "target_abs_log_return",
                       first_test_year: int = 2019, minimum_train: int = 252,
                       block_length: int = 10, bootstrap_samples: int = 2000,
                       placebo_permutations: int = 100, seed: int = 20260713) -> dict:
    """Score one fixed signal and return the four pre-registered gates."""
    observed = _score_once(frame, signal=signal, controls=controls, target=target,
                           first_test_year=first_test_year, minimum_train=minimum_train)
    p_value, ci = _moving_block_p(observed.pop("loss_gain"), block_length=block_length,
                                  samples=bootstrap_samples, seed=seed)

    rng = np.random.default_rng(seed + 1)
    placebo_improvements = []
    for _ in range(placebo_permutations):
        permuted = frame.copy()
        permuted[signal] = _block_permute(frame[signal], block_length=block_length, rng=rng)
        score = _score_once(permuted, signal=signal, controls=controls, target=target,
                            first_test_year=first_test_year, minimum_train=minimum_train)
        placebo_improvements.append(score["oos_squared_error_improvement"])
    observed_improvement = observed["oos_squared_error_improvement"]
    percentile = float(np.mean(observed_improvement > np.asarray(placebo_improvements)))

    lead = frame.copy()
    lead[signal] = lead[signal].shift(-1)
    lead_score = _score_once(lead, signal=signal, controls=controls, target=target,
                             first_test_year=first_test_year, minimum_train=minimum_train)
    lead_score.pop("loss_gain")

    observed.update({
        "moving_block_bootstrap": {
            "samples": int(bootstrap_samples), "block_length": int(block_length),
            "one_sided_p_mean_loss_gain_le_zero": p_value,
            "mean_loss_gain_ci95": ci,
        },
        "block_permutation_placebo": {
            "permutations": int(placebo_permutations),
            "observed_percentile": percentile,
            "improvement_quantiles": [float(x) for x in np.quantile(placebo_improvements, [.05, .5, .95])],
        },
        "lead_signal_falsifier": {
            "signal_shift": "t+1 signal placed on row t",
            "oos_squared_error_improvement": lead_score["oos_squared_error_improvement"],
            "annual_fold_sign_consistency": lead_score["annual_fold_sign_consistency"],
        },
    })
    return observed


def coverage_by_year(frame: pd.DataFrame, columns: tuple[str, ...]) -> dict:
    result = {}
    for year, group in frame.groupby(frame.index.year):
        result[str(int(year))] = {
            "rows": int(len(group)),
            "coverage": {col: float(group[col].notna().mean()) for col in columns},
            "complete_case_rows": int(group.loc[:, list(columns)].notna().all(axis=1).sum()),
        }
    return result


__all__ = [
    "DEFAULT_CONTROLS", "next_day_absolute_log_return", "walk_forward_score",
    "coverage_by_year",
]
