"""Loads the synthetic recovery_coach_data cohort into typed models.

Supports the flat CSV files for the whole cohort and the WHOOP-shaped JSON for
the demo patients, so real WHOOP data could be swapped in later. Missing days
(device off-wrist or skipped check-in) are kept as None, never imputed.
"""
from __future__ import annotations

import json
import math
import random
from datetime import datetime
from pathlib import Path

import pandas as pd

from .models import (
    CheckIn,
    ClinicalProfile,
    CycleDay,
    GroundTruthEvent,
    LabResult,
    Medication,
    NutritionAssessment,
    Patient,
    PhysicalExam,
    PortalMessage,
    RecoveryDay,
    SleepNight,
)

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "recovery_coach_data"

# Deterministic synthetic locations around Massachusetts so the MassGIS hospital
# locator has something to work with. Not real addresses.
_MA_CENTER = (42.36, -71.06)  # Boston-ish


def _f(v):
    """Parse a float, treating blanks / NaN as missing (None)."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    s = str(v).strip()
    if s == "" or s.lower() == "nan":
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _i(v):
    f = _f(v)
    return int(f) if f is not None else None


def _d(v):
    if v is None or str(v).strip() == "":
        return None
    return datetime.strptime(str(v).strip()[:10], "%Y-%m-%d").date()


def _b(v) -> bool:
    """Parse CSV booleans strictly; never use truthiness on strings."""
    s = str(v).strip().lower()
    if s == "true":
        return True
    if s in ("false", ""):
        return False
    raise ValueError(f"invalid boolean value: {v!r}")


def _require_patient(cohort, patient_id: str, source: str) -> None:
    if patient_id not in cohort.patients:
        raise ValueError(f"{source}: unknown patient_id {patient_id}")


def _synthetic_location(patient_id: str) -> tuple:
    rng = random.Random(patient_id)
    lat = _MA_CENTER[0] + rng.uniform(-0.6, 0.6)
    lon = _MA_CENTER[1] + rng.uniform(-0.8, 0.8)
    return round(lat, 4), round(lon, 4)


class Cohort:
    """In-memory store of the whole synthetic cohort, indexed by patient."""

    def __init__(self):
        self.patients: dict[str, Patient] = {}
        self.recovery: dict[str, list[RecoveryDay]] = {}
        self.sleep: dict[str, list[SleepNight]] = {}
        self.cycle: dict[str, list[CycleDay]] = {}
        self.checkins: dict[str, list[CheckIn]] = {}
        self.messages: dict[str, list[PortalMessage]] = {}
        self.profiles: dict[str, ClinicalProfile] = {}
        self.labs: dict[str, list[LabResult]] = {}
        self.exams: dict[str, list[PhysicalExam]] = {}
        self.nutrition: dict[str, list[NutritionAssessment]] = {}
        self.medications: dict[str, list[Medication]] = {}
        self.ground_truth: dict[str, GroundTruthEvent] = {}

    # -- convenience ---------------------------------------------------------
    def patient_ids(self) -> list[str]:
        return list(self.patients.keys())


def _read_csv(name: str) -> pd.DataFrame:
    return pd.read_csv(DATA_DIR / name, dtype=str, keep_default_na=False)


def load_cohort() -> Cohort:
    c = Cohort()

    # Patients
    for _, r in _read_csv("patients.csv").iterrows():
        pid = r["patient_id"]
        lat, lon = _synthetic_location(pid)
        c.patients[pid] = Patient(
            patient_id=pid,
            name=r["name"],
            age=_i(r["age"]) or 0,
            sex=r["sex"],
            diagnosis=r["diagnosis"],
            stage=r["stage"],
            treatment=r["treatment"],
            tx_start=_d(r["tx_start"]),
            device_start=_d(r["device_start"]),
            baseline_weight_kg=_f(r["baseline_weight_kg"]),
            cohort=r["cohort"],
            story=r["story"],
            lat=lat,
            lon=lon,
            as_of_date=_d(r.get("as_of_date", "")),
            planned_treatment_weeks=_i(r.get("planned_treatment_weeks", "")) or 7,
        )
        c.recovery[pid] = []
        c.sleep[pid] = []
        c.cycle[pid] = []
        c.checkins[pid] = []
        c.messages[pid] = []
        c.labs[pid] = []
        c.exams[pid] = []
        c.nutrition[pid] = []
        c.medications[pid] = []

    # Extended synthetic clinical profile. Its coherent Western Massachusetts
    # location replaces the older generated placeholder used by the hospital locator.
    for _, r in _read_csv("patient_clinical_profile.csv").iterrows():
        pid = r["patient_id"]
        _require_patient(c, pid, "patient_clinical_profile.csv")
        profile = ClinicalProfile(
            patient_id=pid,
            height_cm=_f(r["height_cm"]), baseline_bmi=_f(r["baseline_bmi"]),
            ecog_baseline=_i(r["ecog_baseline"]), hpv_ebv_status=r["hpv_ebv_status"],
            smoking_status=r["smoking_status"], pack_years=_i(r["pack_years"]),
            comorbidities=r["comorbidities"], allergies=r["allergies"],
            primary_language=r["primary_language"], home_town=r["home_town"],
            county=r["county"], home_lat=_f(r["home_lat"]), home_lon=_f(r["home_lon"]),
            distance_to_center_miles=_f(r["distance_to_center_miles"]),
            est_drive_time_min=_i(r["est_drive_time_min"]), broadband=r["broadband"],
            lives_with=r["lives_with"], caregiver_available=_b(r["caregiver_available"]),
            has_home_thermometer=_b(r["has_home_thermometer"]),
            transportation=r["transportation"], cisplatin_regimen=r["cisplatin_regimen"],
            planned_rt_dose_gy=_f(r["planned_rt_dose_gy"]),
            wearable_device=r["wearable_device"], synthetic=_b(r["synthetic"]),
        )
        c.profiles[pid] = profile
        c.patients[pid].lat = profile.home_lat
        c.patients[pid].lon = profile.home_lon

    # Recovery (per-day)
    for _, r in _read_csv("whoop_recovery.csv").iterrows():
        pid = r["patient_id"]
        c.recovery[pid].append(RecoveryDay(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            recovery_score=_f(r["recovery_score"]),
            resting_heart_rate=_f(r["resting_heart_rate"]),
            hrv_rmssd_milli=_f(r["hrv_rmssd_milli"]),
            spo2_percentage=_f(r["spo2_percentage"]),
            skin_temp_celsius=_f(r["skin_temp_celsius"]),
        ))

    # Sleep (per-night)
    for _, r in _read_csv("whoop_sleep.csv").iterrows():
        pid = r["patient_id"]
        c.sleep[pid].append(SleepNight(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            disturbance_count=_f(r["disturbance_count"]),
            respiratory_rate=_f(r["respiratory_rate"]),
            sleep_performance_percentage=_f(r["sleep_performance_percentage"]),
            sleep_efficiency_percentage=_f(r["sleep_efficiency_percentage"]),
        ))

    # Cycle (per-day)
    for _, r in _read_csv("whoop_cycle.csv").iterrows():
        pid = r["patient_id"]
        c.cycle[pid].append(CycleDay(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            strain=_f(r["strain"]),
            average_heart_rate=_f(r["average_heart_rate"]),
            max_heart_rate=_f(r["max_heart_rate"]),
        ))

    # Check-ins (per-day)
    for _, r in _read_csv("checkins.csv").iterrows():
        pid = r["patient_id"]
        c.checkins[pid].append(CheckIn(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            pain_0_10=_f(r["pain_0_10"]),
            swallow_difficulty_0_10=_f(r["swallow_difficulty_0_10"]),
            nausea_0_10=_f(r["nausea_0_10"]),
            fatigue_0_10=_f(r["fatigue_0_10"]),
            fluid_cups=_f(r["fluid_cups"]),
            meals_eaten_pct=_f(r["meals_eaten_pct"]),
            transcript=r.get("transcript", ""),
        ))

    # Expanded labs: use labs_all.csv as the single source. Loading labs.csv as
    # well would double-count every original result.
    for _, r in _read_csv("labs_all.csv").iterrows():
        pid = r["patient_id"]
        _require_patient(c, pid, "labs_all.csv")
        c.labs[pid].append(LabResult(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            encounter=r["encounter"], panel=r["panel"], specimen=r["specimen"],
            test=r["test"], value=_f(r["value"]), result_text=r["result_text"],
            unit=r["unit"], ref_low=_f(r["ref_low"]), ref_high=_f(r["ref_high"]),
            flag=r["flag"],
        ))

    # Point-in-time clinical exam context. This is display-only and never an
    # input to the wearable detector.
    for _, r in _read_csv("physical_exams.csv").iterrows():
        pid = r["patient_id"]
        _require_patient(c, pid, "physical_exams.csv")
        c.exams[pid].append(PhysicalExam(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            exam_type=r["exam_type"], ecog=_i(r["ecog"]), weight_kg=_f(r["weight_kg"]),
            temp_c=_f(r["temp_c"]), heart_rate=_f(r["heart_rate"]),
            sbp=_i(r["sbp"]), dbp=_i(r["dbp"]),
            respiratory_rate=_f(r["respiratory_rate"]), spo2_pct=_f(r["spo2_pct"]),
            orthostatic_vitals=r["orthostatic_vitals"],
            oral_mucositis_grade_ctcae=_i(r["oral_mucositis_grade_ctcae"]),
            dysphagia_grade_ctcae=_i(r["dysphagia_grade_ctcae"]),
            radiation_dermatitis_grade_ctcae=_i(r["radiation_dermatitis_grade_ctcae"]),
            feeding_tube=r["feeding_tube"], exam_summary=r["exam_summary"],
        ))

    for _, r in _read_csv("nutrition_assessments.csv").iterrows():
        pid = r["patient_id"]
        _require_patient(c, pid, "nutrition_assessments.csv")
        c.nutrition[pid].append(NutritionAssessment(
            patient_id=pid, date=_d(r["date"]), tx_day=_i(r["tx_day"]),
            weight_kg=_f(r["weight_kg"]), bmi=_f(r["bmi"]),
            weight_change_pct_from_baseline=_f(r["weight_change_pct_from_baseline"]),
            avg_meals_eaten_pct_7d=_f(r["avg_meals_eaten_pct_7d"]),
            avg_fluid_cups_7d=_f(r["avg_fluid_cups_7d"]),
            fluid_goal_cups=_f(r["fluid_goal_cups"]), diet_texture=r["diet_texture"],
            feeding_route=r["feeding_route"], pg_sga_score=_i(r["pg_sga_score"]),
            pg_sga_category=r["pg_sga_category"], pg_sga_triage=r["pg_sga_triage"],
            dietitian_seen=_b(r["dietitian_seen"]), taste_change=r["taste_change"],
        ))

    for _, r in _read_csv("medications.csv").iterrows():
        pid = r["patient_id"]
        _require_patient(c, pid, "medications.csv")
        c.medications[pid].append(Medication(
            patient_id=pid, medication=r["medication"], dose=r["dose"],
            route_frequency=r["route_frequency"], start_tx_day=_i(r["start_tx_day"]),
            stop_tx_day=_i(r["stop_tx_day"]), indication=r["indication"],
            status=r["status"],
        ))

    # Portal messages
    for _, r in _read_csv("portal_messages.csv").iterrows():
        pid = r["patient_id"]
        if pid not in c.messages:
            c.messages[pid] = []
        c.messages[pid].append(PortalMessage(
            patient_id=pid, sent_at=r["sent_at"], tx_day=_i(r["tx_day"]),
            subject=r["subject"], body=r["body"],
            true_triage=r["true_triage"], true_reason=r["true_reason"],
            helpful_wearable_context=r["helpful_wearable_context"],
        ))

    # Ground truth
    for _, r in _read_csv("events_ground_truth.csv").iterrows():
        c.ground_truth[r["patient_id"]] = GroundTruthEvent(
            patient_id=r["patient_id"],
            event_type=r["event_type"],
            onset_tx_day=_i(r["onset_tx_day"]),
            earliest_reasonable_alert_tx_day=_f(r["earliest_reasonable_alert_tx_day"]),
            counterfactual_ed_tx_day=_f(r["counterfactual_ed_tx_day"]),
            should_alert=str(r["should_alert"]).strip().lower() == "true",
        )

    # sort every longitudinal series by date for safety
    for store in (c.recovery, c.sleep, c.cycle, c.checkins,
                  c.labs, c.exams, c.nutrition):
        for pid in store:
            store[pid].sort(key=lambda x: (x.date is None, x.date))

    # Every extended profile is required to join to all 23 synthetic patients.
    missing_profiles = set(c.patients) - set(c.profiles)
    if missing_profiles:
        raise ValueError(f"patient_clinical_profile.csv missing patients: {sorted(missing_profiles)}")

    return c


# ---------------------------------------------------------------------------
# WHOOP-shaped JSON loader (demo patients) — proves the swap-in path
# ---------------------------------------------------------------------------

def load_recovery_from_whoop_json(patient_id: str) -> list[RecoveryDay]:
    """Load one demo patient's recovery series from the WHOOP-shaped JSON.

    Returns the same RecoveryDay shape as the CSV loader so the two are
    interchangeable. tx_day is not in the JSON, so it is left as None here and
    is matched back to the CSV by date at the call site when needed.
    """
    path = DATA_DIR / "whoop_api_json" / f"{patient_id}.json"
    with open(path, "r", encoding="utf-8") as fh:
        doc = json.load(fh)
    out = []
    for rec in doc.get("recovery", []):
        score = rec.get("score", {}) or {}
        created = rec.get("created_at", "")[:10]
        out.append(RecoveryDay(
            patient_id=patient_id,
            date=_d(created),
            tx_day=0,  # placeholder; CSV is authoritative for tx_day
            recovery_score=_f(score.get("recovery_score")),
            resting_heart_rate=_f(score.get("resting_heart_rate")),
            hrv_rmssd_milli=_f(score.get("hrv_rmssd_milli")),
            spo2_percentage=_f(score.get("spo2_percentage")),
            skin_temp_celsius=_f(score.get("skin_temp_celsius")),
        ))
    out.sort(key=lambda x: (x.date is None, x.date))
    return out
