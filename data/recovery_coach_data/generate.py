"""
OncoPath synthetic data generator for head & neck cancer chemoradiation.

ALL DATA IS SYNTHETIC. No real patients. For hackathon / demo use only.

Wearable fields mirror the WHOOP developer API (recovery, sleep, cycle) so real
WHOOP exports can be swapped in later.

Run:  python generate.py          (writes CSV + JSON next to this file)
"""
import json
import os
from datetime import date, timedelta

import numpy as np
import pandas as pd

SEED = 42
OUT = os.path.dirname(os.path.abspath(__file__))
BASELINE_DAYS = 14          # wear device 2 weeks before treatment to set a baseline
TREATMENT_DAYS = 49         # 7 weeks of RT (70 Gy / 35 fx, weekdays)
POST_DAYS = 7               # 1 week after RT ends
COHORT_AS_OF_DATE = date(2026, 10, 18)  # shared point-in-time dashboard snapshot


# ----------------------------------------------------------------------------
# Patient definitions (3 scripted demo patients + random cohort)
# ----------------------------------------------------------------------------
DEMO_PATIENTS = [
    dict(
        patient_id="P001", name="Maria Alvarez", age=58, sex="F",
        diagnosis="HPV+ oropharyngeal SCC (right tonsil)", stage="T2N1M0 (AJCC8 stage I)",
        treatment="RT 70 Gy/35 fx + weekly cisplatin 40 mg/m2", chemo="weekly",
        tx_start=date(2026, 9, 30), weight_kg=68.0,
        rhr=62, hrv=42, skin_temp=33.6, resp=14.8,
        events=[dict(type="dehydration", onset_tx_day=15, ed_tx_day=22)],
        story="Mucositis + post-chemo nausea -> drinks less -> dehydration. "
              "Wearable drift visible from tx day 17-19; natural course = ED visit tx day 22.",
    ),
    dict(
        patient_id="P002", name="James Okafor", age=64, sex="M",
        diagnosis="Laryngeal SCC (supraglottic)", stage="T3N2bM0 (AJCC8 stage IVA)",
        treatment="RT 70 Gy/35 fx + cisplatin 100 mg/m2 q3w", chemo="q3w",
        tx_start=date(2026, 9, 14), weight_kg=82.0,
        rhr=68, hrv=28, skin_temp=33.9, resp=15.5,
        events=[dict(type="neutropenic_fever", onset_tx_day=33, ed_tx_day=34)],
        story="ANC falls after cycle 2 (tx day 22). Skin temp, RHR and resp rate "
              "rise overnight tx day 33 -> detectable on morning sync, hours before he calls.",
    ),
    dict(
        patient_id="P003", name="Linda Chen", age=52, sex="F",
        diagnosis="Oral cavity SCC (lateral tongue), post-op", stage="pT2N1M0",
        treatment="Adjuvant RT 60 Gy/30 fx, no chemo", chemo="none",
        tx_start=date(2026, 9, 14), weight_kg=61.0,
        rhr=58, hrv=48, skin_temp=33.4, resp=14.2,
        events=[dict(type="false_alarm_confounder", onset_tx_day=10, ed_tx_day=None)],
        story="Control patient: expected gradual fatigue only. One-night dip on tx day 10 "
              "(late night at a family event) that should NOT trigger an alert.",
    ),
]


