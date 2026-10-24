"""Minimal server-side SMART App Launch (FHIR R4) support.

Supports EHR launch from the SMART Health IT launcher using discovery, OAuth 2.0
authorization code flow, and PKCE. Access tokens stay server-side in memory for
the hackathon demo; only an opaque session id is stored in an HttpOnly cookie.
Production deployment should use an encrypted shared session store.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlencode, urlparse

import httpx


@dataclass
class SmartSession:
    id: str
    issuer: str
    fhir_patient_id: Optional[str]
    local_patient_id: str
    access_token: str
    token_type: str
    expires_at: Optional[float]


class SmartLaunchManager:
    def __init__(self, client=None):
        self.client_id = os.getenv("SMART_CLIENT_ID", "between-visits-demo")
        self.client_secret = os.getenv("SMART_CLIENT_SECRET")
        self.scope = os.getenv(
            "SMART_SCOPES", "launch openid fhirUser patient/*.read")
        self.redirect_uri_override = os.getenv("SMART_REDIRECT_URI")
        self.client = client or httpx.Client(timeout=12.0)
        self._pending: dict[str, dict] = {}
        self._sessions: dict[str, SmartSession] = {}

    def begin(self, base_url: str, issuer: str, launch: str,
              local_patient_id: str) -> str:
        issuer = issuer.rstrip("/")
        self._validate_issuer(issuer)
        discovery = self.client.get(
            issuer + "/.well-known/smart-configuration",
            headers={"Accept": "application/json"})
        discovery.raise_for_status()
        config = discovery.json()
        authorize = config.get("authorization_endpoint")
        token = config.get("token_endpoint")
        if not authorize or not token:
            raise ValueError("SMART discovery is missing OAuth endpoints")
        self._validate_endpoint(authorize, issuer)
        self._validate_endpoint(token, issuer)

        state = secrets.token_urlsafe(24)
        verifier = secrets.token_urlsafe(64)
        challenge = base64.urlsafe_b64encode(
            hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redirect_uri = self.redirect_uri_override or base_url.rstrip("/") + "/smart/callback"
        self._pending[state] = {
            "issuer": issuer, "launch": launch, "verifier": verifier,
            "token_endpoint": token, "redirect_uri": redirect_uri,
            "local_patient_id": local_patient_id, "created_at": time.time(),
        }
        params = {
            "response_type": "code", "client_id": self.client_id,
            "redirect_uri": redirect_uri, "launch": launch,
            "scope": self.scope, "state": state,
            "aud": issuer, "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        return authorize + "?" + urlencode(params)

    def complete(self, code: str, state: str) -> SmartSession:
        pending = self._pending.pop(state, None)
        if not pending or time.time() - pending["created_at"] > 600:
            raise ValueError("SMART launch state is missing or expired")
        form = {
            "grant_type": "authorization_code", "code": code,
            "redirect_uri": pending["redirect_uri"],
            "client_id": self.client_id,
            "code_verifier": pending["verifier"],
        }
        auth = None
        if self.client_secret:
            auth = (self.client_id, self.client_secret)
            form.pop("client_id", None)
        response = self.client.post(
            pending["token_endpoint"], data=form, auth=auth,
            headers={"Accept": "application/json"})
        response.raise_for_status()
        token = response.json()
        if not token.get("access_token"):
            raise ValueError("SMART token response has no access token")
        sid = secrets.token_urlsafe(24)
        expires_in = token.get("expires_in")
        session = SmartSession(
            id=sid, issuer=pending["issuer"],
            fhir_patient_id=token.get("patient"),
            local_patient_id=pending["local_patient_id"],
            access_token=token["access_token"],
            token_type=token.get("token_type", "Bearer"),
            expires_at=(time.time() + int(expires_in)) if expires_in else None,
        )
        self._sessions[sid] = session
        return session

    def get_session(self, sid: Optional[str]) -> Optional[SmartSession]:
        if not sid:
            return None
        session = self._sessions.get(sid)
        if session and session.expires_at and session.expires_at <= time.time():
            self._sessions.pop(sid, None)
            return None
        return session

    def disconnect(self, sid: Optional[str]) -> None:
        if sid:
            self._sessions.pop(sid, None)

    def _validate_issuer(self, issuer: str) -> None:
        parsed = urlparse(issuer)
        host = (parsed.hostname or "").lower()
        allowed = [x.strip().lower() for x in os.getenv(
            "SMART_ALLOWED_HOSTS", "launch.smarthealthit.org").split(",") if x.strip()]
        local = host in ("localhost", "127.0.0.1")
        if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
            raise ValueError("SMART issuer must use HTTPS")
        if not local and not any(host == x or host.endswith("." + x) for x in allowed):
            raise ValueError("SMART issuer host is not allowed")

    def _validate_endpoint(self, endpoint: str, issuer: str) -> None:
        parsed, issuer_parsed = urlparse(endpoint), urlparse(issuer)
        if parsed.scheme != "https" and parsed.hostname not in ("localhost", "127.0.0.1"):
            raise ValueError("SMART OAuth endpoint must use HTTPS")
        # Discovery may use an auth sibling path, but it must stay on the same host.
        if parsed.hostname != issuer_parsed.hostname:
            raise ValueError("SMART OAuth endpoint host differs from issuer")
