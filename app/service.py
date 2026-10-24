"""Application service layer: wires the cohort, baselines, detector, and store
together for the API and the rendered views.
"""
from __future__ import annotations

import os
import statistics
from dataclasses import asdict
from datetime import timedelta

from .baseline import compute_baseline, zscore
from .config_loader import detection
from .data_loader import load_cohort
from .detector import detect_for_patient
from .fhir_export import build_context_card, build_fhir_bundle, build_handoff_communication
from .fhir_labs import FHIRLabClient, bundled_fhir_labs
from .hospitals import nearest_suitable
from .store import Store


class Service:
    def __init__(self):
        self.cohort = load_cohort()
        self.store = Store()
        self.baselines = {pid: compute_baseline(self.cohort, pid)
                          for pid in self.cohort.patient_ids()}
        self.fhir_lab_client = FHIRLabClient()
        self.fhir_lab_remote_enabled = os.getenv(
            "FHIR_LAB_REMOTE_ENABLED", "true").lower() == "true"
        self._fhir_lab_cache = {}
        self._seed_alerts()

    def visible_records(self, pid: str, records: list) -> list:
        """Return records available at this synthetic snapshot."""
        as_of = self.cohort.patients[pid].as_of_date
        return [record for record in records
                if record.date is not None and (as_of is None or record.date <= as_of)]

    def current_tx_day(self, pid: str) -> int:
        recovery = self.visible_records(pid, self.cohort.recovery.get(pid, []))
        return max((record.tx_day for record in recovery
                    if record.tx_day is not None and record.tx_day > 0), default=0)

    def patient_outlook(self, pid: str) -> dict:
        """Estimate near-term deterioration from recent personal-baseline drift.

        This is a transparent prototype ranking signal, not a calibrated clinical
        probability. It uses only records available at the cohort snapshot.
        """
        baseline = self.baselines[pid]
        recovery = [record for record in self.visible_records(
            pid, self.cohort.recovery.get(pid, []))
            if record.tx_day is not None and record.tx_day > 0][-3:]
        checkins = [record for record in self.visible_records(
            pid, self.cohort.checkins.get(pid, []))
            if record.tx_day is not None and record.tx_day > 0][-3:]

        if not baseline.sufficient or len(recovery) < 2:
            return {
                "score": 0.0, "level": "insufficient", "direction": "unknown",
                "pattern": "insufficient data", "confidence": "Low",
                "window": "Next 72 hours", "drivers": [],
            }

        def mean_z(signal: str) -> float:
            values = [zscore(baseline, signal, getattr(record, signal))
                      for record in recovery]
            valid = [value for value in values if value is not None]
            return statistics.fmean(valid) if valid else 0.0

        z_rhr = mean_z("resting_heart_rate")
        z_hrv = mean_z("hrv_rmssd_milli")
        z_temp = mean_z("skin_temp_celsius")
        baseline_fluids = [record.fluid_cups for record in self.cohort.checkins.get(pid, [])
                           if record.tx_day is not None and record.tx_day <= 0
                           and record.fluid_cups is not None]
        recent_fluids = [record.fluid_cups for record in checkins
                         if record.fluid_cups is not None]
        baseline_fluid_mean = statistics.fmean(baseline_fluids) if baseline_fluids else 8.0
        recent_fluid_mean = statistics.fmean(recent_fluids) if recent_fluids else baseline_fluid_mean
        fluid_drop = max(0.0, baseline_fluid_mean - recent_fluid_mean)

        dehydration_score = max(0.0, z_rhr) * 10 + max(0.0, -z_hrv) * 10 + fluid_drop * 6
        infection_score = max(0.0, z_rhr) * 10 + max(0.0, z_temp) * 12
        raw_score = max(dehydration_score, infection_score)
        score = round(min(100.0, raw_score), 1)
        pattern = "infection" if infection_score > dehydration_score else "dehydration"
        level = "elevated" if score >= 75 else "moderate" if score >= 50 else "low"

        first_rhr = zscore(baseline, "resting_heart_rate", recovery[0].resting_heart_rate) or 0.0
        last_rhr = zscore(baseline, "resting_heart_rate", recovery[-1].resting_heart_rate) or 0.0
        direction = "worsening" if last_rhr - first_rhr >= 0.75 else (
            "improving" if first_rhr - last_rhr >= 0.75 else "stable")

        driver_values = [
            (max(0.0, z_rhr), f"Resting heart rate is {z_rhr:+.1f} SD from baseline."),
            (max(0.0, -z_hrv), f"HRV is {z_hrv:+.1f} SD from baseline."),
            (max(0.0, z_temp), f"Skin temperature is {z_temp:+.1f} SD from baseline."),
            (fluid_drop, f"Fluid intake is down {fluid_drop:.1f} cups from baseline."),
        ]
        drivers = [text for value, text in sorted(driver_values, reverse=True)
                   if value > 0][:3]
        confidence = "High" if len(recovery) == 3 and len(recent_fluids) >= 2 else "Moderate"
        return {
            "score": score, "level": level, "direction": direction,
            "pattern": pattern, "confidence": confidence,
            "window": "Next 72 hours", "drivers": drivers,
        }

    def _alert_is_current(self, alert: dict) -> bool:
        tx_day = alert.get("tx_day")
        return tx_day is None or tx_day <= self.current_tx_day(alert["patient_id"])

    def attention_alerts(self) -> list:
        """Return one priority item per flagged patient for the dashboard.

        Urgent patient-reported red flags are always included. Remaining slots
        contain the highest short-term outlook scores among patients with an
        unresolved detector alert. Historical alerts stay in the audit store.
        """
        active = [alert for alert in self.alerts()
                  if alert["status"] in ("open", "acknowledged", "in_progress")]
        by_patient = {}
        for alert in active:
            by_patient.setdefault(alert["patient_id"], []).append(alert)

        selected = []
        forecast_candidates = []
        for pid, patient_alerts in by_patient.items():
            red_flags = [alert for alert in patient_alerts
                         if alert["type"] == "red_flag_escalation"]
            if red_flags:
                alert = max(red_flags, key=lambda item: item.get("created_at", ""))
                alert["outlook"] = {
                    "score": 100.0, "level": "urgent", "direction": "needs review",
                    "pattern": "patient red flag", "confidence": "Patient reported",
                    "window": "Now", "drivers": [reason.get("text", "Patient report")
                                                   for reason in alert.get("reasons", [])][:3],
                }
                selected.append(alert)
                continue

            outlook = self.patient_outlook(pid)
            matching = max(
                patient_alerts,
                key=lambda item: (
                    item["type"] == outlook["pattern"],
                    item["risk_score"],
                    item.get("created_at", ""),
                ),
            )
            matching["outlook"] = outlook
            forecast_candidates.append(matching)

        max_flagged = detection().get("dashboard", {}).get("max_flagged_patients", 5)
        available_slots = max(0, max_flagged - len(selected))
        forecast_candidates.sort(
            key=lambda alert: (alert["outlook"]["score"], alert["risk_score"]),
            reverse=True,
        )
        selected.extend(forecast_candidates[:available_slots])
        selected.sort(
            key=lambda alert: (
                alert["type"] == "red_flag_escalation",
                alert["outlook"]["score"],
                alert["risk_score"],
            ),
            reverse=True,
        )
        return selected

    def _seed_alerts(self):
        """Run the detector once at startup and persist alerts visible now."""
        for pid in self.cohort.patient_ids():
            current_tx_day = self.current_tx_day(pid)
            for alert in detect_for_patient(self.cohort, pid, self.baselines[pid]):
                if alert.tx_day is not None and alert.tx_day > current_tx_day:
                    continue
                if self.store.get_alert(alert.id) is None:
                    self.store.upsert_alert(alert)

    # -- patients ------------------------------------------------------------
    def patient_status(self, pid: str) -> str:
        b = self.baselines[pid]
        if not b.sufficient:
            return "insufficient_baseline"
        open_alerts = [a for a in self.attention_alerts()
                       if a["patient_id"] == pid]
        if open_alerts:
            return "alerting"
        return "baseline_ok"

    def cohort_overview(self) -> list:
        alerts = self.attention_alerts()
        by_patient = {}
        for a in alerts:
            if a["status"] in ("open", "acknowledged", "in_progress"):
                by_patient.setdefault(a["patient_id"], []).append(a)
        rows = []
        for pid, p in self.cohort.patients.items():
            pa = by_patient.get(pid, [])
            top = max((a["risk_score"] for a in pa), default=0.0)
            outlook = pa[0]["outlook"] if pa else self.patient_outlook(pid)
            latest_tx_day = self.current_tx_day(pid)
            treatment_week = min(
                p.planned_treatment_weeks,
                max(1, (latest_tx_day + 6) // 7),
            ) if latest_tx_day else 0
            rows.append({
                "patient_id": pid, "name": p.name, "cohort": p.cohort,
                "diagnosis": p.diagnosis,
                "treatment": p.treatment,
                "home_town": self.cohort.profiles[pid].home_town,
                "drive_minutes": self.cohort.profiles[pid].est_drive_time_min,
                "tx_day": latest_tx_day,
                "treatment_week": treatment_week,
                "planned_treatment_weeks": p.planned_treatment_weeks,
                "status": ("insufficient_baseline" if not self.baselines[pid].sufficient
                           else "alerting" if pa else "baseline_ok"),
                "outlook": outlook,
                "outlook_priority": bool(pa),
                "risk_score": top,
                "open_alerts": len(pa),
                "alert_types": sorted({a["type"] for a in pa}),
                "top_alert_id": max(pa, key=lambda a: a["risk_score"])["id"] if pa else None,
            })
        rows.sort(key=lambda r: (r["risk_score"], r["open_alerts"]), reverse=True)
        return rows

    def timeseries(self, pid: str, signals: list) -> dict:
        b = self.baselines[pid]
        rec = {r.date: r for r in self.visible_records(pid, self.cohort.recovery.get(pid, []))}
        sleep = {s.date: s for s in self.visible_records(pid, self.cohort.sleep.get(pid, []))}
        dates = sorted(set(rec) | set(sleep))
        out = {"patient_id": pid, "signals": {}}
        for sig in signals:
            sb = b.signals.get(sig)
            series = []
            for d in dates:
                val = None
                if sig in ("resting_heart_rate", "hrv_rmssd_milli", "skin_temp_celsius", "spo2_percentage"):
                    r = rec.get(d)
                    val = getattr(r, sig) if r else None
                    txd = r.tx_day if r else None
                elif sig in ("respiratory_rate", "sleep_performance_percentage"):
                    s = sleep.get(d)
                    val = getattr(s, sig) if s else None
                    txd = s.tx_day if s else None
                else:
                    txd = None
                series.append({"date": d.isoformat(), "tx_day": txd, "value": val})
            out["signals"][sig] = {
                "baseline_mean": sb.mean if sb else None,
                "baseline_sd": sb.sd if sb else None,
                "series": series,
            }
        return out

    def clinical_context(self, pid: str, tx_day=None) -> dict:
        """Build a point-in-time clinical snapshot without future leakage.

        Clinical data enriches the nurse card only. It never enters detection.
        Counterfactual natural-course ED labs are excluded from operational context.
        """
        cutoff = int(tx_day) if tx_day is not None else self.current_tx_day(pid)

        def latest(records):
            eligible = [r for r in records if r.tx_day is not None and r.tx_day <= cutoff]
            return asdict(max(eligible, key=lambda r: (r.tx_day, r.date))) if eligible else None

        exam = latest(self.cohort.exams.get(pid, []))
        nutrition = latest(self.cohort.nutrition.get(pid, []))

        # Select the most recent routine result for each high-value lab. Acute
        # "natural course" rows encode counterfactual outcomes and are display-
        # excluded so the alert card cannot imply they had already happened.
        wanted = ("anc", "wbc", "hemoglobin", "platelets", "sodium", "bun",
                  "creatinine", "albumin", "crp", "prealbumin")
        routine_labs = [r for r in self.cohort.labs.get(pid, [])
                        if r.tx_day is not None and r.tx_day <= cutoff
                        and "natural course" not in r.encounter.lower()]
        labs = []
        for test in wanted:
            matches = [r for r in routine_labs if r.test == test]
            if matches:
                labs.append(asdict(max(matches, key=lambda r: (r.tx_day, r.date))))

        active_meds = [asdict(m) for m in self.cohort.medications.get(pid, [])
                       if (m.start_tx_day is None or m.start_tx_day <= cutoff)
                       and (m.stop_tx_day is None or m.stop_tx_day >= cutoff)]

        return {
            "as_of_tx_day": cutoff,
            "profile": asdict(self.cohort.profiles[pid]),
            "exam": exam,
            "nutrition": nutrition,
            "labs": labs,
            "medications": active_meds,
            "counterfactual_excluded": True,
        }

    def fhir_lab_context(self, pid: str, tx_day=None) -> dict:
        """Read latest mapped labs from FHIR, with an offline-safe fallback."""
        patient = self.cohort.patients[pid]
        if tx_day is not None and patient.tx_start:
            as_of_date = patient.tx_start + timedelta(days=int(tx_day) - 1)
        else:
            snapshot_date = patient.as_of_date
            dates = [lab.date for lab in self.cohort.labs.get(pid, [])
                     if lab.date and (snapshot_date is None or lab.date <= snapshot_date)]
            as_of_date = max(dates) if dates else patient.tx_start
        cache_key = (pid, int(tx_day) if tx_day is not None else None)
        if cache_key in self._fhir_lab_cache:
            return self._fhir_lab_cache[cache_key]

        remote_error = None
        if self.fhir_lab_remote_enabled:
            try:
                result = self.fhir_lab_client.fetch_latest(pid, as_of_date)
                if result["labs"]:
                    self._fhir_lab_cache[cache_key] = result
                    return result
                remote_error = "No mapped laboratory Observations found on the FHIR server."
            except Exception as exc:
                remote_error = f"FHIR server unavailable: {type(exc).__name__}"

        result = bundled_fhir_labs(
            self.cohort, pid, as_of_date,
            int(tx_day) if tx_day is not None else None)
        result["remote_error"] = remote_error
        self._fhir_lab_cache[cache_key] = result
        return result

    # -- alerts --------------------------------------------------------------
    def alerts(self, status=None):
        alerts = [alert for alert in self.store.list_alerts(status)
                  if self._alert_is_current(alert)]
        overdue = self.store.overdue_ids(detection()["alert"]["overdue_hours"])
        for a in alerts:
            a["overdue"] = a["id"] in overdue
            p = self.cohort.patients.get(a["patient_id"])
            a["patient_name"] = p.name if p else a["patient_id"]
        return alerts

    def alert_card(self, alert_id: str) -> dict:
        alert = self.store.get_alert(alert_id)
        if not alert:
            raise KeyError(alert_id)
        p = self.cohort.patients[alert["patient_id"]]
        hospital = nearest_suitable(p.lat, p.lon) if alert["severity"] in ("urgent", "elevated") else None
        chat_ctx = self.store.chat_context(alert["patient_id"])
        clinical = self.clinical_context(alert["patient_id"], alert.get("tx_day"))
        fhir_labs = self.fhir_lab_context(alert["patient_id"], alert.get("tx_day"))
        card = build_context_card(p, alert, hospital, chat_ctx, clinical)
        if alert["type"] == "red_flag_escalation":
            outlook = {
                "score": 100.0, "level": "urgent", "direction": "needs review",
                "pattern": "patient red flag", "confidence": "Patient reported",
                "window": "Now", "drivers": [reason.get("text", "Patient report")
                                               for reason in alert.get("reasons", [])][:3],
            }
        else:
            outlook = self.patient_outlook(alert["patient_id"])
        return {"alert": alert, "card": card, "hospital": hospital,
                "clinical": clinical, "fhir_labs": fhir_labs,
                "outlook": outlook}

    def handoff_communication(self, alert_id: str) -> dict:
        alert = self.store.get_alert(alert_id)
        if not alert:
            raise KeyError(alert_id)
        patient = self.cohort.patients[alert["patient_id"]]
        labs = self.fhir_lab_context(alert["patient_id"], alert.get("tx_day"))
        return build_handoff_communication(patient, alert, labs)

    def fhir(self, alert_id: str) -> dict:
        alert = self.store.get_alert(alert_id)
        if not alert:
            raise KeyError(alert_id)
        p = self.cohort.patients[alert["patient_id"]]
        hospital = nearest_suitable(p.lat, p.lon)
        return build_fhir_bundle(p, alert, hospital)