def make_cohort(rng, n=20, start_id=101):
    first = ["Ana", "Robert", "Grace", "David", "Priya", "Michael", "Sofia", "Thomas", "Aisha",
             "William", "Mei", "Carlos", "Helen", "Samuel", "Fatima", "George", "Rosa", "Daniel",
             "Irene", "Paul", "Nora", "Victor"]
    last = ["Rivera", "Nguyen", "Smith", "Patel", "Kowalski", "Brown", "Rossi", "Kim", "Haddad",
            "Murphy", "Lopez", "Johansson", "Singh", "Baker", "Mensah", "Cohen", "Ortiz", "Wright",
            "Tanaka", "Silva", "Ali", "Moreau"]
    sites = [("HPV+ oropharyngeal SCC", "weekly"), ("Laryngeal SCC", "q3w"),
             ("Oral cavity SCC, post-op", "none"), ("Hypopharyngeal SCC", "weekly"),
             ("Nasopharyngeal carcinoma", "q3w")]
    out = []
    for i in range(n):
        sex = rng.choice(["F", "M"], p=[0.3, 0.7])
        dx, chemo = sites[rng.integers(len(sites))]
        r = rng.random()
        if r < 0.45:
            events = []
        elif r < 0.75:
            on = int(rng.integers(12, 36))
            events = [dict(type="dehydration", onset_tx_day=on, ed_tx_day=on + int(rng.integers(6, 9)))]
        elif r < 0.9 and chemo != "none":
            on = int(rng.integers(26, 44))
            events = [dict(type="neutropenic_fever", onset_tx_day=on, ed_tx_day=on + 1)]
        else:
            events = [dict(type="false_alarm_confounder", onset_tx_day=int(rng.integers(5, 40)), ed_tx_day=None)]
        treatment = {"weekly": "RT 70 Gy/35 fx + weekly cisplatin 40 mg/m2",
                     "q3w": "RT 70 Gy/35 fx + cisplatin 100 mg/m2 q3w",
                     "none": "RT 60-66 Gy, no chemo"}[chemo]
        out.append(dict(
            patient_id=f"P{start_id + i}", name=f"{first[i % len(first)]} {last[(i * 7) % len(last)]}",
            age=int(rng.integers(42, 78)), sex=sex, diagnosis=dx, stage="synthetic",
            treatment=treatment, chemo=chemo,
            tx_start=date(2026, 9, 14) + timedelta(days=7 * int(rng.integers(-2, 3))),
            weight_kg=round(float(rng.normal(80 if sex == "M" else 66, 9)), 1),
            rhr=int(rng.normal(65, 6)), hrv=float(np.clip(rng.normal(36, 10), 15, 70)),
            skin_temp=round(float(rng.normal(33.7, 0.3)), 2), resp=round(float(rng.normal(15, 0.8)), 1),
            events=events, story="Cohort patient (random).",
        ))
    return out


# ----------------------------------------------------------------------------
# Physiology helpers
# ----------------------------------------------------------------------------
def sigmoid(x):
    return 1 / (1 + np.exp(-x))


def mucositis(tx_day):
    """0..1 burden: starts ~week 2-3, peaks weeks 5-6, eases slowly after RT."""
    if tx_day < 1:
        return 0.0
    rise = sigmoid((tx_day - 20) / 4.0)
    fall = 1 - 0.5 * sigmoid((tx_day - (TREATMENT_DAYS + 10)) / 4.0)
    return float(rise * fall)


def chemo_days(p):
    if p["chemo"] == "weekly":
        return [1, 8, 15, 22, 29, 36]
    if p["chemo"] == "q3w":
        return [1, 22, 43]
    return []


def nausea_from_chemo(tx_day, cdays, chemo):
    """Nausea peaks 1-3 days after cisplatin."""
    peak = 4.0 if chemo == "q3w" else 2.5
    v = 0.0
    for c in cdays:
        d = tx_day - c
        if 0 <= d <= 4:
            v = max(v, peak * np.exp(-((d - 1.5) ** 2) / 2.0))
    return v


def event_intensity(p, tx_day):
    """Returns dict of event-type -> 0..1 intensity on this day."""
    out = {}
    for e in p["events"]:
        on, ed = e["onset_tx_day"], e["ed_tx_day"]
        if e["type"] == "dehydration":
            if on <= tx_day < ed:
                out["dehydration"] = min(1.0, (tx_day - on + 1) / (ed - on))
            elif ed <= tx_day < ed + 4:          # ED visit + IV fluids -> recovers over ~4 days
                out["dehydration"] = max(0.0, 1.0 - (tx_day - ed + 1) / 4)
        elif e["type"] == "neutropenic_fever":
            if tx_day == on:
                out["fever"] = 0.35              # evening onset, partial day
            elif tx_day == ed:
                out["fever"] = 1.0               # overnight peak, captured on morning sync
            elif ed < tx_day < ed + 5:           # admitted, antibiotics, resolves
                out["fever"] = max(0.0, 1.0 - (tx_day - ed) / 4)
        elif e["type"] == "false_alarm_confounder":
            if tx_day == on:
                out["confounder"] = 1.0
    return out


