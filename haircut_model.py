"""Regressione locale su vendite /sold per calibrare haircut e rischio."""

from __future__ import annotations

from dataclasses import dataclass

MIN_SAMPLES = 3
MAX_SAMPLES = 40
HAIRCUT_MIN = -0.05
HAIRCUT_MAX = 0.15


@dataclass(frozen=True)
class RiskCoefficients:
    haircut_adj: float
    bid_discount: float
    roi_penalty_pct: float


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def append_sale_sample(data: dict, *, estimated: float, actual: float) -> dict:
    samples: list[dict[str, float]] = list(data.get("samples") or [])
    samples.append({"est": float(estimated), "actual": float(actual)})
    if len(samples) > MAX_SAMPLES:
        samples = samples[-MAX_SAMPLES:]
    out = dict(data)
    out["samples"] = samples
    out["n"] = len(samples)
    err = actual - estimated
    out["err_sum"] = float(out.get("err_sum") or 0) + err
    return out


def regression_haircut_adjustment(data: dict) -> float:
    """Stima haircut extra da errori profitto stimato vs reale."""
    samples: list[dict[str, float]] = list(data.get("samples") or [])
    legacy = float(data.get("haircut_adj") or 0)
    if len(samples) < MIN_SAMPLES:
        return _clamp(legacy, HAIRCUT_MIN, HAIRCUT_MAX)

    ests = [float(s["est"]) for s in samples]
    acts = [float(s["actual"]) for s in samples]
    n = len(ests)
    mean_est = sum(ests) / n
    mean_act = sum(acts) / n
    var_est = sum((e - mean_est) ** 2 for e in ests)

    if var_est < 1e-6:
        mean_err = mean_act - mean_est
        scale = max(abs(mean_est), 20.0)
        adj = -mean_err / scale * 0.12
    else:
        cov = sum((e - mean_est) * (a - mean_act) for e, a in zip(ests, acts))
        slope = cov / var_est
        intercept = mean_act - slope * mean_est
        slope_penalty = max(0.0, 1.0 - _clamp(slope, 0.0, 1.5)) * 0.08
        bias_penalty = max(0.0, -intercept / max(abs(mean_est), 20.0)) * 0.06
        adj = slope_penalty + bias_penalty

    blended = legacy * 0.25 + adj * 0.75
    return _clamp(blended, HAIRCUT_MIN, HAIRCUT_MAX)


def risk_coefficients_from_data(data: dict) -> RiskCoefficients:
    haircut = regression_haircut_adjustment(data)
    samples: list[dict[str, float]] = list(data.get("samples") or [])
    bid_discount = 1.0
    roi_penalty = 0.0

    if len(samples) >= MIN_SAMPLES:
        errors = [float(s["actual"]) - float(s["est"]) for s in samples]
        mean_err = sum(errors) / len(errors)
        if mean_err < -8:
            bid_discount = _clamp(1.0 + mean_err / 120.0, 0.82, 1.0)
            roi_penalty = _clamp(-mean_err / 4.0, 0.0, 12.0)
        elif mean_err > 8:
            bid_discount = _clamp(1.0 + mean_err / 200.0, 1.0, 1.05)
            roi_penalty = 0.0

    if haircut > 0.08:
        bid_discount = min(bid_discount, 1.0 - (haircut - 0.08) * 0.5)
        roi_penalty = max(roi_penalty, (haircut - 0.08) * 40)

    return RiskCoefficients(
        haircut_adj=haircut,
        bid_discount=_clamp(bid_discount, 0.80, 1.05),
        roi_penalty_pct=roi_penalty,
    )
