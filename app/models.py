"""Typed domain models for OncoPath.

Kept as plain dataclasses so they serialize cleanly to JSON for the API and
templates, and so tests can construct them without a database.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date, datetime
from typing import Optional


# ---------------------------------------------------------------------------
# Raw cohort entities (mirror the recovery_coach_data schema)
# ---------------------------------------------------------------------------

@dataclass
class Patient:
    patient_id: str
    name: str
    age: int
    sex: str
    diagnosis: str
    stage: str
    treatment: str
    tx_start: Optional[date]
    device_start: Optional[date]
    baseline_weight_kg: Optional[float]
    cohort: str           # "demo" | "random"
    story: str
    # Synthetic location (lat/lon) used only for the MassGIS hospital locator.
    lat: Optional[float] = None
    lon: Optional[float] = None
    # The cohort is a point-in-time snapshot even though generated files retain
    # complete trajectories for detector evaluation.
    as_of_date: Optional[date] = None
    planned_treatment_weeks: int = 7


@dataclass
class RecoveryDay:
    patient_id: str
    date: date
    tx_day: int
    recovery_score: Optional[float]
    resting_heart_rate: Optional[float]
    hrv_rmssd_milli: Optional[float]
    spo2_percentage: Optional[float]
    skin_temp_celsius: Optional[float]


@dataclass
class SleepNight:
    patient_id: str
    date: date
    tx_day: int
    disturbance_count: Optional[float]
    respiratory_rate: Optional[float]
    sleep_performance_percentage: Optional[float]
    sleep_efficiency_percentage: Optional[float]


@dataclass
class CycleDay:
    patient_id: str
    date: date
    tx_day: int
    strain: Optional[float]
    average_heart_rate: Optional[float]
    max_heart_rate: Optional[float]


@dataclass
class CheckIn:
    patient_id: str
    date: date
    tx_day: int
    pain_0_10: Optional[float]
    swallow_difficulty_0_10: Optional[float]
    nausea_0_10: Optional[float]
    fatigue_0_10: Optional[float]
    fluid_cups: Optional[float]
    meals_eaten_pct: Optional[float]
    transcript: str = ""


@dataclass
class PortalMessage:
    patient_id: str
    sent_at: str
    tx_day: int
    subject: str
    body: str
    true_triage: str
    true_reason: str
    helpful_wearable_context: str


@dataclass
class ClinicalProfile:
    patient_id: str
    height_cm: Optional[float]
    baseline_bmi: Optional[float]
    ecog_baseline: Optional[int]
    hpv_ebv_status: str
    smoking_status: str
    pack_years: Optional[int]
    comorbidities: str
    allergies: str
    primary_language: str
    home_town: str
    county: str
    home_lat: Optional[float]
    home_lon: Optional[float]
    distance_to_center_miles: Optional[float]
    est_drive_time_min: Optional[int]
    broadband: str
    lives_with: str
    caregiver_available: bool
    has_home_thermometer: bool
    transportation: str
    cisplatin_regimen: str
    planned_rt_dose_gy: Optional[float]
    wearable_device: str
    synthetic: bool


@dataclass
class LabResult:
    patient_id: str
    date: date
    tx_day: int
    encounter: str
    panel: str
    specimen: str
    test: str
    value: Optional[float]
    result_text: str
    unit: str
    ref_low: Optional[float]
    ref_high: Optional[float]
    flag: str


@dataclass
class PhysicalExam:
    patient_id: str
    date: date
    tx_day: int
    exam_type: str
    ecog: Optional[int]
    weight_kg: Optional[float]
    temp_c: Optional[float]
    heart_rate: Optional[float]
    sbp: Optional[int]
    dbp: Optional[int]
    respiratory_rate: Optional[float]
    spo2_pct: Optional[float]
    orthostatic_vitals: str
    oral_mucositis_grade_ctcae: Optional[int]
    dysphagia_grade_ctcae: Optional[int]
    radiation_dermatitis_grade_ctcae: Optional[int]
    feeding_tube: str
    exam_summary: str


@dataclass
class NutritionAssessment:
    patient_id: str
    date: date
    tx_day: int
    weight_kg: Optional[float]
    bmi: Optional[float]
    weight_change_pct_from_baseline: Optional[float]
    avg_meals_eaten_pct_7d: Optional[float]
    avg_fluid_cups_7d: Optional[float]
    fluid_goal_cups: Optional[float]
    diet_texture: str
    feeding_route: str
    pg_sga_score: Optional[int]
    pg_sga_category: str
    pg_sga_triage: str
    dietitian_seen: bool
    taste_change: str


@dataclass
class Medication:
    patient_id: str
    medication: str
    dose: str
    route_frequency: str
    start_tx_day: Optional[int]
    stop_tx_day: Optional[int]
    indication: str
    status: str


@dataclass
class GroundTruthEvent:
    patient_id: str
    event_type: str                     # dehydration | neutropenic_fever | false_alarm_confounder
    onset_tx_day: Optional[int]
    earliest_reasonable_alert_tx_day: Optional[float]
    counterfactual_ed_tx_day: Optional[float]
    should_alert: bool


# ---------------------------------------------------------------------------
# Derived entities
# ---------------------------------------------------------------------------

@dataclass
class SignalBaseline:
    signal: str
    mean: Optional[float]
    sd: Optional[float]
    median: Optional[float]
    iqr: Optional[float]
    n_valid_days: int


@dataclass
class Baseline:
    patient_id: str
    sufficient: bool
    n_valid_days: int
    signals: dict = field(default_factory=dict)   # signal -> SignalBaseline


@dataclass
class Reason:
    signal: str
    current_value: Optional[float]
    baseline_value: Optional[float]
    deviation: Optional[float]           # z-score
    window: str
    text: str


@dataclass
class Alert:
    id: str
    patient_id: str
    type: str                            # dehydration | infection | red_flag_escalation
    severity: str                        # watch | elevated | urgent
    risk_score: float
    created_at: str
    tx_day: Optional[int]
    status: str = "open"                 # open|acknowledged|in_progress|resolved|dismissed
    reasons: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    source: str = "detector"             # detector | chat_red_flag

    def to_dict(self) -> dict:
        d = asdict(self)
        return d


# ---------------------------------------------------------------------------
# Chat entities
# ---------------------------------------------------------------------------

@dataclass
class ParsedSymptom:
    symptom: str
    severity: Optional[int]
    onset: Optional[str]
    tier: str                            # soft | red_flag
    confidence: float
    needs_nurse_review: bool


@dataclass
class ChatTurn:
    id: str
    patient_id: str
    author: str                          # patient | nurse | system
    kind: str                            # free_text | symptom_report | system_question | safety_message | nurse_message
    text: str
    created_at: str
    question_id: Optional[str] = None
    parsed_symptoms: list = field(default_factory=list)   # ParsedSymptom dicts
    shared_signals: list = field(default_factory=list)
    share_window_days: Optional[int] = None
    needs_nurse_review: bool = False
    language: str = "en"                 # en | es
    input_mode: str = "typed"            # typed | voice

    def to_dict(self) -> dict:
        return asdict(self)
