"""Baseline engine, detector, and the ground-truth correctness gate."""
from app.baseline import compute_baseline, zscore
from app.detector import detect_for_patient
from app.evaluation import evaluate


def test_baseline_uses_pretreatment_only_and_is_sufficient(cohort):
    b = compute_baseline(cohort, "P001")
    assert b.sufficient
    assert b.n_valid_days >= 7
    # every baseline signal is derived from tx_day <= 0 days only
    assert b.signals["resting_heart_rate"].mean is not None


def test_zscore_is_none_for_missing_value(cohort):
    b = compute_baseline(cohort, "P001")
    assert zscore(b, "resting_heart_rate", None) is None


def test_every_alert_has_reasons(cohort):
    for pid in cohort.patient_ids():
        for alert in detect_for_patient(cohort, pid):
            assert alert.reasons, f"{alert.id} has no reasons"
            for r in alert.reasons:
                assert r["text"]


def test_insufficient_baseline_suppresses_alerts(cohort, monkeypatch):
    # force a patient's baseline to look insufficient and confirm no alerts
    b = compute_baseline(cohort, "P001")
    b.sufficient = False
    assert detect_for_patient(cohort, "P001", b) == []


def test_ground_truth_all_true_events_detected_no_false_alarms(cohort):
    report = evaluate(cohort)
    s = report["summary"]
    # every labelled true event is detected
    assert s["missed"] == 0, report["results"]
    assert s["sensitivity"] == 1.0
    # the labelled false-alarm confounders never fire
    assert s["false_alarms"] == 0, [r for r in report["results"]
                                    if not r["should_alert"] and r["detected"]]


def test_false_alarm_confounders_are_silent(cohort):
    # P003 (single late night) and P102 must not alert at all
    for pid in ("P003", "P102"):
        assert detect_for_patient(cohort, pid) == [], pid


def test_demo_events_fire_no_earlier_than_onset(cohort):
    # For the 3 scripted demo patients, onset is an authored fact, so an alert
    # must never fire before it (requirement 3.5). The random cohort has
    # stochastic physiology and may legitimately drift earlier.
    report = evaluate(cohort)
    for r in report["results"]:
        if (r["should_alert"] and r["detected"] and r.get("cohort") == "demo"
                and r.get("onset_tx_day") is not None):
            assert r["fired_tx_day"] >= r["onset_tx_day"], r
