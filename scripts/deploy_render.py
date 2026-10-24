#!/usr/bin/env python3
"""Create or redeploy the free Between Visits Render web service.

Reads RENDER_API_KEY from the process environment. The key is never written or
printed. Optionally set RENDER_OWNER_ID, RENDER_SERVICE_NAME, or RENDER_REGION.
"""
from __future__ import annotations

import os
import sys
import time

import httpx

API = "https://api.render.com/v1"
REPO = "https://github.com/nikita-bedi/between-visits"


def service_payload(owner_id: str) -> dict:
    return {
        "type": "web_service",
        "name": os.getenv("RENDER_SERVICE_NAME", "between-visits"),
        "ownerId": owner_id,
        "repo": REPO,
        "branch": "main",
        "autoDeployTrigger": "commit",
        "envVars": [
            {"key": "PYTHON_VERSION", "value": "3.11.9"},
            {"key": "BETWEEN_VISITS_LLM_PROVIDER", "value": "rules"},
            {"key": "FHIR_LAB_REMOTE_ENABLED", "value": "true"},
            {"key": "FHIR_LAB_BASE_URL", "value": "https://hapi.fhir.org/baseR4"},
            {"key": "SMART_CLIENT_ID", "value": "between-visits-demo"},
        ],
        "serviceDetails": {
            "runtime": "python",
            "plan": "free",
            "region": os.getenv("RENDER_REGION", "oregon"),
            "healthCheckPath": "/",
            "envSpecificDetails": {
                "buildCommand": "pip install -r requirements.txt",
                "startCommand": "uvicorn app.main:app --host 0.0.0.0 --port $PORT",
            },
        },
    }


def request(client: httpx.Client, method: str, path: str, **kwargs):
    response = client.request(method, API + path, **kwargs)
    if response.status_code >= 400:
        detail = response.text[:1000]
        raise RuntimeError(f"Render API {method} {path} failed ({response.status_code}): {detail}")
    return response


def choose_owner(client: httpx.Client) -> str:
    explicit = os.getenv("RENDER_OWNER_ID")
    if explicit:
        return explicit
    owners = request(client, "GET", "/owners", params={"limit": 100}).json()
    values = [item.get("owner", item) for item in owners]
    if len(values) == 1:
        return values[0]["id"]
    available = ", ".join(f"{o.get('name')} ({o.get('id')})" for o in values)
    raise RuntimeError(
        "Multiple Render workspaces are available. Set RENDER_OWNER_ID to one of: " + available)


def existing_service(client: httpx.Client, owner_id: str, name: str):
    rows = request(client, "GET", "/services",
                   params={"ownerId": owner_id, "limit": 100}).json()
    services = [item.get("service", item) for item in rows]
    return next((s for s in services if s.get("name") == name), None)


def public_url(service: dict) -> str:
    return service.get("serviceDetails", {}).get("url") or f"https://{service.get('slug')}.onrender.com"


def poll_deploy(client: httpx.Client, service_id: str, deploy_id: str,
                timeout_seconds: int = 900) -> str:
    terminal = {"live", "deactivated", "build_failed", "update_failed", "canceled"}
    deadline = time.time() + timeout_seconds
    last = None
    while time.time() < deadline:
        deploy = request(client, "GET", f"/services/{service_id}/deploys/{deploy_id}").json()
        status = deploy.get("status", "unknown")
        if status != last:
            print(f"deploy status: {status}", flush=True)
            last = status
        if status in terminal:
            return status
        time.sleep(10)
    return "timed_out"


def deploy() -> int:
    key = os.getenv("RENDER_API_KEY")
    if not key:
        print("RENDER_API_KEY is not set. Export it in this terminal and retry.", file=sys.stderr)
        return 2
    client = httpx.Client(timeout=30.0, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json",
        "Content-Type": "application/json",
    })
    owner_id = choose_owner(client)
    payload = service_payload(owner_id)
    current = existing_service(client, owner_id, payload["name"])
    if current:
        print(f"Redeploying existing service: {current['dashboardUrl']}")
        result = request(client, "POST", f"/services/{current['id']}/deploys",
                         json={"clearCache": "do_not_clear"})
        deploy_data = result.json() if result.content else {}
        deploy_id = deploy_data.get("id")
        service = current
    else:
        result = request(client, "POST", "/services", json=payload).json()
        service = result["service"]
        deploy_id = result.get("deployId")
        print(f"Created free service: {service['dashboardUrl']}")

    print(f"Public URL: {public_url(service)}")
    if not deploy_id:
        print("Deploy was queued. Check the Render dashboard for build status.")
        return 0
    status = poll_deploy(client, service["id"], deploy_id)
    print(f"Final deploy status: {status}")
    if status == "live":
        url = public_url(service)
        health = httpx.get(url + "/", timeout=90.0, follow_redirects=True)
        health.raise_for_status()
        print(f"Live and responding: {url}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(deploy())
