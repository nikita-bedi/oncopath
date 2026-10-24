"""Config-source, FHIR validation, and hospital locator tests."""
import tempfile
from pathlib import Path

import pytest
import yaml
from fhir.resources.bundle import Bundle

from app import chat as chat_mod
from app import config_loader as cfg
from app.data_loader import load_cohort
from app.detector import detect_for_patient
from app.fhir_export import build_fhir_bundle
from app.hospitals import nearest_suitable


def test_red_flag_behavior_is_config_driven(tmp_path, monkeypatch):
    """Changing the config file changes behavior with no code edit."""
    # a term that is NOT normally a red flag
    assert not chat_mod.classify("my left pinky itches")["red_flags"]

    # point the loader at a temp config that adds the itch as a red flag
    rules = cfg.symptom_rules()
    import copy
    patched = copy.deepcopy(rules)
    patched["red_flags"].append({"id": "rf_test", "label": "Test",
                                 "match": ["pinky itches"]})
    monkeypatch.setattr(cfg, "symptom_rules", lambda: patched)

    assert chat_mod.classify("my left pinky itches")["red_flags"], \
        "behavior should follow the (patched) config, proving no hardcoding"


def test_fhir_bundle_is_valid_and_tagged_synthetic():
    cohort = load_cohort()
    p = cohort.patients["P002"]
    alert = detect_for_patient(cohort, "P002")[0].to_dict()
    hospital = nearest_suitable(p.lat, p.lon)
    bundle = build_fhir_bundle(p, alert, hospital)
    # validates against FHIR R4
    Bundle(**bundle)
    # carries the synthetic / decision-support tag
    assert bundle["meta"]["tag"][0]["code"] == "synthetic-decision-support"
    assert any(e["resource"]["resourceType"] == "Patient" for e in bundle["entry"])
    assert any(e["resource"]["resourceType"] == "Observation" for e in bundle["entry"])


def test_hospital_nearest_found_in_massachusetts():
    # Boston-ish coordinates -> should find a nearby ED hospital
    res = nearest_suitable(42.36, -71.06)
    assert res["found"] is True
    assert res["distance_km"] >= 0
    assert "Suggestion only" in res["note"]


def test_hospital_none_within_range():
    # far from Massachusetts -> explicit none-within-range, not a misleading result
    res = nearest_suitable(0.0, 0.0)
    assert res["found"] is False
    assert "within" in res["reason"]


def test_approved_system_texts_cover_questions_and_safety():
    approved = cfg.approved_system_texts()
    for q in cfg.question_bank()["questions"]:
        assert q["text"] in approved
    for m in cfg.safety_messages()["safety_messages"]:
        assert m["text"] in approved
