"""SMART App Launch and FHIR laboratory integration tests."""
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from fhir.resources.communication import Communication
from hypothesis import given, settings
from hypothesis import strategies as st

from app.data_loader import load_cohort
from app.fhir_export import build_handoff_communication
from app.fhir_labs import (
    CODE_TO_NAME, LAB_ORDER, LOINC_LABS, FHIRLabClient, bundled_fhir_labs,
    panel_freshness, select_latest_observations,
)
from app.service import Service
from app.smart import SmartLaunchManager
from scripts.seed_hapi_fhir import seed


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeFHIRHTTP:
    def __init__(self, bundle):
        self.bundle = bundle
        self.last_url = None
        self.last_params = None

    def get(self, url, params=None, **kwargs):
        self.last_url, self.last_params = url, params
        return FakeResponse(self.bundle)


class FakeSmartHTTP:
    def get(self, url, **kwargs):
        return FakeResponse({
            "authorization_endpoint": "https://launch.smarthealthit.org/v/r4/auth/authorize",
            "token_endpoint": "https://launch.smarthealthit.org/v/r4/auth/token",
        })

    def post(self, url, data=None, auth=None, **kwargs):
        assert data["code_verifier"]
        return FakeResponse({"access_token": "synthetic-token", "token_type": "Bearer",
                             "patient": "smart-patient-123", "expires_in": 3600})


def make_observation(code, collected, value, oid, status="final", category="laboratory"):
    return {
        "resourceType": "Observation", "id": oid, "status": status,
        "category": [{"coding": [{"code": category}]}],
        "code": {"coding": [{"system": "http://loinc.org", "code": code}]},
        "effectiveDateTime": collected.isoformat(),
        "valueQuantity": {"value": value, "unit": "mg/dL"},
        "referenceRange": [{"low": {"value": 1, "unit": "mg/dL"},
                            "high": {"value": 9, "unit": "mg/dL"}}],
    }


def test_required_loinc_map_is_exact():
    assert {name: meta["code"] for name, meta in LOINC_LABS.items()} == {
        "sodium": "2951-2", "potassium": "2823-3", "creatinine": "2160-0",
        "bun": "3094-0", "magnesium": "19123-9", "wbc": "6690-2",
        "anc": "751-8", "hemoglobin": "718-7", "platelets": "777-3",
        "albumin": "1751-7",
    }


@settings(max_examples=250, deadline=None)
@given(st.lists(st.tuples(
    st.sampled_from(list(CODE_TO_NAME)),
    st.integers(min_value=0, max_value=30),
    st.floats(min_value=0.1, max_value=300, allow_nan=False, allow_infinity=False),
    st.sampled_from(["final", "amended", "preliminary"]),
    st.sampled_from(["laboratory", "vital-signs"]),
), min_size=1, max_size=80))
def test_every_displayed_lab_is_the_most_recent_observation_for_its_loinc(rows):
    """Required property: no displayed lab can be older than an eligible peer."""
    start = date(2026, 1, 1)
    observations = [make_observation(code, start + timedelta(days=day), value,
                                     f"obs-{i:03d}", status, category)
                    for i, (code, day, value, status, category) in enumerate(rows)]
    displayed = select_latest_observations(observations, date(2026, 2, 1))
    for lab in displayed:
        eligible = [o for o in observations
                    if o["status"] in ("final", "amended", "corrected")
                    and o["category"][0]["coding"][0]["code"] == "laboratory"
                    and o["code"]["coding"][0]["code"] == lab["loinc"]]
        expected = max(eligible,
                       key=lambda o: (date.fromisoformat(o["effectiveDateTime"][:10]),
                                      o.get("meta", {}).get("lastUpdated", ""), o["id"]))
        assert lab["observation_id"] == expected["id"]
        assert lab["value"] == expected["valueQuantity"]["value"]


