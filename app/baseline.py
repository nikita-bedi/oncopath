"""Per-patient baseline engine.

Each patient is compared against THEIR OWN pre-treatment period (tx_day <= 0),
never against other patients. If the baseline has too few valid days it is
marked insufficient and no baseline-relative alerts are raised.
"""
from __future__ import annotations

import statistics
from typing import Optional

from .config_loader import detection
from .models import Baseline, SignalBaseline

# signal name -> (store attribute, record attribute)
RECOVERY_SIGNALS = {
    "resting_heart_rate": "resting_heart_rate",
    "hrv_rmssd_milli": "hrv_rmssd_milli",
    "skin_temp_celsius": "skin_temp_celsius",
    "spo2_percentage": "spo2_percentage",
}
SLEEP_SIGNALS = {
    "respiratory_rate": "respiratory_rate",
    "sleep_performance_percentage": "sleep_performance_percentage",
}
CYCLE_SIGNALS = {
    "strain": "strain",
}


def _stats(values: list[float]) -> SignalBaseline:
    n = len(values)
    if n == 0:
        return SignalBaseline("", None, None, None, None, 0)
    mean = statistics.fmean(values)
    sd = statistics.pstdev(values) if n > 1 else 0.0
    median = statistics.median(values)
    if n >= 4:
        q = statistics.quantiles(values, n=4)
        iqr = q[2] - q[0]
    else:
        iqr = 0.0
    return SignalBaseline("", mean, sd, median, iqr, n)


def compute_baseline(cohort, patient_id: str) -> Baseline:
    cfg = detection()["baseline"]
    min_valid = cfg["min_valid_days"]

    recovery = [r for r in cohort.recovery.get(patient_id, []) if r.tx_day is not None and r.tx_day <= 0]
    sleep = [s for s in cohort.sleep.get(patient_id, []) if s.tx_day is not None and s.tx_day <= 0]
    cycle = [c for c in cohort.cycle.get(patient_id, []) if c.tx_day is not None and c.tx_day <= 0]

    signals: dict[str, SignalBaseline] = {}

    def add(name, records, attr):
        vals = [getattr(r, attr) for r in records if getattr(r, attr) is not None]
        sb = _stats(vals)
        sb.signal = name
        signals[name] = sb

    for name, attr in RECOVERY_SIGNALS.items():
        add(name, recovery, attr)
    for name, attr in SLEEP_SIGNALS.items():
        add(name, sleep, attr)
    for name, attr in CYCLE_SIGNALS.items():
        add(name, cycle, attr)

    # "n valid days" is driven by the primary recovery signal (RHR), the
    # backbone of both detection patterns.
    n_valid = signals["resting_heart_rate"].n_valid_days
    sufficient = n_valid >= min_valid

    return Baseline(
        patient_id=patient_id,
        sufficient=sufficient,
        n_valid_days=n_valid,
        signals=signals,
    )


def zscore(baseline: Baseline, signal: str, value: Optional[float]) -> Optional[float]:
    """Deviation of a value from this patient's own baseline, in SDs.

    Returns None if the value is missing, the signal is unknown, or the
    baseline SD is zero (cannot form a z-score).
    """
    if value is None:
        return None
    sb = baseline.signals.get(signal)
    if sb is None or sb.mean is None or not sb.sd:
        return None
    return (value - sb.mean) / sb.sd


def delta(baseline: Baseline, signal: str, value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    sb = baseline.signals.get(signal)
    if sb is None or sb.mean is None:
        return None
    return value - sb.mean
