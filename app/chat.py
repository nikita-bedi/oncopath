"""Care-team chat: symptom classification, red-flag escalation, free-text
parsing, and the scripted follow-up engine.

Hard safety invariants (covered by property tests):
  1. Any message containing a red-flag term always escalates urgently and the
     patient receives the exact fixed safety message, regardless of wearables.
  2. A soft symptom alone never produces an alert/escalation.
  3. Every system turn's text is byte-identical to an approved question-bank
     entry or a fixed safety message — nothing else is ever emitted.

All symptom terms and system-emittable text come from the config files; none is
hardcoded here.
"""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from . import config_loader as cfg
from .models import Alert, ChatTurn, ParsedSymptom


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _norm(text: str) -> str:
    return " " + re.sub(r"\s+", " ", text.lower().strip()) + " "


def _negated(text_norm: str, phrase: str) -> bool:
    """True if the phrase appears only in a negated context (e.g. 'no fever')."""
    negations = cfg.symptom_rules().get("negations", [])
    idx = text_norm.find(phrase)
    while idx != -1:
        prefix = text_norm[:idx]
        # look at the handful of chars immediately before the phrase
        tail = prefix[-20:]
        if not any(neg.strip() and tail.rstrip().endswith(neg.strip()) for neg in negations):
            # also treat "<neg> ... <phrase>" within a short span as negated
            if not any(neg in tail for neg in negations):
                return False  # found a non-negated occurrence
        idx = text_norm.find(phrase, idx + 1)
    return True  # every occurrence was negated


def classify(message: str) -> dict:
    """Classify a patient message against the clinician-reviewed rules.

    Returns {red_flags: [rule...], soft: [rule...]}. Red-flag detection wins.
    """
    rules = cfg.symptom_rules()
    tn = _norm(message)

    def hits(entries, apply_negation):
        found = []
        for rule in entries:
            for phrase in rule["match"]:
                p = phrase.lower()
                if p not in tn:
                    continue
                # Negation is only applied to soft symptoms. For red flags we
                # deliberately never suppress a match: a missed red flag is far
                # more dangerous than an occasional extra escalation that a
                # clinician reviews anyway ("fail safe, not silent").
                if apply_negation and _negated(tn, p):
                    continue
                found.append({"id": rule["id"], "label": rule["label"], "match": phrase})
                break
        return found

    return {
        "red_flags": hits(rules["red_flags"], apply_negation=False),
        "soft": hits(rules["soft_symptoms"], apply_negation=True),
    }


# ---------------------------------------------------------------------------
# Free-text parser -> structured symptom fields for the nurse card
# ---------------------------------------------------------------------------

_SEVERITY_RE = re.compile(r"\b(\d{1,2})\s*(?:/|out of)\s*10\b")
_ONSET_PATTERNS = [
    ("today", "today"), ("this morning", "today"), ("tonight", "today"),
    ("last night", "last night"), ("yesterday", "yesterday"),
    ("couple of days", "a few days ago"), ("few days", "a few days ago"),
    ("for days", "a few days ago"), ("this week", "this week"),
]


def parse_symptoms(message: str, classification: dict) -> list[ParsedSymptom]:
    tn = message.lower()
    sev = None
    m = _SEVERITY_RE.search(tn)
    if m:
        try:
            sev = max(0, min(10, int(m.group(1))))
        except ValueError:
            sev = None
    onset = None
    for needle, label in _ONSET_PATTERNS:
        if needle in tn:
            onset = label
            break

    out: list[ParsedSymptom] = []
    for rf in classification["red_flags"]:
        out.append(ParsedSymptom(rf["label"], sev, onset, "red_flag",
                                 confidence=0.95, needs_nurse_review=False))
    for ss in classification["soft"]:
        # low confidence when we could not pin severity or onset for a soft symptom
        conf = 0.8 if (sev is not None or onset is not None) else 0.5
        out.append(ParsedSymptom(ss["label"], sev, onset, "soft",
                                 confidence=conf,
                                 needs_nurse_review=conf < 0.6))
    return out


