#!/usr/bin/env python3
"""Idempotently seed Between Visits synthetic patients and routine labs to HAPI R4.

The public HAPI server is shared and periodically reset. Run this script again
before a demo if needed. Counterfactual natural-course ED rows are excluded by
default so the EHR view cannot imply those events already occurred.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.data_loader import load_cohort
from app.fhir_labs import LOINC_LABS, PATIENT_IDENTIFIER_SYSTEM, lab_to_observation, patient_resource_id

DEFAULT_BASE = "https://hapi.fhir.org/baseR4"
SYNTHETIC_TAG = {"system": "http://between-visits.example/synthetic",
                 "code": "synthetic-data",
                 "display": "Synthetic data — not a real patient"}


def patient_resource(patient, profile) -> dict:
    parts = patient.name.rsplit(" ", 1)
    return {
        "resourceType": "Patient", "id": patient_resource_id(patient.patient_id),
        "meta": {"tag": [SYNTHETIC_TAG]}, "active": True,
        "identifier": [{"system": PATIENT_IDENTIFIER_SYSTEM, "value": patient.patient_id}],
        "name": [{"use": "usual", "text": patient.name,
                  "given": [parts[0]], "family": parts[-1] if len(parts) > 1 else parts[0]}],
        "gender": {"F": "female", "M": "male"}.get(patient.sex, "unknown"),
        "address": [{"city": profile.home_town, "state": "MA",
                     "district": profile.county,
                     "text": f"Synthetic location: {profile.home_town}, MA"}],
        "communication": [{"language": {"text": profile.primary_language}}],
        "extension": [{"url": "http://between-visits.example/fhir/StructureDefinition/synthetic-data",
                       "valueBoolean": True}],
    }


def observation_id(patient_id: str, lab) -> str:
    signature = f"{patient_id}|{lab.test}|{lab.date}|{lab.tx_day}|{lab.encounter}|{lab.specimen}"
    return "bv-" + hashlib.sha1(signature.encode()).hexdigest()[:24]


def resources(include_counterfactual: bool = False):
    cohort = load_cohort()
    for pid, patient in cohort.patients.items():
        yield patient_resource(patient, cohort.profiles[pid])
    for pid, labs in cohort.labs.items():
        for lab in labs:
            if lab.test not in LOINC_LABS or lab.value is None:
                continue
            if not include_counterfactual and "natural course" in lab.encounter.lower():
                continue
            obs = lab_to_observation(pid, lab)
            obs["id"] = observation_id(pid, lab)
            yield obs


def batches(items, size):
    batch = []
    for item in items:
        batch.append(item)
        if len(batch) == size:
            yield batch
            batch = []
    if batch:
        yield batch


def seed(base_url: str, batch_size: int, include_counterfactual: bool,
         dry_run: bool = False) -> dict:
    all_resources = list(resources(include_counterfactual))
    counts = {}
    for resource in all_resources:
        counts[resource["resourceType"]] = counts.get(resource["resourceType"], 0) + 1
    if dry_run:
        return {"base_url": base_url, "dry_run": True, "counts": counts}

    client = httpx.Client(timeout=60.0, headers={
        "Accept": "application/fhir+json", "Content-Type": "application/fhir+json"})
    uploaded = 0
    for index, group in enumerate(batches(all_resources, batch_size), start=1):
        bundle = {
            "resourceType": "Bundle", "type": "transaction",
            "entry": [{"fullUrl": f"urn:uuid:{r['id']}", "resource": r,
                       "request": {"method": "PUT", "url": f"{r['resourceType']}/{r['id']}"}}
                      for r in group],
        }
        response = client.post(base_url.rstrip("/"), content=json.dumps(bundle))
        response.raise_for_status()
        result = response.json()
        if result.get("resourceType") != "Bundle":
            raise RuntimeError(f"batch {index}: server did not return a Bundle")
        uploaded += len(group)
        print(f"batch {index}: {uploaded}/{len(all_resources)} resources", flush=True)
    return {"base_url": base_url, "dry_run": False, "counts": counts,
            "uploaded": uploaded}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default=DEFAULT_BASE)
    parser.add_argument("--batch-size", type=int, default=100)
    parser.add_argument("--include-counterfactual", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    result = seed(args.base_url, args.batch_size, args.include_counterfactual, args.dry_run)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
