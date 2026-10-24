"""CMS care-update context card and FHIR Bundle export.

The card follows the CMS care-update fields; the FHIR Bundle carries a
Patient, one Observation per driving signal, and a Communication summarizing the
decision-support update. Everything is tagged synthetic / decision-support so it
can never be mistaken for a diagnosis.
"""
from __future__ import annotations

from datetime import datetime, timezone

# LOINC-ish codes for the wearable signals (approximate; demo only)
_SIGNAL_META = {
    "resting_heart_rate": ("40443-4", "Heart rate --resting", "/min"),
    "hrv_rmssd_milli": ("80404-7", "R-R interval.standard deviation", "ms"),
    "skin_temp_celsius": ("8310-5", "Body temperature", "Cel"),
    "spo2_percentage": ("59408-5", "Oxygen saturation", "%"),
    "respiratory_rate": ("9279-1", "Respiratory rate", "/min"),
    "fluid_cups": ("None", "Fluid intake (self-reported, cups)", "cups"),
}

_SYNTHETIC_TAG = {
    "system": "http://between-visits.example/synthetic",
    "code": "synthetic-decision-support",
    "display": "Synthetic data — decision support only, not a diagnosis",
}


def build_context_card(patient, alert: dict, hospital: dict | None,
                       chat_context: dict | None,
                       clinical_context: dict | None = None) -> dict:
    """Assemble the CMS care-update fields for the dashboard card."""
    return {
        "synthetic": True,
        "decision_support_only": True,
        # CMS care-update: patient identification / demographics
        "patient": {
            "id": patient.patient_id,
            "name": patient.name,
            "age": patient.age,
            "sex": patient.sex,
            "diagnosis": patient.diagnosis,
            "stage": patient.stage,
            "treatment": patient.treatment,
        },
        # CMS care-update: current clinical status / reason for update
        "reason_for_update": {
            "type": alert["type"],
            "severity": alert["severity"],
            "risk_score": alert["risk_score"],
            "tx_day": alert.get("tx_day"),
            "summary": _summary(alert),
        },
        # CMS care-update: relevant observations
        "observations": alert.get("reasons", []),
        # CMS care-update: recommendation (framed as decision support)
        "recommendation": {
            "framing": "decision_support",
            "text": "Review the driving signals against baseline and decide whether to "
                    "contact the patient or direct them to care. The system does not "
                    "diagnose; the clinician makes the clinical decision.",
            "suggested_hospital": hospital,
        },
        # CMS care-update: timing
        "timing": {
            "alert_created_at": alert.get("created_at"),
            "exported_at": _now(),
        },
        "chat_context": chat_context or {},
        # Point-in-time synthetic clinical context, bounded to the alert day.
        # It is display-only and does not participate in alert detection.
        "clinical_context": clinical_context or {},
    }


def _summary(alert: dict) -> str:
    t = alert["type"]
    if t == "dehydration":
        return "Dehydration-pattern drift: resting HR up and HRV down vs baseline over " \
               "several days with falling fluid intake."
    if t == "infection":
        return "Infection-pattern drift: overnight skin-temperature and resting-HR spike " \
               "vs baseline with supporting signals."
    if t == "red_flag_escalation":
        return "Patient reported a red-flag symptom in chat; routed urgently for clinician review."
    return "Signal drift vs baseline."