# ----------------------------------------------------------------------------
# Transcript text for daily voice check-ins
# ----------------------------------------------------------------------------
def checkin_text(rng, pain, swallow, cups, nausea, fatigue, ev):
    parts = []
    if ev.get("fever", 0) > 0.6:
        parts.append(rng.choice(["I had chills last night and felt really hot.",
                                 "Felt shaky and warm overnight, didn't sleep well."]))
    if pain <= 2:
        parts.append(rng.choice(["Throat feels okay today.", "Not much pain to speak of."]))
    elif pain <= 5:
        parts.append(rng.choice(["Throat is getting sore, especially when I swallow.",
                                 "Some mouth pain, the rinses help a little."]))
    else:
        parts.append(rng.choice(["The pain is bad, it really hurts to swallow.",
                                 "My mouth and throat are very painful, even with the pain meds."]))
    if swallow >= 6:
        parts.append(rng.choice(["Hard to get anything down, mostly sipping shakes.",
                                 "Swallowing is tough, I skipped dinner."]))
    parts.append(f"I think I had about {int(round(cups))} cups of fluid.")
    if nausea >= 4:
        parts.append(rng.choice(["Pretty nauseous, threw up once.", "Feeling queasy most of the day."]))
    elif nausea >= 2:
        parts.append("A little queasy after the chemo.")
    if ev.get("dehydration", 0) > 0.6:
        parts.append(rng.choice(["Got dizzy when I stood up this morning.",
                                 "Feeling lightheaded and my mouth is so dry."]))
    if ev.get("confounder", 0) > 0:
        parts.append("Stayed up late at my niece's wedding, feeling it today but it was fun.")
    if fatigue >= 7:
        parts.append(rng.choice(["Exhausted, stayed in bed most of the day.", "Very tired today."]))
    elif fatigue >= 4:
        parts.append("Pretty tired but managed a short walk.")
    return " ".join(parts)


