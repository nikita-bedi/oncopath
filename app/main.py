"""FastAPI application: JSON API + two server-rendered surfaces.

Surfaces:
  /patient    -> patient phone view (check-in, status, care-team chat)
  /clinician  -> clinician dashboard (risk-ranked list, context cards)

Every page shows the "synthetic data" label. Decision support only.
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Literal, Optional

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from . import SYNTHETIC_DATA_LABEL, chat as chat_mod
from . import config_loader as cfg
from .evaluation import evaluate
from .llm_assistant import ClinicalAssistant
from .service import Service
from .smart import SmartLaunchManager

BASE = Path(__file__).resolve().parent
app = FastAPI(title="Between Visits", description="Synthetic-data decision-support demo")
templates = Jinja2Templates(directory=str(BASE / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")

svc = Service()
assistant = ClinicalAssistant()
smart = SmartLaunchManager()


# --------------------------------------------------------------------------
# request bodies
# --------------------------------------------------------------------------
class CheckInBody(BaseModel):
    date: Optional[str] = None
    pain_0_10: Optional[float] = None
    swallow_difficulty_0_10: Optional[float] = None
    nausea_0_10: Optional[float] = None
    fatigue_0_10: Optional[float] = None
    fluid_cups: Optional[float] = None
    meals_eaten_pct: Optional[float] = None


class ChatBody(BaseModel):
    message: str
    shared_signals: List[Literal[
        "resting_heart_rate", "hrv_rmssd_milli", "skin_temp_celsius",
        "sleep_performance_percentage", "strain"]] = []
    share_window_days: Optional[Literal[3, 7, 14]] = None


class FollowupBody(BaseModel):
    question_id: str


class FollowupAnswerBody(BaseModel):
    question_id: str
    answer: str


class ActionBody(BaseModel):
    actor: str = "nurse"
    action: str
    note: str = ""


# --------------------------------------------------------------------------
# JSON API
# --------------------------------------------------------------------------
@app.get("/api/patients")
def api_patients():
    return svc.cohort_overview()


@app.get("/api/patients/{pid}")
def api_patient(pid: str):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    p = svc.cohort.patients[pid]
    b = svc.baselines[pid]
    return {
        "patient": {
            "id": p.patient_id, "name": p.name, "age": p.age, "sex": p.sex,
            "diagnosis": p.diagnosis, "stage": p.stage, "treatment": p.treatment,
            "cohort": p.cohort, "story": p.story,
        },
        "baseline": {"sufficient": b.sufficient, "n_valid_days": b.n_valid_days},
        "status": svc.patient_status(pid),
        "clinical": svc.clinical_context(pid),
    }


@app.get("/api/patients/{pid}/timeseries")
def api_timeseries(pid: str, signals: str = "resting_heart_rate,hrv_rmssd_milli"):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    return svc.timeseries(pid, [s.strip() for s in signals.split(",") if s.strip()])


@app.post("/api/patients/{pid}/checkins")
def api_checkin(pid: str, body: CheckInBody):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    # Demo: acknowledge receipt. (Persisting new check-ins and re-running the
    # detector live is out of scope for the synthetic demo.)
    return {"ok": True, "received": body.dict()}


@app.get("/api/alerts")
def api_alerts(status: Optional[str] = None):
    return svc.alerts(status)


@app.get("/api/alerts/{alert_id}")
def api_alert(alert_id: str):
    try:
        return svc.alert_card(alert_id)
    except KeyError:
        raise HTTPException(404, "unknown alert")


@app.post("/api/alerts/{alert_id}/actions")
def api_alert_action(alert_id: str, body: ActionBody):
    try:
        return svc.store.act_on_alert(alert_id, body.actor, body.action, body.note)
    except KeyError:
        raise HTTPException(404, "unknown alert")
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/alerts/{alert_id}/fhir")
def api_fhir(alert_id: str):
    try:
        return JSONResponse(svc.fhir(alert_id))
    except KeyError:
        raise HTTPException(404, "unknown alert")


@app.get("/api/alerts/{alert_id}/handoff")
def api_handoff_communication(alert_id: str):
    try:
        return JSONResponse(
            svc.handoff_communication(alert_id),
            headers={"Content-Disposition":
                     f'attachment; filename="{alert_id}-handoff-communication.json"'})
    except KeyError:
        raise HTTPException(404, "unknown alert")


@app.get("/api/alerts/{alert_id}/hospital")
def api_hospital(alert_id: str):
    try:
        card = svc.alert_card(alert_id)
    except KeyError:
        raise HTTPException(404, "unknown alert")
    return card["hospital"] or {"found": False, "reason": "no escalation for this alert"}


# -- chat -------------------------------------------------------------------
@app.get("/api/patients/{pid}/chat")
def api_chat_thread(pid: str):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    return {"thread": svc.store.thread(pid),
            "banner": cfg.safety_message("sm_expectations_banner")}


@app.post("/api/patients/{pid}/chat")
def api_chat_send(pid: str, body: ChatBody):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    outcome = chat_mod.handle_patient_message(
        svc.store, pid, body.message, body.shared_signals,
        body.share_window_days)

    # Deterministic red flags always win and bypass the model. For other
    # messages, the assistant may choose an approved follow-up question ID.
    if not outcome["escalated"]:
        result = assistant.analyze(
            body.message, outcome["parsed_symptoms"],
            outcome["needs_nurse_review"])
        svc.store.add_assistant_note(
            pid, outcome["turn_id"], result.provider,
            result.nurse_summary, result.question_id)
        outcome["assistant"] = {
            "provider": result.provider,
            "model_used": result.model_used,
            "question_id": result.question_id,
        }
        if result.question_id:
            followup = chat_mod.ask_followup(svc.store, pid, result.question_id)
            outcome["followup"] = followup.to_dict()
    return outcome


@app.post("/api/patients/{pid}/chat/followup")
def api_chat_followup(pid: str, body: FollowupBody):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    try:
        turn = chat_mod.ask_followup(svc.store, pid, body.question_id)
    except KeyError:
        raise HTTPException(400, "unknown question id")
    return turn.to_dict()


@app.post("/api/patients/{pid}/chat/followup/answer")
def api_chat_followup_answer(pid: str, body: FollowupAnswerBody):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    return chat_mod.answer_followup(svc.store, pid, body.question_id, body.answer).to_dict()


@app.get("/api/patients/{pid}/chat/context")
def api_chat_context(pid: str):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    return svc.store.chat_context(pid)


# -- config & eval ----------------------------------------------------------
@app.get("/api/config/question-bank")
def api_question_bank():
    return cfg.question_bank()


@app.get("/api/config/symptom-rules")
def api_symptom_rules():
    return cfg.symptom_rules()


@app.get("/api/assistant/status")
def api_assistant_status():
    return assistant.status()


@app.get("/api/fhir/labs/{pid}")
def api_fhir_labs(pid: str, tx_day: Optional[int] = None):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    return svc.fhir_lab_context(pid, tx_day)


@app.get("/api/smart/status")
def api_smart_status(request: Request):
    session = smart.get_session(request.cookies.get("bv_smart_session"))
    if not session:
        return {"connected": False}
    return {"connected": True, "issuer": session.issuer,
            "fhir_patient_id": session.fhir_patient_id,
            "local_patient_id": session.local_patient_id}


@app.get("/api/eval/ground-truth")
def api_eval():
    return evaluate(svc.cohort)


# --------------------------------------------------------------------------
# SMART App Launch (EHR launch)
# --------------------------------------------------------------------------
@app.get("/smart/setup", response_class=HTMLResponse)
def smart_setup(request: Request):
    base = str(request.base_url).rstrip("/")
    return templates.TemplateResponse(request, "smart_setup.html", {
        "request": request, "label": SYNTHETIC_DATA_LABEL,
        "app_launch_url": base + "/smart/launch?synthetic_patient=P001",
        "redirect_uri": base + "/smart/callback",
        "client_id": smart.client_id,
    })


@app.get("/smart/launch")
def smart_launch(request: Request, iss: str, launch: str,
                 synthetic_patient: str = "P001"):
    if synthetic_patient not in svc.cohort.patients:
        raise HTTPException(400, "unknown synthetic patient mapping")
    try:
        authorize_url = smart.begin(
            str(request.base_url).rstrip("/"), iss, launch, synthetic_patient)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(400, f"SMART launch failed: {exc}")
    return RedirectResponse(authorize_url, status_code=302)


@app.get("/smart/callback")
def smart_callback(request: Request, code: Optional[str] = None,
                   state: Optional[str] = None, error: Optional[str] = None):
    if error:
        raise HTTPException(400, f"SMART authorization failed: {error}")
    if not code or not state:
        raise HTTPException(400, "SMART callback is missing code or state")
    try:
        session = smart.complete(code, state)
    except (ValueError, httpx.HTTPError) as exc:
        raise HTTPException(400, f"SMART token exchange failed: {exc}")
    response = RedirectResponse(
        f"/clinician?smart=connected&patient={session.local_patient_id}", status_code=302)
    response.set_cookie(
        "bv_smart_session", session.id, max_age=3600, httponly=True,
        secure=request.url.scheme == "https", samesite="lax")
    return response


@app.post("/smart/disconnect")
def smart_disconnect(request: Request):
    smart.disconnect(request.cookies.get("bv_smart_session"))
    response = RedirectResponse("/clinician", status_code=303)
    response.delete_cookie("bv_smart_session")
    return response


# --------------------------------------------------------------------------
# Rendered surfaces
# --------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    report = evaluate(svc.cohort)["summary"]
    return templates.TemplateResponse(request, "home.html", {
        "request": request, "label": SYNTHETIC_DATA_LABEL,
        "report": report,
        "demo_patients": [svc.cohort.patients[pid] for pid in ("P001", "P002", "P003")]})


@app.get("/clinician", response_class=HTMLResponse)
def clinician(request: Request):
    overview = svc.cohort_overview()
    smart_session = smart.get_session(request.cookies.get("bv_smart_session"))
    alerts = svc.alerts()
    active = [a for a in alerts if a["status"] in ("open", "acknowledged", "in_progress")]
    overdue = {a["id"] for a in active if a.get("overdue")}
    stats = {
        "patients": len(overview),
        "active": len(active),
        "urgent": sum(1 for a in active if a["severity"] == "urgent"),
        "in_progress": sum(1 for a in active if a["status"] in ("acknowledged", "in_progress")),
        "resolved": sum(1 for a in alerts if a["status"] in ("resolved", "dismissed")),
    }
    return templates.TemplateResponse(request, "clinician.html", {
        "request": request, "label": SYNTHETIC_DATA_LABEL,
        "overview": overview, "alerts": alerts, "active": active,
        "stats": stats, "overdue": overdue,
        "assistant_status": assistant.status(),
        "smart_session": smart_session})


@app.get("/clinician/{alert_id}", response_class=HTMLResponse)
def clinician_card(request: Request, alert_id: str):
    try:
        data = svc.alert_card(alert_id)
    except KeyError:
        raise HTTPException(404, "unknown alert")
    pid = data["alert"]["patient_id"]
    sigs = sorted({r["signal"] for r in data["alert"]["reasons"]
                   if r["signal"] in ("resting_heart_rate", "hrv_rmssd_milli",
                                       "skin_temp_celsius", "spo2_percentage",
                                       "respiratory_rate")})
    ts = svc.timeseries(pid, sigs) if sigs else {"signals": {}}
    return templates.TemplateResponse(request, "card.html", {
        "request": request, "label": SYNTHETIC_DATA_LABEL,
        "data": data, "timeseries": ts, "patient": svc.cohort.patients[pid],
        "smart_session": smart.get_session(request.cookies.get("bv_smart_session"))})


@app.get("/patient", response_class=HTMLResponse)
def patient_pick(request: Request):
    return templates.TemplateResponse(request, "patient_pick.html", {
        "request": request, "label": SYNTHETIC_DATA_LABEL,
        "patients": [{"id": p.patient_id, "name": p.name, "cohort": p.cohort,
                      "diagnosis": p.diagnosis, "story": p.story,
                      "status": svc.patient_status(p.patient_id)}
                     for p in svc.cohort.patients.values()]})


@app.get("/patient/{pid}", response_class=HTMLResponse)
def patient_view(request: Request, pid: str):
    if pid not in svc.cohort.patients:
        raise HTTPException(404, "unknown patient")
    p = svc.cohort.patients[pid]
    status = svc.patient_status(pid)
    recovery = [r for r in svc.cohort.recovery.get(pid, []) if r.tx_day is not None]
    sleep = [s for s in svc.cohort.sleep.get(pid, []) if s.tx_day is not None]
    cycles = [c for c in svc.cohort.cycle.get(pid, []) if c.tx_day is not None]
    latest = max(recovery, key=lambda r: r.date) if recovery else None
    latest_sleep = max(sleep, key=lambda s: s.date) if sleep else None
    latest_cycle = max(cycles, key=lambda c: c.date) if cycles else None
    latest_tx_day = latest.tx_day if latest and latest.tx_day > 0 else 0
    baseline = svc.baselines[pid]
    return templates.TemplateResponse(request, "patient.html", {
        "request": request, "label": SYNTHETIC_DATA_LABEL,
        "patient": p, "status": status,
        "latest": latest, "latest_sleep": latest_sleep, "latest_cycle": latest_cycle,
        "treatment_week": max(1, (latest_tx_day + 6) // 7) if latest_tx_day else 0,
        "baseline_days": baseline.n_valid_days,
        "banner": cfg.safety_message("sm_expectations_banner"),
        "received_message": cfg.safety_message("sm_received"),
        "assistant_status": assistant.status(),
        "questions": cfg.question_bank()["questions"],
        "thread": svc.store.thread(pid)})