def build_fhir_bundle(patient, alert: dict, hospital: dict | None) -> dict:
    """A FHIR R4 Bundle (collection) as plain JSON."""
    now = _now()
    patient_res = {
        "resourceType": "Patient",
        "id": patient.patient_id,
        "meta": {"tag": [_SYNTHETIC_TAG]},
        "identifier": [{"system": "http://between-visits.example/patient", "value": patient.patient_id}],
        "name": [{"text": patient.name}],
        "gender": _fhir_gender(patient.sex),
    }
    entries = [{"resource": patient_res}]

    for r in alert.get("reasons", []):
        meta = _SIGNAL_META.get(r.get("signal"))
        if not meta or r.get("current_value") is None:
            continue
        code, display, unit = meta
        entries.append({"resource": {
            "resourceType": "Observation",
            "meta": {"tag": [_SYNTHETIC_TAG]},
            "status": "preliminary",
            "category": [{"coding": [{
                "system": "http://terminology.hl7.org/CodeSystem/observation-category",
                "code": "vital-signs"}]}],
            "code": {"coding": [{"system": "http://loinc.org", "code": code, "display": display}]},
            "subject": {"reference": f"Patient/{patient.patient_id}"},
            "effectiveDateTime": now,
            "valueQuantity": {"value": r["current_value"], "unit": unit},
            "note": [{"text": r.get("text", "")}],
        }})

    comm_text = _summary(alert)
    if hospital and hospital.get("found"):
        comm_text += f" Suggested (clinician confirms): {hospital['name']} ({hospital['distance_km']} km)."
    entries.append({"resource": {
        "resourceType": "Communication",
        "meta": {"tag": [_SYNTHETIC_TAG]},
        "status": "preparation",
        "category": [{"text": "care-update (decision support, not a diagnosis)"}],
        "subject": {"reference": f"Patient/{patient.patient_id}"},
        "sent": now,
        "payload": [{"contentCodeableConcept": {"text": comm_text}}],
    }})

    return {
        "resourceType": "Bundle",
        "meta": {"tag": [_SYNTHETIC_TAG]},
        "type": "collection",
        "timestamp": now,
        "entry": entries,
    }


def _fhir_gender(sex: str) -> str:
    s = (sex or "").strip().upper()
    return {"F": "female", "M": "male"}.get(s, "unknown")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_handoff_communication(patient, alert: dict, fhir_labs: dict) -> dict:
    """Export the nurse handoff as a standalone FHIR R4 Communication."""
    lab_lines = []
    for lab in fhir_labs.get("labs", []):
        flag = f" [{lab['flag']}]" if lab.get("flag") else ""
        trend = (f", {lab['trend']} {abs(lab['change']):g} from prior"
                 if lab.get("change") is not None else "")
        lab_lines.append(
            f"{lab['display']}: {lab['value']} {lab['unit']}{flag} "
            f"({lab['collected_date']}{trend})")
    freshness = fhir_labs.get("warning") or "Latest laboratory panel is within 5 days."
    summary = (
        f"SYNTHETIC DATA — DECISION SUPPORT ONLY. {_summary(alert)} "
        f"Alert risk score {alert['risk_score']}. "
        f"Laboratory context: {'; '.join(lab_lines) if lab_lines else 'none available'}. "
        f"{freshness} Clinician review and decision required."
    )
    about = [{"reference": f"Observation/{lab['observation_id']}"}
             for lab in fhir_labs.get("labs", []) if lab.get("observation_id")]
    resource = {
        "resourceType": "Communication",
        "id": ("handoff-" + alert["id"].lower().replace("_", "-"))[:64],
        "meta": {"tag": [_SYNTHETIC_TAG]},
        "identifier": [{"system": "http://between-visits.example/handoff",
                        "value": alert["id"]}],
        "status": "completed",
        "category": [{"coding": [{
            "system": "http://terminology.hl7.org/CodeSystem/communication-category",
            "code": "notification", "display": "Notification"}],
            "text": "Nurse care handoff"}],
        "priority": "urgent" if alert.get("severity") == "urgent" else "routine",
        "subject": {"reference": f"Patient/{patient.patient_id}",
                    "display": patient.name},
        "sent": _now(),
        "sender": {"display": "OncoPath decision-support service"},
        "recipient": [{"display": "Care-team nurse"}],
        "payload": [{"contentCodeableConcept": {"text": summary}}],
        "note": [{"text": "Synthetic data. This handoff is not a diagnosis; clinician verification required."}],
    }
    if about:
        resource["about"] = about
    return resource