# ----------------------------------------------------------------------------
# Main simulation per patient
# ----------------------------------------------------------------------------
def simulate(p, rng):
    cdays = chemo_days(p)
    rt_days = TREATMENT_DAYS if p["chemo"] != "none" else 42
    start = p["tx_start"] - timedelta(days=BASELINE_DAYS)
    n_days = BASELINE_DAYS + TREATMENT_DAYS + POST_DAYS

    rec, slp, cyc, chk = [], [], [], []
    weight = p["weight_kg"]
    daily_state = {}

    for i in range(n_days):
        d = start + timedelta(days=i)
        tx_day = i - BASELINE_DAYS + 1           # tx day 1 = first RT fraction; <=0 = baseline
        on_rt = 1 <= tx_day <= rt_days and d.weekday() < 5
        m = mucositis(tx_day) * (0.8 if p["chemo"] == "none" else 1.0)
        fat = 0.0 if tx_day < 1 else min(1.0, tx_day / TREATMENT_DAYS) * 0.6 + 0.4 * m
        nau = nausea_from_chemo(tx_day, cdays, p["chemo"])
        ev = event_intensity(p, tx_day)
        dh, fv, cf = ev.get("dehydration", 0), ev.get("fever", 0), ev.get("confounder", 0)

        # --- wearable physiology -------------------------------------------
        rhr = p["rhr"] + 4 * fat + 2 * m + 12 * dh + 16 * fv + 5 * cf + rng.normal(0, 1.8)
        hrv = p["hrv"] * (1 - 0.18 * fat - 0.10 * m - 0.38 * dh - 0.45 * fv - 0.30 * cf) \
              * float(np.exp(rng.normal(0, 0.09)))
        skin = p["skin_temp"] + 0.1 * fat + 0.15 * dh + 1.4 * fv + rng.normal(0, 0.12)
        resp = p["resp"] + 0.4 * fat + 1.2 * dh + 3.0 * fv + 0.3 * cf + rng.normal(0, 0.35)
        spo2 = 97.2 - 0.3 * fat - 0.5 * dh - 2.0 * fv + rng.normal(0, 0.6)

        in_bed_h = 7.6 + 0.6 * fat + 0.8 * fv - 2.6 * cf + rng.normal(0, 0.45)
        eff = 90 - 6 * m - 8 * dh - 15 * fv - 5 * cf + rng.normal(0, 2.5)
        sleep_perf = 86 - 8 * fat - 10 * m - 18 * dh - 25 * fv - 30 * cf + rng.normal(0, 5)
        disturb = 9 + 6 * m + 6 * dh + 8 * fv + 3 * cf + rng.normal(0, 2)

        hrv_term = (hrv / p["hrv"] - 1) * 65
        recovery = 62 + hrv_term - 1.3 * (rhr - p["rhr"]) + 0.3 * (sleep_perf - 86) + rng.normal(0, 4)

        strain = 10.5 - 4 * fat - 1.5 * m - 3.5 * dh - 5 * fv + 1.5 * cf + rng.normal(0, 1.1)
        if on_rt:
            strain += 0.6                         # daily trip to the radiation center
        kj = 7200 + 450 * strain + (800 if p["sex"] == "M" else 0) + rng.normal(0, 300)

        # --- patient-reported --------------------------------------------
        pain = 1 + 7 * m + 1.5 * dh + rng.normal(0, 0.7)
        swallow = 0.5 + 7.5 * m + 1.0 * dh + rng.normal(0, 0.7)
        nausea = nau + 3.5 * dh + 1.5 * fv + rng.normal(0, 0.5)
        fatigue = 2 + 4 * fat + 3 * dh + 5 * fv + 2 * cf + rng.normal(0, 0.8)
        cups = 8 - 2.2 * m - 0.5 * nau - 4.2 * dh - 1.5 * fv + rng.normal(0, 0.6)
        meals = 100 - 40 * m - 10 * nau - 30 * dh - 30 * fv + rng.normal(0, 6)

        clip = lambda v, lo, hi: float(np.clip(v, lo, hi))
        pain, swallow, nausea, fatigue = (round(clip(x, 0, 10)) for x in (pain, swallow, nausea, fatigue))
        cups, meals = round(clip(cups, 1, 12), 1), round(clip(meals, 10, 100))

        # weight drifts down with intake; dehydration adds acute fluid loss
        weight += -0.028 * (100 - meals) / 10 - 0.25 * dh + (0.18 if 0 < dh < 0.6 and tx_day > 22 else 0) \
                  + rng.normal(0, 0.05)
        daily_state[tx_day] = dict(date=d, weight=weight, dh=dh, fv=fv, m=m, fat=fat, meals=meals)

        # missing data: ~4% device off-wrist / missed check-ins (never on key event days)
        key_day = dh > 0.4 or fv > 0
        wore = key_day or rng.random() > 0.04
        checked_in = key_day or rng.random() > 0.06

        pid = p["patient_id"]
        ds = d.isoformat()
        if wore:
            rec.append(dict(
                patient_id=pid, date=ds, tx_day=tx_day,
                recovery_score=round(clip(recovery, 1, 99)),
                resting_heart_rate=round(rhr),
                hrv_rmssd_milli=round(clip(hrv, 5, 150), 1),
                spo2_percentage=round(clip(spo2, 88, 100), 1),
                skin_temp_celsius=round(skin, 2),
            ))
            in_bed_ms = int(clip(in_bed_h, 3, 12) * 3.6e6)
            eff_c = clip(eff, 55, 98) / 100
            asleep = in_bed_ms * eff_c
            slp.append(dict(
                patient_id=pid, date=ds, tx_day=tx_day,
                total_in_bed_time_milli=in_bed_ms,
                total_awake_time_milli=int(in_bed_ms - asleep),
                total_light_sleep_time_milli=int(asleep * 0.52),
                total_slow_wave_sleep_time_milli=int(asleep * (0.20 - 0.05 * fv)),
                total_rem_sleep_time_milli=int(asleep * (0.28 + 0.05 * fv)),
                disturbance_count=int(clip(disturb, 0, 40)),
                respiratory_rate=round(resp, 1),
                sleep_performance_percentage=round(clip(sleep_perf, 10, 100)),
                sleep_efficiency_percentage=round(eff_c * 100, 1),
            ))
            cyc.append(dict(
                patient_id=pid, date=ds, tx_day=tx_day,
                strain=round(clip(strain, 0, 21), 1),
                kilojoule=round(kj),
                average_heart_rate=round(rhr + 12 + 0.8 * strain),
                max_heart_rate=round(rhr + 45 + 3 * strain + rng.normal(0, 5)),
            ))
        if checked_in and tx_day >= -2:          # check-ins start 2 days before treatment
            chk.append(dict(
                patient_id=pid, date=ds, tx_day=tx_day,
                pain_0_10=pain, swallow_difficulty_0_10=swallow, nausea_0_10=nausea,
                fatigue_0_10=fatigue, fluid_cups=cups, meals_eaten_pct=meals,
                transcript=checkin_text(rng, pain, swallow, cups, nausea, fatigue, ev),
            ))

    labs, vitals = clinic_visits(p, daily_state, cdays, rng)
    sched = schedule(p, start, n_days, cdays, rt_days)
    return rec, slp, cyc, chk, labs, vitals, sched


