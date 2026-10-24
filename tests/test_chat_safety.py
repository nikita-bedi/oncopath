"""Property-based safety tests for the care-team chat — the critical gate.

  1. Any message containing a red-flag term always escalates urgently and the
     patient receives the exact fixed safety message.
  2. A soft symptom alone never produces an alert/escalation.
  3. Every system turn the engine can emit is byte-identical to an approved
     question-bank entry or a fixed safety message — never off-script.
"""
import tempfile
import uuid
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app import chat as chat_mod
from app import config_loader as cfg
from app.store import Store


def _fresh_store() -> Store:
    """A brand-new store per call, so Hypothesis examples don't share state.

    (A pytest fixture is function-scoped and would be reused across generated
    inputs, which Hypothesis rightly warns against.)
    """
    tmp = Path(tempfile.mkdtemp()) / f"{uuid.uuid4().hex}.db"
    return Store(tmp)


# all configured match phrases
_RED_FLAG_PHRASES = [p for rule in cfg.symptom_rules()["red_flags"] for p in rule["match"]]
_SOFT_PHRASES = [p for rule in cfg.symptom_rules()["soft_symptoms"] for p in rule["match"]]
_FILLER = st.text(alphabet="abcdefghijklmnopqrstuvwxyz ,.", min_size=0, max_size=40)


@settings(max_examples=200, deadline=None)
@given(phrase=st.sampled_from(_RED_FLAG_PHRASES), pre=_FILLER, post=_FILLER)
def test_red_flag_always_escalates(phrase, pre, post):
    store = _fresh_store()
    msg = f"{pre} {phrase} {post}"
    out = chat_mod.handle_patient_message(store, "PX", msg)
    assert out["escalated"] is True
    # exact fixed safety message, verbatim
    assert out["safety_message"] == cfg.safety_message("sm_red_flag")


@settings(max_examples=200, deadline=None)
@given(phrase=st.sampled_from(_SOFT_PHRASES), pre=_FILLER, post=_FILLER)
def test_soft_symptom_alone_never_escalates(phrase, pre, post):
    store = _fresh_store()
    # build a message that contains the soft phrase but NO red-flag phrase
    msg = f"{pre} {phrase} {post}"
    classification = chat_mod.classify(msg)
    # only run the assertion when the message is genuinely soft-only
    if classification["red_flags"]:
        return
    out = chat_mod.handle_patient_message(store, "PY", msg)
    assert out["escalated"] is False
    assert out["safety_message"] is None


@settings(max_examples=100, deadline=None)
@given(
    msgs=st.lists(st.text(min_size=0, max_size=60), min_size=1, max_size=5),
    qids=st.lists(st.sampled_from([q["id"] for q in cfg.question_bank()["questions"]]),
                  min_size=0, max_size=5),
)
def test_system_never_speaks_off_script(msgs, qids):
    store = _fresh_store()
    approved = cfg.approved_system_texts()
    for m in msgs:
        chat_mod.handle_patient_message(store, "PZ", m)
    for qid in qids:
        chat_mod.ask_followup(store, "PZ", qid)
    # every system turn in the thread must be an approved string
    for turn in store.thread("PZ"):
        if turn["author"] == "system":
            assert turn["text"] in approved, f"off-script system turn: {turn['text']!r}"


def test_unknown_question_id_cannot_produce_a_turn():
    store = _fresh_store()
    before = len([t for t in store.thread("PQ") if t["author"] == "system"])
    with pytest.raises(KeyError):
        chat_mod.ask_followup(store, "PQ", "q_not_a_real_question")
    after = len([t for t in store.thread("PQ") if t["author"] == "system"])
    assert after == before  # nothing emitted


_WEARABLE_SIGNALS = ["resting_heart_rate", "hrv_rmssd_milli",
                     "skin_temp_celsius", "sleep_performance_percentage", "strain"]


@settings(max_examples=80, deadline=None)
@given(signals=st.sets(st.sampled_from(_WEARABLE_SIGNALS), max_size=5),
       window=st.sampled_from([3, 7, 14]))
def test_per_message_wearable_scope_persists_exact_selection(signals, window):
    store = _fresh_store()
    chat_mod.handle_patient_message(
        store, "PS", "Here is my daily update", sorted(signals), window)
    turn = store.thread("PS")[-1]
    assert turn["shared_signals"] == sorted(signals)
    # A window has no meaning and must not persist when no signal was approved.
    assert turn["share_window_days"] == (window if signals else None)
    context = store.chat_context("PS")
    if signals:
        assert context["shared_context"][-1]["signals"] == sorted(signals)
        assert context["shared_context"][-1]["window_days"] == window
    else:
        assert context["shared_context"] == []