def test_fhir_client_queries_laboratory_category_and_returns_trend():
    code = LOINC_LABS["sodium"]["code"]
    observations = [
        make_observation(code, date(2026, 10, 1), 137, "old"),
        make_observation(code, date(2026, 10, 4), 133, "new"),
    ]
    fake = FakeFHIRHTTP({"resourceType": "Bundle",
                         "entry": [{"resource": o} for o in observations]})
    result = FHIRLabClient("https://example.test/fhir", fake).fetch_latest(
        "P001", date(2026, 10, 5))
    assert fake.last_params["category"] == "laboratory"
    assert fake.last_params["patient"] == "bv-p001"
    assert result["labs"][0]["observation_id"] == "new"
    assert result["labs"][0]["previous_value"] == 137
    assert result["labs"][0]["trend"] == "down"
    assert result["labs"][0]["change"] == -4


def test_panel_older_than_five_days_has_freshness_warning():
    freshness = panel_freshness(
        [{"collected_date": "2026-10-01"}], date(2026, 10, 7))
    assert freshness["stale"] is True
    assert freshness["age_days"] == 6
    assert "6 days old" in freshness["warning"]
    assert panel_freshness(
        [{"collected_date": "2026-10-02"}], date(2026, 10, 7))["stale"] is False


def test_bundled_fhir_fallback_uses_latest_result_per_loinc_as_of_alert():
    cohort = load_cohort()
    result = bundled_fhir_labs(cohort, "P001", date(2026, 10, 18), as_of_tx_day=19)
    for shown in result["labs"]:
        source = [lab for lab in cohort.labs["P001"]
                  if lab.test == shown["name"] and lab.tx_day <= 19
                  and "natural course" not in lab.encounter.lower()]
        latest = max(source, key=lambda lab: (lab.date, lab.tx_day))
        assert shown["collected_date"] == latest.date.isoformat()
        assert shown["value"] == latest.value


def test_handoff_is_valid_fhir_r4_communication_and_synthetic():
    cohort = load_cohort()
    service = Service()
    service.fhir_lab_remote_enabled = False
    alert = service.store.get_alert("P002-infection")
    labs = service.fhir_lab_context("P002", alert["tx_day"])
    resource = build_handoff_communication(cohort.patients["P002"], alert, labs)
    Communication(**resource)
    assert resource["resourceType"] == "Communication"
    assert resource["meta"]["tag"][0]["code"] == "synthetic-decision-support"
    assert "Clinician review" in resource["payload"][0]["contentCodeableConcept"]["text"]


def test_smart_launch_discovers_authorizes_with_pkce_and_keeps_context():
    manager = SmartLaunchManager(FakeSmartHTTP())
    url = manager.begin("http://localhost:8100",
                        "https://launch.smarthealthit.org/v/r4/fhir",
                        "launch-token", "P001")
    query = parse_qs(urlparse(url).query)
    assert query["launch"] == ["launch-token"]
    assert query["code_challenge_method"] == ["S256"]
    assert "patient/*.read" in query["scope"][0]
    session = manager.complete("auth-code", query["state"][0])
    assert session.fhir_patient_id == "smart-patient-123"
    assert session.local_patient_id == "P001"
    assert manager.get_session(session.id) == session


def test_smart_launch_rejects_unapproved_issuer_before_network_call():
    manager = SmartLaunchManager(FakeSmartHTTP())
    try:
        manager.begin("http://localhost:8100", "https://evil.example/fhir", "x", "P001")
    except ValueError as exc:
        assert "not allowed" in str(exc)
    else:
        raise AssertionError("unapproved SMART issuer was accepted")


def test_hapi_seed_is_repeatable_and_excludes_counterfactual_rows_by_default():
    dry = seed("https://hapi.fhir.org/baseR4", 100, False, dry_run=True)
    assert dry["counts"]["Patient"] == 23
    assert dry["counts"]["Observation"] == 1840


def test_handoff_download_endpoint_returns_communication():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    response = client.get("/api/alerts/P001-dehydration/handoff")
    assert response.status_code == 200
    assert response.json()["resourceType"] == "Communication"
    assert "attachment" in response.headers["content-disposition"]


def test_smart_status_is_disconnected_without_session_cookie():
    from fastapi.testclient import TestClient
    from app.main import app
    response = TestClient(app).get("/api/smart/status")
    assert response.status_code == 200
    assert response.json() == {"connected": False}
