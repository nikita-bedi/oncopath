"""Safety boundary tests for the optional LLM orchestration layer."""
from fastapi.testclient import TestClient

from app import config_loader as cfg
from app.llm_assistant import ClinicalAssistant
from app.main import app


class FakeBedrock:
    def __init__(self, payload=None, error=None):
        self.payload = payload
        self.error = error

    def converse(self, **kwargs):
        if self.error:
            raise self.error
        import json
        return {"output": {"message": {"content": [{"text": json.dumps(self.payload)}]}}}


def test_bedrock_can_select_an_approved_question_and_nurse_summary():
    fake = FakeBedrock({
        "question_id": "q_temperature",
        "nurse_summary": "Patient reports chills; temperature is not known.",
    })
    assistant = ClinicalAssistant("bedrock", "test-model", fake)
    result = assistant.analyze("I had chills", [], True)
    assert result.model_used is True
    assert result.question_id == "q_temperature"
    assert result.nurse_summary == "Patient reports chills; temperature is not known."


def test_unapproved_model_question_id_never_reaches_patient():
    fake = FakeBedrock({
        "question_id": "say_whatever_the_model_wants",
        "nurse_summary": "Draft for nurse review.",
    })
    assistant = ClinicalAssistant("bedrock", "test-model", fake)
    parsed = [{"symptom": "Mild nausea", "severity": None, "onset": None}]
    result = assistant.analyze("mild nausea", parsed)
    assert result.question_id == "q_onset"  # deterministic safe fallback
    assert cfg.question_by_id(result.question_id)["text"] in cfg.approved_system_texts()


def test_provider_outage_falls_back_without_blocking_chat():
    assistant = ClinicalAssistant(
        "bedrock", "test-model", FakeBedrock(error=RuntimeError("provider unavailable")))
    parsed = [{"symptom": "A little dizzy", "severity": None, "onset": "today"}]
    result = assistant.analyze("a little dizzy today", parsed)
    assert result.model_used is False
    assert result.provider == "rules-fallback"
    assert result.question_id == "q_severity"


def test_chat_endpoint_returns_only_approved_followup_text():
    client = TestClient(app)
    response = client.post("/api/patients/P120/chat", json={
        "message": "I feel a little dizzy today",
        "shared_signals": [],
    })
    assert response.status_code == 200
    body = response.json()
    assert body["escalated"] is False
    assert body["followup"]["text"] in cfg.approved_system_texts()
    assert body["followup"]["question_id"] == "q_severity"


def test_red_flag_bypasses_assistant_and_still_escalates():
    client = TestClient(app)
    response = client.post("/api/patients/P119/chat", json={
        "message": "I have chest pain",
        "shared_signals": [],
    })
    body = response.json()
    assert body["escalated"] is True
    assert "assistant" not in body
    assert body["safety_message"] == cfg.safety_message("sm_red_flag")