def clinic_visits(p, st, cdays, rng):
    """Weekly labs + vitals on treatment Mondays (tx days 1, 8, 15, ...)."""
    labs, vitals = [], []
    ref = dict(  # test: (unit, low, high)
        wbc=("10^9/L", 4.0, 11.0), anc=("10^9/L", 1.5, 7.5), hemoglobin=("g/dL", 12.0, 16.0),
        platelets=("10^9/L", 150, 400), sodium=("mmol/L", 135, 145), potassium=("mmol/L", 3.5, 5.1),
        bun=("mg/dL", 7, 20), creatinine=("mg/dL", 0.6, 1.2), magnesium=("mg/dL", 1.7, 2.2),
        albumin=("g/dL", 3.5, 5.0),
    )
    base_cr = 0.75 if p["sex"] == "F" else 0.95
    visit_days = [-6] + [1 + 7 * k for k in range(7)]   # pre-treatment visit + weekly
    for vd in visit_days:
        if vd not in st:
            continue
        s = st[vd]
        # look-back: dehydration/fever level over the last 2 days
        dh = max(st.get(vd - k, {}).get("dh", 0) for k in range(3))
        weeks = max(0, vd) / 7
        chemo = p["chemo"] != "none"
        # neutrophil nadir ~10-14 days after each cisplatin dose
        nadir = 0.0
        for c in cdays:
            dd = vd - c
            if 5 <= dd <= 16:
                nadir = max(nadir, np.exp(-((dd - 11) ** 2) / 12) * (1.0 if p["chemo"] == "q3w" else 0.45))
        if any(e["type"] == "neutropenic_fever" for e in p["events"]):
            nadir *= 1.35
        anc = max(0.2, 4.2 * (1 - 0.08 * weeks * chemo) * (1 - 0.85 * nadir) + rng.normal(0, 0.25))
        vals = dict(
            wbc=anc + 1.9 + rng.normal(0, 0.3),
            anc=anc,
            hemoglobin=13.6 - 0.22 * weeks * (1.4 if chemo else 1) + rng.normal(0, 0.25),
            platelets=250 - 9 * weeks * chemo + rng.normal(0, 15),
            sodium=139.5 - 1.0 * weeks * chemo * 0.4 - 5.5 * dh + rng.normal(0, 1.0),
            potassium=4.2 - 0.05 * weeks * chemo - 0.4 * dh + rng.normal(0, 0.15),
            bun=14 + 1.0 * weeks + 14 * dh + rng.normal(0, 1.5),
            creatinine=base_cr * (1 + 0.04 * weeks * chemo + 0.55 * dh) + rng.normal(0, 0.04),
            magnesium=2.0 - 0.06 * weeks * chemo + rng.normal(0, 0.06),
            albumin=4.2 - 0.07 * weeks - (100 - s["meals"]) / 200 + rng.normal(0, 0.1),
        )
        for k, v in vals.items():
            unit, lo, hi = ref[k]
            v = round(float(v), 2 if k in ("anc", "wbc", "creatinine", "magnesium", "potassium") else 1)
            labs.append(dict(patient_id=p["patient_id"], date=s["date"].isoformat(), tx_day=vd,
                             test=k, value=v, unit=unit, ref_low=lo, ref_high=hi,
                             flag="L" if v < lo else ("H" if v > hi else "")))
        vitals.append(dict(
            patient_id=p["patient_id"], date=s["date"].isoformat(), tx_day=vd,
            weight_kg=round(s["weight"], 1),
            weight_change_pct=round(100 * (s["weight"] / p["weight_kg"] - 1), 1),
            temp_c=round(36.7 + rng.normal(0, 0.15), 1),
            heart_rate=round(p["rhr"] + 8 + 18 * dh + rng.normal(0, 3)),
            sbp=round(126 - 18 * dh + rng.normal(0, 6)), dbp=round(78 - 8 * dh + rng.normal(0, 4)),
        ))
    return labs, vitals


