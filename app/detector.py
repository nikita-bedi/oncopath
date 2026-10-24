"""Rules-based drift detector with reasons.

Two patterns:
  - dehydration: RHR up + HRV down vs baseline, sustained across N days, while
    check-in fluid intake trends down. Requires persistence.
  - infection / possible neutropenic fever: overnight skin-temp + RHR spike vs
    baseline, with supporting respiratory-rate rise and/or SpO2 drop. Allowed to
    fire on a single overnight ONLY because it is corroborated across multiple
    independent signals at once.

A single out-of-range day/night on a single signal can never alert.
Every alert carries a human-readable reason list. Ground truth is NEVER an input
to detection — it is used only to evaluate the detector in evaluation.py.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Optional

from .baseline import Baseline, compute_baseline, zscore
from .config_loader import detection
from .models import Alert, Reason


def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.1f}"


def _recovery_by_date(cohort, pid):
    return {r.date: r for r in cohort.recovery.get(pid, []) if r.date is not None}


def _sleep_by_date(cohort, pid):
    return {s.date: s for s in cohort.sleep.get(pid, []) if s.date is not None}


def _checkin_by_date(cohort, pid):
    return {c.date: c for c in cohort.checkins.get(pid, []) if c.date is not None}


def detect_for_patient(cohort, patient_id: str, baseline: Optional[Baseline] = None) -> list[Alert]:
    """Return all alerts the detector would raise for a patient over their series.

    At most one alert per pattern (the earliest firing day), matching how a
    nurse would see one open item per emerging problem.
    """
    cfg = detection()
    if baseline is None:
        baseline = compute_baseline(cohort, patient_id)

    # No baseline-relative alerts without a sufficient baseline.
    if not baseline.sufficient:
        return []

    recovery = sorted(
        [r for r in cohort.recovery.get(patient_id, []) if r.date is not None],
        key=lambda r: r.date,
    )
    rec_by_date = _recovery_by_date(cohort, patient_id)
    sleep_by_date = _sleep_by_date(cohort, patient_id)
    chk_by_date = _checkin_by_date(cohort, patient_id)

    alerts: list[Alert] = []

    dehy = _detect_dehydration(cfg, baseline, recovery, chk_by_date, patient_id)
    if dehy:
        alerts.append(dehy)

    inf = _detect_infection(cfg, baseline, recovery, sleep_by_date, patient_id)
    if inf:
        alerts.append(inf)

    return alerts


def _detect_dehydration(cfg, baseline, recovery, chk_by_date, patient_id) -> Optional[Alert]:
    d = cfg["dehydration"]
    window = d["window_days"]
    need = d["persist_days"]

    # treatment-period days only (tx_day > 0), need enough history for a window
    tx_recovery = [r for r in recovery if r.tx_day is not None and r.tx_day > 0]

    # baseline mean fluid from check-ins (tx_day <= 0)
    base_fluids = [c.fluid_cups for c in chk_by_date.values()
                   if c.tx_day is not None and c.tx_day <= 0 and c.fluid_cups is not None]
    base_fluid_mean = statistics.fmean(base_fluids) if base_fluids else None

    for i in range(len(tx_recovery)):
        day = tx_recovery[i]
        win = tx_recovery[max(0, i - window + 1): i + 1]
        if len(win) < need:
            continue

        # count days in window where RHR is up AND HRV is down vs baseline
        qualifying = 0
        for r in win:
            z_rhr = zscore(baseline, "resting_heart_rate", r.resting_heart_rate)
            z_hrv = zscore(baseline, "hrv_rmssd_milli", r.hrv_rmssd_milli)
            if z_rhr is None or z_hrv is None:
                continue
            if z_rhr >= d["rhr_z_min"] and z_hrv <= d["hrv_z_max"]:
                qualifying += 1

        if qualifying < need:
            continue

        # fluids trending down vs baseline over the same window
        win_dates = [r.date for r in win]
        win_fluids = [chk_by_date[dt].fluid_cups for dt in win_dates
                      if dt in chk_by_date and chk_by_date[dt].fluid_cups is not None]
        if base_fluid_mean is None or not win_fluids:
            continue
        win_fluid_mean = statistics.fmean(win_fluids)
        if (base_fluid_mean - win_fluid_mean) < d["fluid_drop_cups"]:
            continue

        # The firing day itself must show fluids meaningfully down, so a brief
        # mid-window dip that has already recovered does not trip the alert.
        day_fluid = chk_by_date[day.date].fluid_cups if day.date in chk_by_date else None
        day_drop_min = d.get("day_fluid_drop_cups", 0.0)
        if day_drop_min and (day_fluid is None or (base_fluid_mean - day_fluid) < day_drop_min):
            continue

        # Build reasons from the firing day.
        z_rhr = zscore(baseline, "resting_heart_rate", day.resting_heart_rate)
        z_hrv = zscore(baseline, "hrv_rmssd_milli", day.hrv_rmssd_milli)
        sb_rhr = baseline.signals["resting_heart_rate"]
        sb_hrv = baseline.signals["hrv_rmssd_milli"]
        reasons = [
            Reason("resting_heart_rate", day.resting_heart_rate, sb_rhr.mean, z_rhr,
                   f"{need}+ of last {window} days",
                   f"Resting HR {_fmt(day.resting_heart_rate)} bpm vs baseline "
                   f"{_fmt(sb_rhr.mean)} (+{_fmt(z_rhr)} SD), elevated for several days."),
            Reason("hrv_rmssd_milli", day.hrv_rmssd_milli, sb_hrv.mean, z_hrv,
                   f"{need}+ of last {window} days",
                   f"HRV {_fmt(day.hrv_rmssd_milli)} ms vs baseline "
                   f"{_fmt(sb_hrv.mean)} ({_fmt(z_hrv)} SD), depressed for several days."),
            Reason("fluid_cups", win_fluid_mean, base_fluid_mean, None,
                   f"last {window} days",
                   f"Fluid intake averaging {_fmt(win_fluid_mean)} cups vs baseline "
                   f"{_fmt(base_fluid_mean)} cups — trending down."),
        ]
        risk = _risk_score("dehydration", [z_rhr, z_hrv], qualifying, need)
        return Alert(
            id=f"{patient_id}-dehydration",
            patient_id=patient_id,
            type="dehydration",
            severity=_severity(risk),
            risk_score=risk,
            created_at=datetime.utcnow().isoformat() + "Z",
            tx_day=day.tx_day,
            reasons=[r.__dict__ for r in reasons],
        )
    return None


def _detect_infection(cfg, baseline, recovery, sleep_by_date, patient_id) -> Optional[Alert]:
    inf = cfg["infection"]
    tx_recovery = [r for r in recovery if r.tx_day is not None and r.tx_day > 0]

    for day in tx_recovery:
        z_temp = zscore(baseline, "skin_temp_celsius", day.skin_temp_celsius)
        z_rhr = zscore(baseline, "resting_heart_rate", day.resting_heart_rate)
        if z_temp is None or z_rhr is None:
            continue
        if z_temp < inf["skin_temp_z_min"] or z_rhr < inf["rhr_z_min"]:
            continue

        # supporting signals (respiratory rate up / SpO2 down) from the same night
        supporting = 0
        support_reasons = []
        night = sleep_by_date.get(day.date)
        if night is not None:
            z_resp = zscore(baseline, "respiratory_rate", night.respiratory_rate)
            if z_resp is not None and z_resp >= inf["support_resp_z_min"]:
                supporting += 1
                sb = baseline.signals["respiratory_rate"]
                support_reasons.append(Reason(
                    "respiratory_rate", night.respiratory_rate, sb.mean, z_resp,
                    "overnight",
                    f"Respiratory rate {_fmt(night.respiratory_rate)} vs baseline "
                    f"{_fmt(sb.mean)} (+{_fmt(z_resp)} SD)."))
        z_spo2 = zscore(baseline, "spo2_percentage", day.spo2_percentage)
        if z_spo2 is not None and z_spo2 <= inf["support_spo2_z_max"]:
            supporting += 1
            sb = baseline.signals["spo2_percentage"]
            support_reasons.append(Reason(
                "spo2_percentage", day.spo2_percentage, sb.mean, z_spo2,
                "overnight",
                f"SpO2 {_fmt(day.spo2_percentage)}% vs baseline {_fmt(sb.mean)}% "
                f"({_fmt(z_spo2)} SD)."))

        if supporting < inf["min_supporting"]:
            continue

        sb_temp = baseline.signals["skin_temp_celsius"]
        sb_rhr = baseline.signals["resting_heart_rate"]
        reasons = [
            Reason("skin_temp_celsius", day.skin_temp_celsius, sb_temp.mean, z_temp,
                   "overnight",
                   f"Overnight skin temp {_fmt(day.skin_temp_celsius)}°C vs baseline "
                   f"{_fmt(sb_temp.mean)}°C (+{_fmt(z_temp)} SD)."),
            Reason("resting_heart_rate", day.resting_heart_rate, sb_rhr.mean, z_rhr,
                   "overnight",
                   f"Resting HR {_fmt(day.resting_heart_rate)} bpm vs baseline "
                   f"{_fmt(sb_rhr.mean)} (+{_fmt(z_rhr)} SD)."),
        ] + support_reasons
        risk = _risk_score("infection", [z_temp, z_rhr], 1 + supporting, 1)
        return Alert(
            id=f"{patient_id}-infection",
            patient_id=patient_id,
            type="infection",
            severity=_severity(risk),
            risk_score=risk,
            created_at=datetime.utcnow().isoformat() + "Z",
            tx_day=day.tx_day,
            reasons=[r.__dict__ for r in reasons],
        )
    return None


def _risk_score(pattern: str, zs: list, corroborating: int, needed: int) -> float:
    base = {"dehydration": 40.0, "infection": 60.0}[pattern]
    mag = sum(abs(z) for z in zs if z is not None)
    return round(min(100.0, base + 5 * mag + 4 * corroborating), 1)


def _severity(risk: float) -> str:
    if risk >= 75:
        return "urgent"
    if risk >= 50:
        return "elevated"
    return "watch"
