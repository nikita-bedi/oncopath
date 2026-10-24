"""Smoke coverage for the hackathon presentation surfaces.

These tests protect the visible product story as the UI evolves: the synthetic-data
label, demo outcomes, risk queue, explained alert, patient check-in, and chat safety
notice must remain present.
"""
from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _page(path: str) -> str:
    response = client.get(path)
    assert response.status_code == 200
    assert "Synthetic data" in response.text
    return response.text


def test_landing_page_tells_the_closed_loop_story():
    html = _page("/")
    assert "Catch the drift" in html
    assert "13/13" in html
    assert "0</strong><span>false-alarm nights escalated" in html
    assert "Maria drinks less" in html
    assert "From signal to completed care" in html


def test_clinician_dashboard_has_priority_queue_and_full_cohort():
    html = _page("/clinician")
    assert "Care command center" in html
    assert "Priority queue" in html
    assert "Needs review" in html
    assert "All patients" in html
    assert "Search patient or diagnosis" in html


def test_alert_card_keeps_reasons_baseline_and_human_action_visible():
    html = _page("/clinician/P002-infection")
    assert "Why this surfaced" in html
    assert "Personal baseline" in html
    assert "Decision support, not a diagnosis" in html
    assert "Closed-loop care" in html
    assert "Clinician action required" in html
    assert "Export FHIR Bundle" in html
    assert "Download nurse handoff" in html
    assert "FHIR R4 Observations" in html
    assert "TNM stage" in html
    assert "T3N2bM0" in html


def test_patient_view_has_checkin_chat_and_expectations_banner():
    html = _page("/patient/P001")
    assert "Daily check-in" in html
    assert "Message your care team" in html
    assert "not monitored 24/7" in html
    assert "Choose wearable data to share with this message" in html
    assert "Your data, your choice" in html
    assert 'value="resting_heart_rate"' in html
    assert 'value="hrv_rmssd_milli"' in html
    assert 'value="skin_temp_celsius"' in html
    assert 'value="sleep_performance_percentage"' in html
    assert 'value="strain"' in html
    assert "Selection resets after this message" in html
    assert "Try red flag" in html
    assert "This app does not diagnose" in html


def test_patient_story_picker_highlights_all_three_demo_cases():
    html = _page("/patient")
    assert "Dehydration story" in html
    assert "Fever story" in html
    assert "False-alarm control" in html


def test_smart_setup_and_fhir_lab_ui_are_visible():
    setup = _page("/smart/setup")
    assert "SMART Health IT launcher" in setup
    assert "/smart/launch?synthetic_patient=P001" in setup
    assert "/smart/callback" in setup

    card = _page("/clinician/P002-infection")
    assert "FHIR R4 Observations" in card
    assert "Latest result for each mapped LOINC code" in card
    assert "Download nurse handoff" in card
    assert "Launch from EHR" in card


def test_chat_api_persists_visible_wearable_consent_and_rejects_unknown_fields():
    response = client.post("/api/patients/P118/chat", json={
        "message": "Here is an update for my care team",
        "shared_signals": ["resting_heart_rate", "strain"],
        "share_window_days": 7,
    })
    assert response.status_code == 200
    thread = client.get("/api/patients/P118/chat").json()["thread"]
    patient_turn = [turn for turn in thread if turn["author"] == "patient"][-1]
    assert patient_turn["shared_signals"] == ["resting_heart_rate", "strain"]
    assert patient_turn["share_window_days"] == 7

    rejected = client.post("/api/patients/P118/chat", json={
        "message": "bad client input",
        "shared_signals": ["entire_medical_record"],
        "share_window_days": 365,
    })
    assert rejected.status_code == 422


def test_render_blueprint_uses_free_plan_and_no_paid_model():
    from pathlib import Path
    import yaml
    from scripts.deploy_render import service_payload
    blueprint = yaml.safe_load(
        (Path(__file__).resolve().parent.parent / "render.yaml").read_text())
    service = blueprint["services"][0]
    assert service["type"] == "web"
    assert service["runtime"] == "python"
    assert service["plan"] == "free"
    assert service["autoDeployTrigger"] == "off"
    env = {item["key"]: item["value"] for item in service["envVars"]}
    assert env["BETWEEN_VISITS_LLM_PROVIDER"] == "rules"
    assert "$PORT" in service["startCommand"]

    api_payload = service_payload("own-test")
    assert api_payload["ownerId"] == "own-test"
    assert api_payload["serviceDetails"]["plan"] == "free"
    assert api_payload["serviceDetails"]["runtime"] == "python"
    assert api_payload["autoDeployTrigger"] == "off"
    api_env = {item["key"]: item["value"] for item in api_payload["envVars"]}
    assert api_env["BETWEEN_VISITS_LLM_PROVIDER"] == "rules"


def test_github_workflow_tests_before_using_secret_deploy_hook():
    from pathlib import Path
    import yaml
    root = Path(__file__).resolve().parent.parent
    workflow = yaml.safe_load(
        (root / ".github/workflows/test-and-deploy.yml").read_text())
    assert workflow["on"]["push"]["branches"] == ["main"]
    assert "workflow_dispatch" in workflow["on"]
    assert workflow["jobs"]["deploy"]["needs"] == "test"
    deploy_env = workflow["jobs"]["deploy"]["env"]
    assert "secrets.RENDER_DEPLOY_HOOK_URL" in deploy_env["RENDER_DEPLOY_HOOK_URL"]
    steps = workflow["jobs"]["deploy"]["steps"]
    deploy_step = next(step for step in steps if step["name"] == "Deploy the exact tested commit")
    assert "ref=${GITHUB_SHA}" in deploy_step["run"]
    assert "RENDER_DEPLOY_HOOK_URL" in deploy_step["run"]
