"""FHIR R4 laboratory Observation integration.

Reads category=laboratory Observations from a FHIR server, maps the ten labs used
by OncoPath by exact LOINC code, and selects the most recent result at or
before the alert's simulated date. The public HAPI server is the demo default;
bundled synthetic labs are a deterministic fallback because the public server is
shared and periodically reset.
"""
from __future__ import annotations

import os
from datetime import date, datetime
from typing import Optional

import httpx

FHIR_LAB_BASE_URL = os.getenv("FHIR_LAB_BASE_URL", "https://hapi.fhir.org/baseR4").rstrip("/")
PATIENT_IDENTIFIER_SYSTEM = "http://between-visits.example/patient"
SYNTHETIC_TAG_SYSTEM = "http://between-visits.example/synthetic"

LOINC_LABS = {
    "sodium": {"code": "2951-2", "display": "Sodium"},
    "potassium": {"code": "2823-3", "display": "Potassium"},
    "creatinine": {"code": "2160-0", "display": "Creatinine"},
    "bun": {"code": "3094-0", "display": "Blood urea nitrogen"},
    "magnesium": {"code": "19123-9", "display": "Magnesium"},
    "wbc": {"code": "6690-2", "display": "Leukocytes"},
    "anc": {"code": "751-8", "display": "Neutrophils"},
    "hemoglobin": {"code": "718-7", "display": "Hemoglobin"},
    "platelets": {"code": "777-3", "display": "Platelets"},
    "albumin": {"code": "1751-7", "display": "Albumin"},
}
CODE_TO_NAME = {meta["code"]: name for name, meta in LOINC_LABS.items()}
LAB_ORDER = list(LOINC_LABS)


def patient_resource_id(patient_id: str) -> str:
    return "bv-" + patient_id.lower()


def observation_loinc(observation: dict) -> Optional[str]:
    for coding in observation.get("code", {}).get("coding", []):
        if coding.get("system") == "http://loinc.org" and coding.get("code") in CODE_TO_NAME:
            return coding["code"]
    return None


def observation_date(observation: dict) -> Optional[date]:
    raw = (observation.get("effectiveDateTime") or observation.get("issued") or
           observation.get("effectivePeriod", {}).get("end") or
           observation.get("effectivePeriod", {}).get("start"))
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw)[:10])
    except ValueError:
        return None


def is_laboratory(observation: dict) -> bool:
    for category in observation.get("category", []):
        for coding in category.get("coding", []):
            if coding.get("code") == "laboratory":
                return True
    return False


def _interpretation(observation: dict) -> str:
    for interpretation in observation.get("interpretation", []):
        for coding in interpretation.get("coding", []):
            if coding.get("code"):
                return coding["code"]
        if interpretation.get("text"):
            return interpretation["text"]
    return ""


def _reference_range(observation: dict) -> dict:
    ranges = observation.get("referenceRange", [])
    if not ranges:
        return {"low": None, "high": None, "unit": ""}
    r = ranges[0]
    low, high = r.get("low", {}), r.get("high", {})
    return {
        "low": low.get("value"),
        "high": high.get("value"),
        "unit": low.get("unit") or high.get("unit") or "",
    }


def select_latest_observations(observations: list[dict],
                               as_of_date: Optional[date] = None) -> list[dict]:
    """Select latest and previous result for each mapped LOINC.

    Selection is independent per LOINC and deterministic. Only final/amended/
    corrected category=laboratory Observations at or before `as_of_date` count.
    """
    grouped: dict[str, list[dict]] = {code: [] for code in CODE_TO_NAME}
    for obs in observations:
        if obs.get("resourceType") != "Observation" or not is_laboratory(obs):
            continue
        if obs.get("status") not in ("final", "amended", "corrected"):
            continue
        code = observation_loinc(obs)
        collected = observation_date(obs)
        if not code or not collected or (as_of_date and collected > as_of_date):
            continue
        if "valueQuantity" not in obs:
            continue
        grouped[code].append(obs)

    selected = []
    for name in LAB_ORDER:
        code = LOINC_LABS[name]["code"]
        rows = sorted(grouped[code],
                      key=lambda o: (observation_date(o), o.get("meta", {}).get("lastUpdated", ""), o.get("id", "")),
                      reverse=True)
        if not rows:
            continue
        latest = rows[0]
        previous = rows[1] if len(rows) > 1 else None
        quantity = latest["valueQuantity"]
        current_value = quantity.get("value")
        previous_value = (previous or {}).get("valueQuantity", {}).get("value")
        change = None
        direction = "no prior result"
        if isinstance(current_value, (int, float)) and isinstance(previous_value, (int, float)):
            change = round(float(current_value) - float(previous_value), 3)
            direction = "up" if change > 0 else "down" if change < 0 else "unchanged"
        ref = _reference_range(latest)
        selected.append({
            "name": name,
            "display": LOINC_LABS[name]["display"],
            "loinc": code,
            "observation_id": latest.get("id"),
            "value": current_value,
            "unit": quantity.get("unit") or quantity.get("code") or ref["unit"],
            "collected_date": observation_date(latest).isoformat(),
            "reference_low": ref["low"],
            "reference_high": ref["high"],
            "flag": _interpretation(latest),
            "previous_value": previous_value,
            "previous_date": observation_date(previous).isoformat() if previous else None,
            "change": change,
            "trend": direction,
        })
    return selected