# ---------------------------------------------------------------------------
# Message handling
# ---------------------------------------------------------------------------

def handle_patient_message(store, patient_id: str, message: str,
                           shared_signals: list | None = None,
                           share_window_days: int | None = None) -> dict:
    """Process an inbound patient message.

    Returns a dict describing the outcome, including any fixed safety message
    the patient should see and whether an urgent escalation was created.
    """
    shared_signals = shared_signals or []
    classification = classify(message)
    parsed = parse_symptoms(message, classification)
    needs_review = any(p.needs_nurse_review for p in parsed) or (
        not classification["red_flags"] and not classification["soft"]
        and len(message.strip()) > 0 and _looks_symptomatic(message)
    )

    # record the patient's turn
    turn = ChatTurn(
        id=str(uuid.uuid4()), patient_id=patient_id, author="patient",
        kind="symptom_report" if parsed else "free_text", text=message,
        created_at=_now(), parsed_symptoms=[p.__dict__ for p in parsed],
        shared_signals=shared_signals,
        share_window_days=share_window_days if shared_signals else None,
        needs_nurse_review=needs_review,
    )
    store.add_turn(turn)

    outcome = {
        "classification": classification,
        "parsed_symptoms": [p.__dict__ for p in parsed],
        "escalated": False,
        "safety_message": None,
        "needs_nurse_review": needs_review,
        "turn_id": turn.id,
    }

    # RED FLAG -> immediate urgent escalation + fixed safety message (always)
    if classification["red_flags"]:
        safety_text = cfg.safety_message("sm_red_flag")
        alert = Alert(
            id=f"{patient_id}-redflag-{turn.id[:8]}",
            patient_id=patient_id,
            type="red_flag_escalation",
            severity="urgent",
            risk_score=100.0,
            created_at=_now(),
            tx_day=None,
            source="chat_red_flag",
            reasons=[{
                "signal": "patient_report",
                "current_value": None, "baseline_value": None, "deviation": None,
                "window": "message",
                "text": "Patient reported a red-flag symptom: "
                        + ", ".join(rf["label"] for rf in classification["red_flags"]),
            }],
        )
        store.upsert_alert(alert)
        # system turn: the fixed safety message, verbatim
        store.add_turn(ChatTurn(
            id=str(uuid.uuid4()), patient_id=patient_id, author="system",
            kind="safety_message", text=safety_text, created_at=_now(),
        ))
        outcome["escalated"] = True
        outcome["safety_message"] = safety_text
        outcome["alert_id"] = alert.id

    return outcome


def _looks_symptomatic(message: str) -> bool:
    """Heuristic: a non-trivial message that mentions feeling/symptom words but
    did not match any rule -> flag for a nurse rather than silently dropping."""
    cues = ["feel", "pain", "hurt", "sick", "dizzy", "weak", "can't", "cannot",
            "vomit", "throw", "temperature", "worse"]
    low = message.lower()
    return any(cue in low for cue in cues)


def ask_followup(store, patient_id: str, question_id: str) -> ChatTurn:
    """Emit a scripted follow-up. The text is the EXACT approved question only."""
    q = cfg.question_by_id(question_id)   # raises if unknown -> never off-script
    turn = ChatTurn(
        id=str(uuid.uuid4()), patient_id=patient_id, author="system",
        kind="system_question", text=q["text"], created_at=_now(),
        question_id=question_id,
    )
    store.add_turn(turn)
    return turn


def answer_followup(store, patient_id: str, question_id: str, answer: str) -> ChatTurn:
    turn = ChatTurn(
        id=str(uuid.uuid4()), patient_id=patient_id, author="patient",
        kind="free_text", text=answer, created_at=_now(), question_id=question_id,
    )
    store.add_turn(turn)
    return turn
