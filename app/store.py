"""SQLite-backed persistence for alert lifecycle, chat turns, and escalations.

Only mutable workflow state lives here. The synthetic cohort itself is loaded
read-only into memory by data_loader. This keeps the demo reproducible: delete
the db file to reset.
"""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

LOCAL_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "between_visits.db"
VERCEL_DB_PATH = Path("/tmp/between_visits.db")


def default_db_path() -> Path:
    """Use writable ephemeral storage in Vercel Functions."""
    return VERCEL_DB_PATH if os.getenv("VERCEL") else LOCAL_DB_PATH


DB_PATH = default_db_path()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class Store:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else default_db_path()
        self.conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self):
        c = self.conn
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                type TEXT NOT NULL,
                severity TEXT NOT NULL,
                risk_score REAL NOT NULL,
                created_at TEXT NOT NULL,
                tx_day INTEGER,
                status TEXT NOT NULL DEFAULT 'open',
                source TEXT NOT NULL DEFAULT 'detector',
                reasons_json TEXT NOT NULL DEFAULT '[]'
            );
            CREATE TABLE IF NOT EXISTS alert_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                alert_id TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                note TEXT,
                at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS chat_turns (
                id TEXT PRIMARY KEY,
                patient_id TEXT NOT NULL,
                author TEXT NOT NULL,
                kind TEXT NOT NULL,
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                question_id TEXT,
                parsed_json TEXT NOT NULL DEFAULT '[]',
                shared_json TEXT NOT NULL DEFAULT '[]',
                share_window_days INTEGER,
                needs_nurse_review INTEGER NOT NULL DEFAULT 0,
                language TEXT NOT NULL DEFAULT 'en',
                input_mode TEXT NOT NULL DEFAULT 'typed'
            );
            CREATE TABLE IF NOT EXISTS assistant_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id TEXT NOT NULL,
                turn_id TEXT NOT NULL,
                provider TEXT NOT NULL,
                summary TEXT NOT NULL,
                question_id TEXT,
                created_at TEXT NOT NULL
            );
            """
        )
        # Lightweight migration for demo databases created before scoped
        # sharing gained an explicit time window.
        columns = {r[1] for r in c.execute("PRAGMA table_info(chat_turns)").fetchall()}
        if "share_window_days" not in columns:
            c.execute("ALTER TABLE chat_turns ADD COLUMN share_window_days INTEGER")
        if "language" not in columns:
            c.execute("ALTER TABLE chat_turns ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
        if "input_mode" not in columns:
            c.execute("ALTER TABLE chat_turns ADD COLUMN input_mode TEXT NOT NULL DEFAULT 'typed'")
        c.commit()

    # -- alerts --------------------------------------------------------------
    def upsert_alert(self, alert) -> None:
        self.conn.execute(
            """INSERT INTO alerts (id, patient_id, type, severity, risk_score,
                   created_at, tx_day, status, source, reasons_json)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   severity=excluded.severity, risk_score=excluded.risk_score,
                   reasons_json=excluded.reasons_json""",
            (alert.id, alert.patient_id, alert.type, alert.severity, alert.risk_score,
             alert.created_at, alert.tx_day, alert.status, alert.source,
             json.dumps(alert.reasons)),
        )
        self.conn.commit()

    def get_alert(self, alert_id: str) -> Optional[dict]:
        row = self.conn.execute("SELECT * FROM alerts WHERE id=?", (alert_id,)).fetchone()
        if not row:
            return None
        d = dict(row)
        d["reasons"] = json.loads(d.pop("reasons_json"))
        d["actions"] = self._actions(alert_id)
        return d

    def _actions(self, alert_id: str) -> list:
        rows = self.conn.execute(
            "SELECT actor, action, note, at FROM alert_actions WHERE alert_id=? ORDER BY id",
            (alert_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_alerts(self, status: Optional[str] = None) -> list:
        if status:
            rows = self.conn.execute("SELECT * FROM alerts WHERE status=?", (status,)).fetchall()
        else:
            rows = self.conn.execute("SELECT * FROM alerts").fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["reasons"] = json.loads(d.pop("reasons_json"))
            d["actions"] = self._actions(d["id"])
            out.append(d)
        out.sort(key=lambda a: a["risk_score"], reverse=True)
        return out

    _TRANSITIONS = {
        "open": {"acknowledged", "dismissed"},
        "acknowledged": {"in_progress", "dismissed"},
        "in_progress": {"resolved", "dismissed"},
        "resolved": set(),
        "dismissed": set(),
    }
    _ACTION_TO_STATUS = {
        "acknowledge": "acknowledged",
        "start": "in_progress",
        "resolve": "resolved",
        "dismiss": "dismissed",
    }

    def act_on_alert(self, alert_id: str, actor: str, action: str, note: str = "") -> dict:
        alert = self.get_alert(alert_id)
        if not alert:
            raise KeyError(alert_id)
        new_status = self._ACTION_TO_STATUS.get(action)
        if new_status is None:
            raise ValueError(f"unknown action: {action}")
        if new_status not in self._TRANSITIONS[alert["status"]]:
            raise ValueError(f"cannot {action} from {alert['status']}")
        self.conn.execute("UPDATE alerts SET status=? WHERE id=?", (new_status, alert_id))
        self.conn.execute(
            "INSERT INTO alert_actions (alert_id, actor, action, note, at) VALUES (?,?,?,?,?)",
            (alert_id, actor, action, note, _now()))
        self.conn.commit()
        return self.get_alert(alert_id)

    def overdue_ids(self, hours: int) -> set:
        rows = self.conn.execute(
            "SELECT id, created_at FROM alerts WHERE status IN ('open','acknowledged')").fetchall()
        out = set()
        now = datetime.now(timezone.utc)
        for r in rows:
            try:
                created = datetime.fromisoformat(r["created_at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            if created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if (now - created).total_seconds() / 3600 >= hours:
                out.add(r["id"])
        return out

    # -- chat ----------------------------------------------------------------
    def add_turn(self, turn) -> None:
        self.conn.execute(
            """INSERT INTO chat_turns (id, patient_id, author, kind, text, created_at,
                   question_id, parsed_json, shared_json, share_window_days,
                   needs_nurse_review, language, input_mode)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (turn.id, turn.patient_id, turn.author, turn.kind, turn.text, turn.created_at,
             turn.question_id, json.dumps(turn.parsed_symptoms),
             json.dumps(turn.shared_signals), turn.share_window_days,
             int(turn.needs_nurse_review), turn.language, turn.input_mode))
        self.conn.commit()

    def thread(self, patient_id: str) -> list:
        rows = self.conn.execute(
            "SELECT * FROM chat_turns WHERE patient_id=? ORDER BY created_at, id",
            (patient_id,)).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            d["parsed_symptoms"] = json.loads(d.pop("parsed_json"))
            d["shared_signals"] = json.loads(d.pop("shared_json"))
            d["needs_nurse_review"] = bool(d["needs_nurse_review"])
            out.append(d)
        return out

    def add_assistant_note(self, patient_id: str, turn_id: str, provider: str,
                           summary: str, question_id: Optional[str]) -> None:
        self.conn.execute(
            """INSERT INTO assistant_notes
                   (patient_id, turn_id, provider, summary, question_id, created_at)
               VALUES (?,?,?,?,?,?)""",
            (patient_id, turn_id, provider, summary, question_id, _now()))
        self.conn.commit()

    def assistant_notes(self, patient_id: str) -> list:
        rows = self.conn.execute(
            """SELECT turn_id, provider, summary, question_id, created_at
               FROM assistant_notes WHERE patient_id=? ORDER BY id DESC""",
            (patient_id,)).fetchall()
        return [dict(r) for r in rows]

    def chat_context(self, patient_id: str) -> dict:
        turns = self.thread(patient_id)
        symptoms, answers, review, shared_context, patient_messages = [], [], [], [], []
        for turn in turns:
            symptoms.extend(turn["parsed_symptoms"])
            if turn["author"] == "patient" and turn["text"]:
                patient_messages.append({
                    "turn_id": turn["id"], "text": turn["text"],
                    "language": turn.get("language", "en"),
                    "input_mode": turn.get("input_mode", "typed"),
                    "created_at": turn["created_at"],
                })
            if turn["shared_signals"]:
                shared_context.append({
                    "turn_id": turn["id"], "signals": turn["shared_signals"],
                    "window_days": turn.get("share_window_days"),
                    "created_at": turn["created_at"],
                })
            if turn["author"] == "patient" and turn["question_id"]:
                answers.append({"question_id": turn["question_id"], "answer": turn["text"]})
            if turn["needs_nurse_review"]:
                review.append({"turn_id": turn["id"], "text": turn["text"]})
        return {"symptoms": symptoms, "followup_answers": answers,
                "flagged_for_review": review,
                "shared_context": shared_context,
                "patient_messages": patient_messages,
                "assistant_notes": self.assistant_notes(patient_id)}