def panel_freshness(labs: list[dict], as_of_date: date, max_age_days: int = 5) -> dict:
    if not labs:
        return {"panel_date": None, "age_days": None, "stale": True,
                "warning": "No laboratory Observations were found."}
    panel_date = max(date.fromisoformat(lab["collected_date"]) for lab in labs)
    age = max(0, (as_of_date - panel_date).days)
    stale = age > max_age_days
    return {
        "panel_date": panel_date.isoformat(), "age_days": age, "stale": stale,
        "warning": (f"Latest laboratory panel is {age} days old. Confirm current results in the EHR."
                    if stale else None),
    }


def lab_to_observation(patient_id: str, lab, patient_fhir_id: Optional[str] = None) -> dict:
    """Convert a bundled synthetic LabResult to a FHIR R4 Observation."""
    meta = LOINC_LABS[lab.test]
    resource = {
        "resourceType": "Observation",
        "id": None,
        "meta": {"tag": [{"system": SYNTHETIC_TAG_SYSTEM,
                            "code": "synthetic-data",
                            "display": "Synthetic data — not a real patient"}]},
        "status": "final",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/observation-category",
            "code": "laboratory", "display": "Laboratory"}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": meta["code"],
                               "display": meta["display"]}], "text": meta["display"]},
        "subject": {"reference": f"Patient/{patient_fhir_id or patient_resource_id(patient_id)}"},
        "effectiveDateTime": lab.date.isoformat(),
        "valueQuantity": {"value": lab.value, "unit": lab.unit,
                           "system": "http://unitsofmeasure.org", "code": lab.unit},
        "extension": [{"url": "http://between-visits.example/fhir/StructureDefinition/treatment-day",
                       "valueInteger": lab.tx_day}],
        "note": [{"text": "Synthetic data for OncoPath hackathon demo."}],
    }
    if lab.ref_low is not None or lab.ref_high is not None:
        rr = {}
        if lab.ref_low is not None:
            rr["low"] = {"value": lab.ref_low, "unit": lab.unit}
        if lab.ref_high is not None:
            rr["high"] = {"value": lab.ref_high, "unit": lab.unit}
        resource["referenceRange"] = [rr]
    if lab.flag:
        resource["interpretation"] = [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
            "code": lab.flag, "display": lab.flag}]}]
    return resource


class FHIRLabClient:
    def __init__(self, base_url: str = FHIR_LAB_BASE_URL, client=None, token: Optional[str] = None):
        self.base_url = base_url.rstrip("/")
        headers = {"Accept": "application/fhir+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.client = client or httpx.Client(timeout=12.0, headers=headers)

    def fetch_latest(self, patient_id: str, as_of_date: date) -> dict:
        params = {
            "patient": patient_resource_id(patient_id),
            "category": "laboratory",
            "date": f"le{as_of_date.isoformat()}",
            "_sort": "-date",
            "_count": "500",
        }
        response = self.client.get(f"{self.base_url}/Observation", params=params)
        response.raise_for_status()
        bundle = response.json()
        observations = [entry.get("resource", {}) for entry in bundle.get("entry", [])]
        labs = select_latest_observations(observations, as_of_date)
        freshness = panel_freshness(labs, as_of_date)
        return {
            "source": "fhir-server", "server": self.base_url,
            "patient_fhir_id": patient_resource_id(patient_id),
            "labs": labs, **freshness,
        }


def bundled_fhir_labs(cohort, patient_id: str, as_of_date: date,
                      as_of_tx_day: Optional[int] = None) -> dict:
    """FHIR-equivalent fallback built from the same synthetic source rows."""
    rows = [lab for lab in cohort.labs.get(patient_id, [])
            if lab.test in LOINC_LABS
            and "natural course" not in lab.encounter.lower()
            and (as_of_tx_day is None or lab.tx_day <= as_of_tx_day)]
    observations = [lab_to_observation(patient_id, lab) for lab in rows]
    labs = select_latest_observations(observations, as_of_date)
    return {
        "source": "bundled-fhir-fallback", "server": None,
        "patient_fhir_id": patient_resource_id(patient_id),
        "labs": labs, **panel_freshness(labs, as_of_date),
    }
