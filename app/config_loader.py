"""Loads the clinician-reviewed config files.

These YAML files are the SINGLE source of truth for red-flag / soft symptom
terms, the approved follow-up question bank, and the fixed safety messages.
No symptom term or system-emittable text is hardcoded anywhere else.
"""
from __future__ import annotations

import functools
from pathlib import Path

import yaml

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"


def _load(name: str) -> dict:
    with open(CONFIG_DIR / name, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@functools.lru_cache(maxsize=None)
def symptom_rules() -> dict:
    return _load("symptom_rules.yaml")


@functools.lru_cache(maxsize=None)
def question_bank() -> dict:
    return _load("question_bank.yaml")


@functools.lru_cache(maxsize=None)
def safety_messages() -> dict:
    return _load("safety_messages.yaml")


@functools.lru_cache(maxsize=None)
def detection() -> dict:
    return _load("detection.yaml")


def reload_all() -> None:
    """Drop caches so a config edit takes effect without a code change."""
    for fn in (symptom_rules, question_bank, safety_messages, detection):
        fn.cache_clear()


# --- convenience accessors used by the chat engine and its tests ------------

def approved_system_texts() -> set:
    """Every string the system is allowed to emit as a chat turn."""
    texts = {q["text"] for q in question_bank()["questions"]}
    texts |= {m["text"] for m in safety_messages()["safety_messages"]}
    return texts


def safety_message(msg_id: str) -> str:
    for m in safety_messages()["safety_messages"]:
        if m["id"] == msg_id:
            return m["text"]
    raise KeyError(f"unknown safety message id: {msg_id}")


def question_by_id(qid: str) -> dict:
    for q in question_bank()["questions"]:
        if q["id"] == qid:
            return q
    raise KeyError(f"unknown question id: {qid}")
