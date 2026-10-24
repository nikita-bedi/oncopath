"""Nearest suitable hospital suggestion, modeled on the MassGIS hospitals layer.

Suggestion only — a clinician confirms. Returns an explicit "none within range"
rather than a misleading result when nothing suitable is close enough.
"""
from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

_DATA = Path(__file__).resolve().parent.parent / "data" / "ma_hospitals.json"
MAX_RANGE_KM = 80.0  # beyond this we say "none within range"


@lru_cache(maxsize=1)
def _hospitals() -> list:
    with open(_DATA, "r", encoding="utf-8") as fh:
        return json.load(fh)["hospitals"]


def _haversine_km(lat1, lon1, lat2, lon2) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def nearest_suitable(lat: float, lon: float) -> dict:
    if lat is None or lon is None:
        return {"found": False, "reason": "patient location unavailable"}

    suitable = [h for h in _hospitals() if h.get("ed")]
    scored = sorted(
        ((_haversine_km(lat, lon, h["lat"], h["lon"]), h) for h in suitable),
        key=lambda x: x[0],
    )
    if not scored:
        return {"found": False, "reason": "no ED-capable hospital in dataset"}

    dist, h = scored[0]
    if dist > MAX_RANGE_KM:
        return {"found": False,
                "reason": f"no suitable hospital within {MAX_RANGE_KM:.0f} km "
                          f"(nearest is {h['name']} at {dist:.0f} km)"}
    return {
        "found": True,
        "name": h["name"],
        "address": h["address"],
        "city": h["city"],
        "distance_km": round(dist, 1),
        "why": f"Nearest emergency-department-capable hospital, "
               f"{round(dist, 1)} km from the patient's location.",
        "note": "Suggestion only — the clinician confirms where to direct the patient.",
    }