def schedule(p, start, n_days, cdays, rt_days):
    rows, frac = [], 0
    for i in range(n_days):
        d = start + timedelta(days=i)
        tx_day = i - BASELINE_DAYS + 1
        rt = 1 <= tx_day <= rt_days and d.weekday() < 5
        frac += rt
        if rt or tx_day in cdays:
            rows.append(dict(patient_id=p["patient_id"], date=d.isoformat(), tx_day=tx_day,
                             rt_fraction=frac if rt else None, rt_dose_gy=2.0 if rt else 0.0,
                             cisplatin=("40 mg/m2" if p["chemo"] == "weekly" else "100 mg/m2")
                             if tx_day in cdays else ""))
    return rows


# ----------------------------------------------------------------------------
# Ground truth, portal messages, WHOOP-API-shaped JSON
# ----------------------------------------------------------------------------
def ground_truth(p):
    rows = []
    for e in p["events"]:
        on = p["tx_start"] + timedelta(days=e["onset_tx_day"] - 1)
        if e["type"] == "dehydration":
            detect = e["onset_tx_day"] + max(2, (e["ed_tx_day"] - e["onset_tx_day"]) // 2)
        elif e["type"] == "neutropenic_fever":
            detect = e["ed_tx_day"]               # morning sync after overnight fever
        else:
            detect = None
        rows.append(dict(
            patient_id=p["patient_id"], event_type=e["type"], onset_tx_day=e["onset_tx_day"],
            onset_date=on.isoformat(),
            earliest_reasonable_alert_tx_day=detect,
            counterfactual_ed_tx_day=e["ed_tx_day"],
            counterfactual_ed_date=(p["tx_start"] + timedelta(days=e["ed_tx_day"] - 1)).isoformat()
            if e["ed_tx_day"] else None,
            should_alert=e["type"] != "false_alarm_confounder",
        ))
    return rows


PORTAL_MESSAGES = [
    dict(patient_id="P001", tx_day=20, time="07:42",
         subject="Feeling awful",
         body="Hi, I've been feeling really weak and dizzy the last couple of days. My throat hurts "
              "so much I can barely drink. Is this normal? Should I still come in tomorrow?",
         true_triage="urgent_same_day", true_reason="probable dehydration",
         helpful_wearable_context="RHR/HRV/sleep/activity drift since tx day 15 + falling fluid intake in check-ins. "
                                  "Last labs (tx day 15) still normal -> wearable is the only early signal"),
    dict(patient_id="P002", tx_day=34, time="06:15",
         subject="chills last night",
         body="I had chills and sweats last night and I feel shaky this morning. "
              "I don't have a thermometer at home.",
         true_triage="emergency_now", true_reason="possible neutropenic fever (ANC falling after cycle 2; nadir expected ~tx day 33)",
         helpful_wearable_context="Overnight skin temp, RHR, respiratory rate, SpO2 vs baseline; "
                                  "plus last ANC"),
    dict(patient_id="P003", tx_day=11, time="13:05",
         subject="tired",
         body="Is it normal to feel this tired? I was out late Saturday but I still feel wiped.",
         true_triage="routine", true_reason="expected treatment fatigue + one-off late night",
         helpful_wearable_context="Single-night dip tx day 10 with recovery back to baseline next day"),
    dict(patient_id="P001", tx_day=4, time="18:20",
         subject="mouth rinse question",
         body="Which mouth rinse should I be using? The pharmacy gave me two different ones.",
         true_triage="routine", true_reason="medication question, no wearable data needed",
         helpful_wearable_context="None - system should NOT request wearable data for this message"),
]


def whoop_api_json(pid, rec, slp, cyc):
    """Records shaped like WHOOP developer API (v2) responses."""
    out = {"user_id": pid, "note": "SYNTHETIC - shaped like WHOOP API responses",
           "recovery": [], "sleep": [], "cycle": []}
    for k, r in enumerate(rec):
        out["recovery"].append({
            "cycle_id": f"{pid}-c{k}", "sleep_id": f"{pid}-s{k}", "user_id": pid,
            "created_at": f"{r['date']}T07:00:00Z", "score_state": "SCORED",
            "score": {"user_calibrating": r["tx_day"] < -9, "recovery_score": r["recovery_score"],
                      "resting_heart_rate": r["resting_heart_rate"], "hrv_rmssd_milli": r["hrv_rmssd_milli"],
                      "spo2_percentage": r["spo2_percentage"], "skin_temp_celsius": r["skin_temp_celsius"]}})
    for k, s in enumerate(slp):
        out["sleep"].append({
            "id": f"{pid}-s{k}", "user_id": pid, "start": f"{s['date']}T23:00:00Z", "nap": False,
            "score_state": "SCORED",
            "score": {"stage_summary": {kk: s[kk] for kk in s if kk.endswith("_milli") or kk == "disturbance_count"},
                      "respiratory_rate": s["respiratory_rate"],
                      "sleep_performance_percentage": s["sleep_performance_percentage"],
                      "sleep_efficiency_percentage": s["sleep_efficiency_percentage"]}})
    for k, c in enumerate(cyc):
        out["cycle"].append({
            "id": f"{pid}-c{k}", "user_id": pid, "start": f"{c['date']}T07:00:00Z", "score_state": "SCORED",
            "score": {kk: c[kk] for kk in ("strain", "kilojoule", "average_heart_rate", "max_heart_rate")}})
    return out


def main():
    rng = np.random.default_rng(SEED)
    patients = DEMO_PATIENTS + make_cohort(rng)
    tables = {k: [] for k in ("whoop_recovery", "whoop_sleep", "whoop_cycle", "checkins",
                              "labs", "clinic_vitals", "treatment_schedule")}
    gt, prof = [], []
    os.makedirs(os.path.join(OUT, "whoop_api_json"), exist_ok=True)

    for p in patients:
        rec, slp, cyc, chk, labs, vit, sch = simulate(p, rng)
        for k, rows in zip(tables, (rec, slp, cyc, chk, labs, vit, sch)):
            tables[k].extend(rows)
        gt.extend(ground_truth(p))
        prof.append(dict(patient_id=p["patient_id"], name=p["name"], age=p["age"], sex=p["sex"],
                         diagnosis=p["diagnosis"], stage=p["stage"], treatment=p["treatment"],
                         tx_start=p["tx_start"].isoformat(),
                         device_start=(p["tx_start"] - timedelta(days=BASELINE_DAYS)).isoformat(),
                         as_of_date=COHORT_AS_OF_DATE.isoformat(),
                         planned_treatment_weeks=6 if p["chemo"] == "none" else 7,
                         baseline_weight_kg=p["weight_kg"],
                         cohort="demo" if p in DEMO_PATIENTS else "cohort", story=p["story"]))
        if p in DEMO_PATIENTS:
            with open(os.path.join(OUT, "whoop_api_json", f"{p['patient_id']}.json"), "w") as f:
                json.dump(whoop_api_json(p["patient_id"], rec, slp, cyc), f, indent=1)

    for k, rows in tables.items():
        pd.DataFrame(rows).to_csv(os.path.join(OUT, f"{k}.csv"), index=False)
    pd.DataFrame(prof).to_csv(os.path.join(OUT, "patients.csv"), index=False)
    pd.DataFrame(gt).to_csv(os.path.join(OUT, "events_ground_truth.csv"), index=False)

    msgs = []
    starts = {p["patient_id"]: p["tx_start"] for p in DEMO_PATIENTS}
    for m in PORTAL_MESSAGES:
        d = starts[m["patient_id"]] + timedelta(days=m["tx_day"] - 1)
        msgs.append(dict(patient_id=m["patient_id"], sent_at=f"{d.isoformat()}T{m['time']}",
                         **{k: v for k, v in m.items() if k not in ("patient_id", "time")}))
    pd.DataFrame(msgs).to_csv(os.path.join(OUT, "portal_messages.csv"), index=False)
    print(f"Wrote {len(patients)} patients to {OUT}")


if __name__ == "__main__":
    main()
