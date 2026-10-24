"""Application service layer: wires the cohort, baselines, detector, and store
together for the API and the rendered views.
"""
from __future__ import annotations

import os
from dataclasses import asdict
from datetime import timedelta

from .baseline import compute_baseline
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

    def _seed_alerts(self):
        """Run the detector once at startup and persist the resulting alerts so
        the dashboard has a populated risk list."""
        for pid in self.cohort.patient_ids():
            for alert in detect_for_patient(self.cohort, pid, self.baselines[pid]):
                if self.store.get_alert(alert.id) is None:
                    self.store.upsert_alert(alert)

    # -- patients ------------------------------------------------------------
    def patient_status(self, pid: str) -> str:
        b = self.baselines[pid]
        if not b.sufficient:
            return "insufficient_baseline"
        open_alerts = [a for a in self.store.list_alerts()
                       if a["patient_id"] == pid and a["status"] in ("open", "acknowledged", "in_progress")]
        if open_alerts:
            return "alerting"
        return "baseline_ok"

    def cohort_overview(self) -> list:
        alerts = self.store.list_alerts()
        by_patient = {}
        for a in alerts:
            if a["status"] in ("open", "acknowledged", "in_progress"):
                by_patient.setdefault(a["patient_id"], []).append(a)
        rows = []
        for pid, p in self.cohort.patients.items():
            pa = by_patient.get(pid, [])
            top = max((a["risk_score"] for a in pa), default=0.0)
            tx_days = [r.tx_day for r in self.cohort.recovery.get(pid, [])
                       if r.tx_day is not None and r.tx_day > 0]
            latest_tx_day = max(tx_days, default=0)
            rows.append({
                "patient_id": pid, "name": p.name, "cohort": p.cohort,
                "diagnosis": p.diagnosis,
                "treatment": p.treatment,
                "home_town": self.cohort.profiles[pid].home_town,
                "drive_minutes": self.cohort.profiles[pid].est_drive_time_min,
                "tx_day": latest_tx_day,
                "treatment_week": max(1, (latest_tx_day + 6) // 7) if latest_tx_day else 0,
                "status": self.patient_status(pid),
                "risk_score": top,
                "open_alerts": len(pa),
                "alert_types": sorted({a["type"] for a in pa}),
                "top_alert_id": max(pa, key=lambda a: a["risk_score"])["id"] if pa else None,
            })
        rows.sort(key=lambda r: (r["risk_score"], r["open_alerts"]), reverse=True)
        return rows

    def timeseries(self, pid: str, signals: list) -> dict:
        b = self.baselines[pid]
        rec = {r.date: r for r in self.cohort.recovery.get(pid, [])}
        sleep = {s.date: s for s in self.cohort.sleep.get(pid, [])}
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
        all_days = [r.tx_day for r in self.cohort.exams.get(pid, []) if r.tx_day is not None]
        cutoff = int(tx_day) if tx_day is not None else max(all_days, default=0)

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
            dates = [lab.date for lab in self.cohort.labs.get(pid, []) if lab.date]
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
        alerts = self.store.list_alerts(status)
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
        return {"alert": alert, "card": card, "hospital": hospital,
                "clinical": clinical, "fhir_labs": fhir_labs}

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
