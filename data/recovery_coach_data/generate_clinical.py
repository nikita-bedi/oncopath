"""Extended synthetic clinical data for the OncoPath cohort.

Reads the existing CSVs in this folder (patients, labs, clinic_vitals, checkins,
treatment_schedule, events_ground_truth) and writes NEW files alongside them.
Existing files are never modified. Everything is synthetic. Seed 7.

    python generate_clinical.py
"""
import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
rng = np.random.default_rng(7)

patients = pd.read_csv(HERE / "patients.csv")
labs = pd.read_csv(HERE / "labs.csv")
vitals = pd.read_csv(HERE / "clinic_vitals.csv")
checkins = pd.read_csv(HERE / "checkins.csv")
sched = pd.read_csv(HERE / "treatment_schedule.csv")
events = pd.read_csv(HERE / "events_ground_truth.csv")


def d(s):
    return date.fromisoformat(s)


def tx_date(p, tx_day):
    """tx_day 1 = tx_start; no day 0 skipping, matches existing files."""
    return d(p.tx_start) + timedelta(days=int(tx_day) - 1)


def r(x, n=1):
    return round(float(x), n)


# --------------------------------------------------------------------------- helpers
def chemo_type(p):
    t = p.treatment.lower()
    if "weekly" in t:
        return "weekly"
    if "q3w" in t:
        return "q3w"
    return "none"


def site(p):
    dx = p.diagnosis.lower()
    for key in ("oropharyn", "laryn", "hypopharyn", "nasopharyn", "oral cavity"):
        if key in dx:
            return key
    return "head and neck"


def event_for(pid):
    e = events[(events.patient_id == pid) & (events.should_alert)]
    return None if e.empty else e.iloc[0]


def window(pid, tx_day):
    """Return 'dehydration'/'neutropenic_fever' if tx_day sits in the event window."""
    e = event_for(pid)
    if e is None:
        return None
    end = e.counterfactual_ed_tx_day if not math.isnan(e.counterfactual_ed_tx_day) else e.onset_tx_day + 3
    if e.event_type == "neutropenic_fever":
        end = e.onset_tx_day + 6
    if e.onset_tx_day <= tx_day <= end:
        return e.event_type
    return None


def week_checkin(pid, tx_day):
    c = checkins[(checkins.patient_id == pid) & (checkins.tx_day > tx_day - 7) & (checkins.tx_day <= tx_day)]
    if c.empty:
        return dict(pain=0.5, swallow=0.5, nausea=0.2, fatigue=1.5, cups=8.5, meals=97)
    return dict(pain=c.pain_0_10.mean(), swallow=c.swallow_difficulty_0_10.mean(), nausea=c.nausea_0_10.mean(),
                fatigue=c.fatigue_0_10.mean(), cups=c.fluid_cups.mean(), meals=c.meals_eaten_pct.mean())


def lab_val(pid, tx_day, test):
    v = labs[(labs.patient_id == pid) & (labs.tx_day == tx_day) & (labs.test == test)]
    return None if v.empty else float(v.value.iloc[0])


def egfr(cr, age, sex):
    """CKD-EPI 2021 (race-free)."""
    k, a = (0.7, -0.241) if sex == "F" else (0.9, -0.302)
    g = 142 * min(cr / k, 1) ** a * max(cr / k, 1) ** -1.200 * 0.9938 ** age
    return g * 1.012 if sex == "F" else g


def cis_cumulative(pid, tx_day):
    s = sched[(sched.patient_id == pid) & (sched.tx_day <= tx_day) & sched.cisplatin.notna()]
    return float(s.cisplatin.str.extract(r"(\d+)")[0].astype(float).sum()) if not s.empty else 0.0


# --------------------------------------------------------------------------- 1. profile
TOWNS = [  # town, county, lat, lon (approximate centroids, Western MA)
    ("Great Barrington", "Berkshire", 42.1959, -73.3621), ("North Adams", "Berkshire", 42.7009, -73.1087),
    ("Lee", "Berkshire", 42.3043, -73.2484), ("Williamstown", "Berkshire", 42.7120, -73.2037),
    ("Adams", "Berkshire", 42.6243, -73.1176), ("Becket", "Berkshire", 42.3320, -73.0820),
    ("Otis", "Berkshire", 42.1931, -73.0921), ("Sheffield", "Berkshire", 42.1101, -73.3548),
    ("Greenfield", "Franklin", 42.5876, -72.5995), ("Shelburne Falls", "Franklin", 42.6040, -72.7390),
    ("Orange", "Franklin", 42.5904, -72.3098), ("Turners Falls", "Franklin", 42.6043, -72.5562),
    ("Charlemont", "Franklin", 42.6270, -72.8695), ("Ashfield", "Franklin", 42.5262, -72.7887),
    ("Northampton", "Hampshire", 42.3251, -72.6412), ("Ware", "Hampshire", 42.2598, -72.2398),
    ("Huntington", "Hampshire", 42.2370, -72.8737), ("Belchertown", "Hampshire", 42.2770, -72.4009),
    ("Westfield", "Hampden", 42.1251, -72.7495), ("Palmer", "Hampden", 42.1584, -72.3287),
    ("Blandford", "Hampden", 42.1801, -72.9287), ("Monson", "Hampden", 42.1043, -72.3190),
    ("Cummington", "Hampshire", 42.4626, -72.9054),
]
CENTER = ("Treating cancer center (synthetic), Springfield, MA", 42.1015, -72.5898)
DEMO_TOWNS = {"P001": "Charlemont", "P002": "Otis", "P003": "Northampton"}


def miles(lat1, lon1, lat2, lon2):
    R = 3958.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


COMORB = ["hypertension", "type 2 diabetes", "hyperlipidemia", "COPD", "GERD", "hypothyroidism",
          "coronary artery disease", "depression", "osteoarthritis", "none"]
COMORB_MEDS = {"hypertension": ("lisinopril", "10 mg", "PO daily"), "type 2 diabetes": ("metformin", "500 mg", "PO BID"),
               "hyperlipidemia": ("atorvastatin", "20 mg", "PO nightly"), "COPD": ("tiotropium", "18 mcg", "inhaled daily"),
               "GERD": ("omeprazole", "20 mg", "PO daily"), "hypothyroidism": ("levothyroxine", "75 mcg", "PO daily"),
               "coronary artery disease": ("aspirin", "81 mg", "PO daily"), "depression": ("sertraline", "50 mg", "PO daily"),
               "osteoarthritis": ("acetaminophen", "650 mg", "PO q6h PRN")}
ALLERGIES = ["NKDA"] * 6 + ["penicillin (rash)", "sulfa (hives)", "codeine (nausea)", "latex"]

profiles = []
used = set()
for p in patients.itertuples():
    if p.patient_id in DEMO_TOWNS:
        town = next(t for t in TOWNS if t[0] == DEMO_TOWNS[p.patient_id])
    else:
        choices = [t for t in TOWNS if t[0] not in used and t[0] not in DEMO_TOWNS.values()] or TOWNS
        town = choices[rng.integers(len(choices))]
    used.add(town[0])
    lat = town[2] + rng.normal(0, 0.012)
    lon = town[3] + rng.normal(0, 0.012)
    dist = miles(lat, lon, CENTER[1], CENTER[2]) * 1.3  # road factor
    height = rng.normal(176, 7) if p.sex == "M" else rng.normal(163, 6)
    if p.patient_id == "P001":
        height = 162
    if p.patient_id == "P002":
        height = 178
    if p.patient_id == "P003":
        height = 165
    s = site(p)
    if s == "oropharyn":
        hpv = "p16 positive (HPV-associated)"
    elif s == "nasopharyn":
        hpv = "EBV-associated (EBER ISH positive)"
    else:
        hpv = "p16 negative"
    hpv_pos = "positive" in hpv and "HPV" in hpv
    smoking = "never" if hpv_pos and rng.random() < 0.6 else rng.choice(["former", "current", "former"])
    pack = 0 if smoking == "never" else int(rng.integers(10, 50))
    n_com = int(rng.integers(0, 3)) if p.age < 60 else int(rng.integers(1, 4))
    com = list(rng.choice([c for c in COMORB if c != "none"], size=n_com, replace=False)) if n_com else []
    if p.patient_id == "P001":
        com, smoking, pack = ["hypertension"], "never", 0
    if p.patient_id == "P002":
        com, smoking, pack = ["hypertension", "type 2 diabetes"], "former", 30
    if p.patient_id == "P003":
        com, smoking, pack = [], "former", 8
    ins = "Medicare" if p.age >= 65 else rng.choice(["Commercial (employer)", "MassHealth", "Commercial (employer)"])
    broadband = rng.choice(["reliable", "limited", "limited", "none"]) if town[1] in ("Berkshire", "Franklin") else rng.choice(["reliable", "reliable", "limited"])
    therm = rng.random() > 0.35
    if p.patient_id == "P002":
        therm = False  # matches his portal message
    ct = chemo_type(p)
    lives = str(rng.choice(["spouse/partner", "spouse/partner", "alone", "adult child"]))
    profiles.append(dict(
        patient_id=p.patient_id, height_cm=r(height), baseline_bmi=r(p.baseline_weight_kg / (height / 100) ** 2),
        ecog_baseline=int(rng.choice([0, 0, 1])) if p.age < 70 else int(rng.choice([0, 1, 1])),
        primary_site_group=s, hpv_ebv_status=hpv,
        smoking_status=smoking, pack_years=pack,
        alcohol=rng.choice(["none", "social", "moderate (1-2/day)", "heavy (>3/day)"], p=[0.3, 0.4, 0.2, 0.1]),
        comorbidities="; ".join(com) if com else "none",
        allergies=rng.choice(ALLERGIES) if p.patient_id not in ("P001", "P002", "P003") else ("NKDA" if p.patient_id != "P002" else "penicillin (rash)"),
        primary_language=rng.choice(["English"] * 7 + ["Spanish", "Portuguese", "Polish"]) if p.patient_id not in ("P001", "P002", "P003") else "English",
        insurance=ins, home_town=town[0], county=town[1], home_lat=r(lat, 4), home_lon=r(lon, 4),
        treating_center=CENTER[0], distance_to_center_miles=r(dist), est_drive_time_min=int(dist * 1.45 + 5),
        broadband=broadband, lives_with=lives, caregiver_available=bool(lives != "alone" or rng.random() < 0.3),
        has_home_thermometer=bool(therm),
        transportation=rng.choice(["drives self", "family drives", "volunteer driver program", "drives self"]),
        port_placed=bool(ct == "q3w" or rng.random() < 0.3),
        prophylactic_peg=bool(s in ("hypopharyn", "laryn") and rng.random() < 0.35),
        dental_clearance=True, gcsf_prophylaxis=False,
        cisplatin_regimen={"weekly": "weekly 40 mg/m2", "q3w": "100 mg/m2 q3w", "none": "none"}[ct],
        planned_rt_dose_gy=60 if "60" in p.treatment else 70,
        wearable_device="WHOOP-style band (synthetic)", synthetic=True,
    ))
prof = pd.DataFrame(profiles)
PROF = prof.set_index("patient_id")

# --------------------------------------------------------------------------- 2. labs
REF = {  # test: (panel, unit, low, high) ; sex-specific handled below
    "rbc": ("CBC", "10^12/L", 4.0, 5.5), "hematocrit": ("CBC", "%", 36, 48), "mcv": ("CBC", "fL", 80, 100),
    "mch": ("CBC", "pg", 27, 33), "mchc": ("CBC", "g/dL", 32, 36), "rdw": ("CBC", "%", 11.5, 14.5),
    "mpv": ("CBC", "fL", 7.5, 11.5), "lymphocytes_abs": ("CBC diff", "10^9/L", 1.0, 4.0),
    "monocytes_abs": ("CBC diff", "10^9/L", 0.2, 1.0), "eosinophils_abs": ("CBC diff", "10^9/L", 0.0, 0.5),
    "basophils_abs": ("CBC diff", "10^9/L", 0.0, 0.2),
    "chloride": ("CMP", "mmol/L", 98, 107), "bicarbonate": ("CMP", "mmol/L", 22, 29), "anion_gap": ("CMP", "mmol/L", 8, 16),
    "glucose": ("CMP", "mg/dL", 70, 99), "calcium": ("CMP", "mg/dL", 8.6, 10.2), "corrected_calcium": ("CMP", "mg/dL", 8.6, 10.2),
    "phosphate": ("CMP", "mg/dL", 2.5, 4.5), "total_protein": ("CMP", "g/dL", 6.0, 8.3),
    "ast": ("LFT", "U/L", 10, 40), "alt": ("LFT", "U/L", 7, 56), "alp": ("LFT", "U/L", 44, 147),
    "total_bilirubin": ("LFT", "mg/dL", 0.1, 1.2), "egfr": ("Renal", "mL/min/1.73m2", 60, None),
    "bun_creatinine_ratio": ("Renal", "ratio", 10, 20), "crp": ("Inflammation", "mg/L", 0, 5),
    "prealbumin": ("Nutrition", "mg/dL", 16, 35), "uric_acid": ("CMP", "mg/dL", 3.5, 7.2),
    "ldh": ("Other", "U/L", 140, 280),
}


def ref_for(test, sex):
    panel, unit, lo, hi = REF[test]
    if test == "rbc":
        lo, hi = (4.5, 5.9) if sex == "M" else (4.0, 5.2)
    if test == "hematocrit":
        lo, hi = (41, 53) if sex == "M" else (36, 46)
    if test == "uric_acid":
        lo, hi = (3.5, 7.2) if sex == "M" else (2.6, 6.0)
    return panel, unit, lo, hi


lab_rows = []


def add_lab(pid, dt, tx_day, test, value, unit, lo, hi, panel, encounter, specimen="serum", ndp=1, result_text=""):
    flag = ""
    if value is not None and not isinstance(value, str):
        value = r(value, ndp)
        if lo is not None and value < lo:
            flag = "L"
        if hi is not None and value > hi:
            flag = "H"
        if test == "anc" and value < 0.5:
            flag = "LL"
        if test == "lactate" and value >= 4:
            flag = "HH"
    else:
        txt = (result_text or str(value)).lower()
        flag = "A" if any(w in txt for w in ("positive", "growth of", "gram")) and "no growth" not in txt else ""
    lab_rows.append(dict(patient_id=pid, date=dt.isoformat(), tx_day=int(tx_day), encounter=encounter, panel=panel,
                         specimen=specimen, test=test, value=value if not isinstance(value, str) else "",
                         result_text=result_text or (value if isinstance(value, str) else ""),
                         unit=unit, ref_low=lo if lo is not None else "", ref_high=hi if hi is not None else "", flag=flag))


def weekly_extras(p, pf, tx_day, dt, encounter, dh=0.0, fever=0.0):
    """Correlated extra tests built off the existing weekly values for this visit."""
    pid, sex = p.patient_id, p.sex
    hgb = lab_val(pid, tx_day, "hemoglobin")
    na = lab_val(pid, tx_day, "sodium")
    bun = lab_val(pid, tx_day, "bun")
    cr = lab_val(pid, tx_day, "creatinine")
    alb = lab_val(pid, tx_day, "albumin")
    wbc = lab_val(pid, tx_day, "wbc")
    anc = lab_val(pid, tx_day, "anc")
    weeks = max(0, tx_day) / 7
    ck = week_checkin(pid, tx_day)
    diabetic = "diabetes" in pf.comorbidities
    mcv = 89 + (1.2 * weeks if chemo_type(p) != "none" else 0.3 * weeks) + rng.normal(0, 1.5)
    hct = hgb * 3.0 + rng.normal(0, 0.6)
    rbc = hct / mcv * 10
    lymph = max(0.18, (1.9 if tx_day <= 0 else 1.9 * math.exp(-0.06 * tx_day)) + rng.normal(0, 0.12))  # RT lymphopenia
    eos = max(0.0, 0.12 + rng.normal(0, 0.04))
    baso = max(0.0, 0.03 + rng.normal(0, 0.01))
    mono = float(np.clip(0.48 * (anc / 4.2) ** 0.3 + rng.normal(0, 0.08), 0.15, 0.9))
    cl = na - 36 + 3 * dh + rng.normal(0, 1.2)
    hco3 = 25.5 - 4.5 * dh + rng.normal(0, 1.0)
    glu = (128 if diabetic else 92) + 14 * (tx_day in (1, 2, 22, 23)) * (chemo_type(p) != "none") + rng.normal(0, 8)  # dex on chemo days
    ca = 9.3 - 0.2 * weeks / 6 + rng.normal(0, 0.2)
    crp = (2 + 3.5 * min(ck["pain"], 8) / 8 * (tx_day > 7) + 18 * dh + 140 * fever + rng.gamma(1.5, 0.8))
    pre = max(6, 27 - (100 - ck["meals"]) * 0.18 - 1.2 * weeks * (ck["meals"] < 80) + rng.normal(0, 1.5))
    vals = {
        "rbc": (rbc, 2), "hematocrit": (hct, 1), "mcv": (mcv, 1), "mch": (hgb / rbc * 10, 1), "mchc": (hgb / hct * 100, 1),
        "rdw": (13.0 + 0.15 * weeks + rng.normal(0, 0.4), 1), "mpv": (9.4 + rng.normal(0, 0.5), 1),
        "lymphocytes_abs": (lymph, 2), "monocytes_abs": (mono, 2), "eosinophils_abs": (eos, 2), "basophils_abs": (min(baso, 0.12), 2),
        "chloride": (cl, 0), "bicarbonate": (hco3, 0), "anion_gap": (na - cl - hco3, 0),
        "glucose": (glu, 0), "calcium": (ca, 1), "corrected_calcium": (ca + 0.8 * (4.0 - alb), 1),
        "phosphate": (3.6 - 0.15 * weeks / 6 * (ck["meals"] < 70) + rng.normal(0, 0.3), 1),
        "total_protein": (alb + 2.7 + rng.normal(0, 0.2), 1),
        "ast": (22 + rng.normal(0, 4), 0), "alt": (24 + rng.normal(0, 6), 0), "alp": (78 + rng.normal(0, 12), 0),
        "total_bilirubin": (0.6 + rng.normal(0, 0.12), 1),
        "egfr": (egfr(cr, p.age, sex), 0), "bun_creatinine_ratio": (bun / cr, 1),
        "crp": (crp, 1), "prealbumin": (pre, 1),
    }
    for t, (v, ndp) in vals.items():
        panel, unit, lo, hi = ref_for(t, sex)
        add_lab(pid, dt, tx_day, t, v, unit, lo, hi, panel, encounter, ndp=ndp)


def baseline_only(p, pf, tx_day, dt):
    pid = p.patient_id
    E = "baseline pre-treatment"
    add_lab(pid, dt, tx_day, "pt", 12.2 + rng.normal(0, 0.5), "s", 11.0, 13.5, "Coagulation", E, "plasma")
    add_lab(pid, dt, tx_day, "inr", 1.0 + rng.normal(0, 0.05), "ratio", 0.8, 1.2, "Coagulation", E, "plasma", ndp=2)
    add_lab(pid, dt, tx_day, "aptt", 30 + rng.normal(0, 2), "s", 25, 35, "Coagulation", E, "plasma")
    hypo = "hypothyroidism" in pf.comorbidities
    add_lab(pid, dt, tx_day, "tsh", (2.6 if hypo else 1.8) + rng.normal(0, 0.5), "mIU/L", 0.4, 4.0, "Endocrine", E, ndp=2)
    add_lab(pid, dt, tx_day, "free_t4", 1.2 + rng.normal(0, 0.12), "ng/dL", 0.8, 1.8, "Endocrine", E, ndp=2)
    diab = "diabetes" in pf.comorbidities
    add_lab(pid, dt, tx_day, "hba1c", (7.1 if diab else 5.4) + rng.normal(0, 0.25), "%", 4.0, 5.6, "Endocrine", E, "whole blood")
    add_lab(pid, dt, tx_day, "ferritin", 120 + rng.normal(0, 40), "ng/mL", 24 if p.sex == "M" else 11, 336 if p.sex == "M" else 307, "Iron studies", E, ndp=0)
    add_lab(pid, dt, tx_day, "vitamin_d_25oh", 27 + rng.normal(0, 7), "ng/mL", 30, 100, "Nutrition", E)
    add_lab(pid, dt, tx_day, "ldh", 190 + rng.normal(0, 25), "U/L", 140, 280, "Other", E, ndp=0)
    add_lab(pid, dt, tx_day, "uric_acid", 5.2 + rng.normal(0, 0.8), "mg/dL", *ref_for("uric_acid", p.sex)[2:], "CMP", E)
    for t, txt in (("hbsag", "Nonreactive"), ("anti_hbc_total", "Nonreactive"), ("hcv_antibody", "Nonreactive"),
                   ("hiv_ag_ab", "Nonreactive")):
        add_lab(pid, dt, tx_day, t, txt, "", None, None, "Infection screening", E)
    if p.sex == "F" and p.age < 55:
        add_lab(pid, dt, tx_day, "serum_hcg", "Negative", "", None, None, "Pregnancy", E)
    if pf.primary_site_group == "nasopharyn":
        add_lab(pid, dt, tx_day, "ebv_dna_plasma", int(rng.integers(800, 9000)), "IU/mL", None, 0, "Tumor marker", E, "plasma", ndp=0)
    if pf.primary_site_group == "oropharyn":
        add_lab(pid, dt, tx_day, "ctHPV_DNA", int(rng.integers(150, 2500)), "fragments/mL", None, 0, "Tumor marker", E, "plasma", ndp=0)
    add_lab(pid, dt, tx_day, "urine_specific_gravity", 1.015 + rng.normal(0, 0.003), "", 1.005, 1.030, "Urinalysis", E, "urine", ndp=3)
    add_lab(pid, dt, tx_day, "urine_ketones", "Negative", "", None, None, "Urinalysis", E, "urine")
    add_lab(pid, dt, tx_day, "urine_protein", "Negative", "", None, None, "Urinalysis", E, "urine")


def acute_panel(p, pf, ev, tx_day, dt):
    """Natural-course ED panel for dehydration / neutropenic fever events."""
    pid, sex = p.patient_id, p.sex
    E = f"ED visit ({ev.replace('_', ' ')}, natural course)"
    on_weekly = lab_val(pid, tx_day, "sodium") is not None
    prev = max([x for x in labs[labs.patient_id == pid].tx_day.unique() if x <= tx_day])
    base_cr = lab_val(pid, -6, "creatinine")
    if ev == "dehydration":
        if not on_weekly:  # generate CBC/BMP consistent with dehydration
            na = rng.uniform(130.5, 133.5); bun = rng.uniform(30, 38); cr = base_cr * rng.uniform(1.5, 1.8)
            k = rng.uniform(3.3, 3.7); hgb = lab_val(pid, prev, "hemoglobin") + 0.6; wbc = lab_val(pid, prev, "wbc") + 0.4
            anc = lab_val(pid, prev, "anc") + 0.3; plt = lab_val(pid, prev, "platelets"); mg = lab_val(pid, prev, "magnesium") - 0.1
            for t, v, u, lo, hi, pan, nd in (("sodium", na, "mmol/L", 135, 145, "BMP", 1), ("potassium", k, "mmol/L", 3.5, 5.1, "BMP", 2),
                                             ("bun", bun, "mg/dL", 7, 20, "BMP", 1), ("creatinine", cr, "mg/dL", 0.6, 1.2, "BMP", 2),
                                             ("magnesium", mg, "mg/dL", 1.7, 2.2, "BMP", 2), ("hemoglobin", hgb, "g/dL", 12.0, 16.0, "CBC", 1),
                                             ("wbc", wbc, "10^9/L", 4.0, 11.0, "CBC", 2), ("anc", anc, "10^9/L", 1.5, 7.5, "CBC", 2),
                                             ("platelets", plt, "10^9/L", 150, 400, "CBC", 1)):
                add_lab(pid, dt, tx_day, t, v, u, lo, hi, pan, E, ndp=nd)
        else:
            na, bun, cr = lab_val(pid, tx_day, "sodium"), lab_val(pid, tx_day, "bun"), lab_val(pid, tx_day, "creatinine")
        glu = 105 + rng.normal(0, 10)
        add_lab(pid, dt, tx_day, "chloride", na - 33 + rng.normal(0, 1), "mmol/L", 98, 107, "BMP", E, ndp=0)
        add_lab(pid, dt, tx_day, "bicarbonate", rng.uniform(18.5, 21.5), "mmol/L", 22, 29, "BMP", E, ndp=0)
        add_lab(pid, dt, tx_day, "egfr", egfr(cr, p.age, sex), "mL/min/1.73m2", 60, None, "Renal", E, ndp=0)
        add_lab(pid, dt, tx_day, "bun_creatinine_ratio", bun / cr, "ratio", 10, 20, "Renal", E)
        add_lab(pid, dt, tx_day, "serum_osmolality", 2 * na + glu / 18 + bun / 2.8 + rng.normal(0, 2), "mOsm/kg", 275, 295, "Other", E, ndp=0)
        add_lab(pid, dt, tx_day, "lactate", rng.uniform(1.8, 2.6), "mmol/L", 0.5, 2.0, "Other", E, "venous blood")
        add_lab(pid, dt, tx_day, "crp", rng.uniform(12, 30), "mg/L", 0, 5, "Inflammation", E)
        add_lab(pid, dt, tx_day, "urine_specific_gravity", rng.uniform(1.029, 1.036), "", 1.005, 1.030, "Urinalysis", E, "urine", ndp=3)
        add_lab(pid, dt, tx_day, "urine_ketones", rng.choice(["1+", "2+"]), "", None, None, "Urinalysis", E, "urine")
        add_lab(pid, dt, tx_day, "urine_sodium", rng.uniform(8, 18), "mmol/L", 20, None, "Urinalysis", E, "urine", ndp=0)
    else:  # neutropenic fever
        prev_hgb = lab_val(pid, prev, "hemoglobin")
        anc = rng.uniform(0.12, 0.42)
        for t, v, u, lo, hi, nd in (("wbc", anc + rng.uniform(0.5, 0.9), "10^9/L", 4.0, 11.0, 2), ("anc", anc, "10^9/L", 1.5, 7.5, 2),
                                    ("hemoglobin", prev_hgb - 0.4, "g/dL", 12.0, 16.0, 1),
                                    ("platelets", lab_val(pid, prev, "platelets") * 0.7, "10^9/L", 150, 400, 1),
                                    ("lymphocytes_abs", rng.uniform(0.2, 0.45), "10^9/L", 1.0, 4.0, 2)):
            add_lab(pid, dt, tx_day, t, v, u, lo, hi, "CBC", E, ndp=nd)
        na = lab_val(pid, prev, "sodium") - 1.5
        cr = lab_val(pid, prev, "creatinine") * 1.1
        for t, v, u, lo, hi, nd in (("sodium", na, "mmol/L", 135, 145, 1), ("potassium", 3.9 + rng.normal(0, 0.2), "mmol/L", 3.5, 5.1, 2),
                                    ("bun", lab_val(pid, prev, "bun") + 3, "mg/dL", 7, 20, 1), ("creatinine", cr, "mg/dL", 0.6, 1.2, 2),
                                    ("magnesium", lab_val(pid, prev, "magnesium") - 0.05, "mg/dL", 1.7, 2.2, 2),
                                    ("albumin", lab_val(pid, prev, "albumin") - 0.2, "g/dL", 3.5, 5.0, 1),
                                    ("egfr", egfr(cr, p.age, sex), "mL/min/1.73m2", 60, None, 0)):
            add_lab(pid, dt, tx_day, t, v, u, lo, hi, "CMP", E, ndp=nd)
        add_lab(pid, dt, tx_day, "chloride", na - 35 + rng.normal(0, 1), "mmol/L", 98, 107, "CMP", E, ndp=0)
        add_lab(pid, dt, tx_day, "bicarbonate", rng.uniform(20, 23), "mmol/L", 22, 29, "CMP", E, ndp=0)
        add_lab(pid, dt, tx_day, "glucose", 135 + rng.normal(0, 15), "mg/dL", 70, 99, "CMP", E, ndp=0)
        add_lab(pid, dt, tx_day, "crp", rng.uniform(95, 180), "mg/L", 0, 5, "Inflammation", E)
        add_lab(pid, dt, tx_day, "procalcitonin", rng.uniform(0.8, 3.5), "ng/mL", 0, 0.5, "Inflammation", E, ndp=2)
        add_lab(pid, dt, tx_day, "lactate", rng.uniform(2.1, 3.3), "mmol/L", 0.5, 2.0, "Other", E, "venous blood")
        organism = "Escherichia coli" if pid == "P002" else rng.choice(["Escherichia coli", "Klebsiella pneumoniae", "no growth", "no growth"])
        bc1 = ("Growth of " + organism + " (gram-negative rods) at 14 h; susceptible to cefepime") if organism != "no growth" else "No growth at 5 days"
        add_lab(pid, dt, tx_day, "blood_culture_1", bc1, "", None, None, "Microbiology", E, "blood (peripheral)")
        add_lab(pid, dt, tx_day, "blood_culture_2", bc1 if organism != "no growth" else "No growth at 5 days", "", None, None, "Microbiology",
                E, "blood (port)" if PROF.loc[pid].port_placed else "blood (peripheral)")
        add_lab(pid, dt, tx_day, "urine_culture", "No growth", "", None, None, "Microbiology", E, "urine")
        add_lab(pid, dt, tx_day, "respiratory_viral_panel", "Negative (SARS-CoV-2, influenza A/B, RSV)", "", None, None, "Microbiology", E, "nasopharyngeal swab")


for p in patients.itertuples():
    pf = PROF.loc[p.patient_id]
    visits = sorted(labs[labs.patient_id == p.patient_id].tx_day.unique())
    for vd in visits:
        dt = d(labs[(labs.patient_id == p.patient_id) & (labs.tx_day == vd)].date.iloc[0])
        w = window(p.patient_id, vd)
        e0 = event_for(p.patient_id)
        # labs stay normal until the ED day itself (early signal is wearable-only)
        acute = w == "dehydration" and e0 is not None and vd >= e0.counterfactual_ed_tx_day - 1
        enc = "baseline pre-treatment" if vd <= 0 else "weekly on-treatment labs"
        weekly_extras(p, pf, vd, dt, enc, dh=1.0 if acute else 0.0, fever=0.0)
        if vd <= 0:
            baseline_only(p, pf, vd, dt)
        if vd == max(visits):  # end-of-treatment thyroid check
            add_lab(p.patient_id, dt, vd, "tsh", 2.1 + rng.normal(0, 0.6), "mIU/L", 0.4, 4.0, "Endocrine", enc, ndp=2)
    e = event_for(p.patient_id)
    if e is not None:
        ed_day = int(e.counterfactual_ed_tx_day)
        acute_panel(p, pf, e.event_type, ed_day, tx_date(p, ed_day))

labs_ext = pd.DataFrame(lab_rows)

# --------------------------------------------------------------------------- 3. physical exams
GRADE_MUC = lambda pain: 0 if pain < 1.5 else 1 if pain < 4 else 2 if pain < 7 else 3
GRADE_DYS = lambda sw: 0 if sw < 1.5 else 1 if sw < 5 else 2 if sw < 8 else 3
exam_rows = []
for v in vitals.itertuples():
    p = patients[patients.patient_id == v.patient_id].iloc[0]
    pf = PROF.loc[v.patient_id]
    ck = week_checkin(v.patient_id, v.tx_day)
    w = window(v.patient_id, v.tx_day)
    ev0 = event_for(v.patient_id)
    # dehydration only shows on exam right before the natural-course ED visit
    if w == "dehydration" and v.tx_day < ev0.counterfactual_ed_tx_day - 1:
        w = None
    rt_on = v.tx_day >= 1
    muc = GRADE_MUC(ck["pain"]) if rt_on else 0
    dys = GRADE_DYS(ck["swallow"]) if rt_on else 0
    derm = 0 if v.tx_day < 10 else 1 if v.tx_day < 22 else 2 if v.tx_day < 40 else int(rng.choice([2, 2, 3]))
    xero = 0 if v.tx_day < 14 else 1 if v.tx_day < 29 else 2
    cum_cis = cis_cumulative(v.patient_id, v.tx_day)
    neuro = 1 if cum_cis >= 200 and rng.random() < 0.35 else 0
    tinnitus = bool(cum_cis >= 200 and rng.random() < 0.3)
    dry = w == "dehydration" or ck["cups"] < 4
    ecog = min(3, int(pf.ecog_baseline) + (ck["fatigue"] > 5.5) + (w is not None))
    ortho = "positive (HR +24, SBP -22 on standing)" if w == "dehydration" else "negative"
    thrush = bool(rt_on and v.tx_day > 14 and rng.random() < 0.12)
    rr = 16 + 3 * (w == "neutropenic_fever") + int(rng.integers(-1, 2))
    spo2 = 97 - 2 * (w == "neutropenic_fever") + int(rng.integers(-1, 2))
    note = []
    if muc >= 2:
        note.append(f"grade {muc} oral mucositis with ulceration of the {'oral mucosa' if pf.primary_site_group == 'oral cavity' else 'oropharyngeal mucosa'}")
    if dry:
        note.append("dry mucous membranes, reduced skin turgor")
    if w == "dehydration":
        note.append("appears fatigued and orthostatic")
    if derm >= 2:
        note.append(f"grade {derm} radiation dermatitis of the neck")
    if thrush:
        note.append("white plaques on tongue consistent with oral candidiasis")
    if not note:
        note.append("no acute findings; tolerating treatment")
    exam_rows.append(dict(
        patient_id=v.patient_id, date=v.date, tx_day=v.tx_day,
        exam_type="pre-treatment H&P" if v.tx_day <= 0 else "weekly on-treatment visit (OTV)",
        examiner_role="radiation oncology NP" if v.tx_day > 0 else "radiation oncologist",
        ecog=ecog, weight_kg=v.weight_kg, temp_c=v.temp_c, heart_rate=v.heart_rate, sbp=v.sbp, dbp=v.dbp,
        respiratory_rate=rr, spo2_pct=spo2, orthostatic_vitals=ortho,
        general_appearance="ill-appearing, fatigued" if w else ("tired but well-appearing" if ck["fatigue"] > 5 else "well-appearing"),
        mucous_membranes="dry" if dry else "moist", skin_turgor="decreased" if dry else "normal",
        capillary_refill_s=3 if w == "dehydration" else 2,
        oral_mucositis_grade_ctcae=muc, oral_candidiasis=thrush, dysphagia_grade_ctcae=dys,
        radiation_dermatitis_grade_ctcae=derm if rt_on else 0, xerostomia_grade_ctcae=xero if rt_on else 0,
        neck_nodes="palpable level II node, decreasing" if v.tx_day < 29 and "N0" not in p.stage else "no palpable lymphadenopathy",
        lungs="clear to auscultation bilaterally", heart="regular rate and rhythm, no murmur" if v.heart_rate < 100 else "tachycardic, regular rhythm",
        abdomen="soft, non-tender", peripheral_neuropathy_grade_ctcae=neuro,
        hearing="reports tinnitus" if tinnitus else "no change",
        feeding_tube="PEG in place" if pf.prophylactic_peg and v.tx_day > 0 else ("NG tube placed" if dys == 3 and v.tx_day > 21 else "none"),
        exam_summary="; ".join(note).capitalize() + ".",
    ))
exams = pd.DataFrame(exam_rows)

# --------------------------------------------------------------------------- 4. nutrition
nut_rows = []
for v in vitals.itertuples():
    p = patients[patients.patient_id == v.patient_id].iloc[0]
    pf = PROF.loc[v.patient_id]
    ck = week_checkin(v.patient_id, v.tx_day)
    ex = exams[(exams.patient_id == v.patient_id) & (exams.tx_day == v.tx_day)].iloc[0]
    wl = -v.weight_change_pct
    bmi = v.weight_kg / (pf.height_cm / 100) ** 2
    kcal_need = round(v.weight_kg * 30 / 50) * 50
    prot_need = round(v.weight_kg * 1.3)
    kcal_in = kcal_need * ck["meals"] / 100 * (1.05 if ck["meals"] > 90 else 1)
    # PG-SGA (simplified scoring of boxes 1-4 + disease/age)
    s_wt = 0 if wl < 1 else 1 if wl < 2 else 2 if wl < 5 else 3 if wl < 10 else 4
    s_intake = 0 if ck["meals"] >= 90 else 1 if ck["meals"] >= 75 else 2 if ck["meals"] >= 50 else 4
    s_sym = (ck["pain"] > 3) * 3 + (ck["swallow"] > 3) * 2 + (ck["nausea"] > 2) * 1 + (ck["fatigue"] > 5) * 1
    s_act = min(3, int(ex.ecog))
    s_dis = 1 + (p.age >= 65)
    pgsga = int(s_wt + s_intake + s_sym + s_act + s_dis)
    cat = "A (well nourished)" if pgsga <= 3 else "B (moderate/suspected malnutrition)" if pgsga <= 12 else "C (severely malnourished)"
    triage = "no intervention" if pgsga <= 1 else "patient education" if pgsga <= 3 else "dietitian intervention" if pgsga <= 8 else "critical need for symptom management + nutrition intervention"
    route = ex.feeding_tube if ex.feeding_tube != "none" else ("oral + ONS" if ck["meals"] < 85 else "oral")
    glim = "severe malnutrition" if wl >= 10 else "moderate malnutrition" if wl >= 5 and ck["meals"] < 75 else "not met"
    nut_rows.append(dict(
        patient_id=v.patient_id, date=v.date, tx_day=v.tx_day, weight_kg=v.weight_kg, height_cm=pf.height_cm,
        bmi=r(bmi), weight_change_pct_from_baseline=v.weight_change_pct,
        avg_meals_eaten_pct_7d=r(ck["meals"]), avg_fluid_cups_7d=r(ck["cups"]),
        est_energy_need_kcal=kcal_need, est_energy_intake_kcal=int(kcal_in), est_protein_need_g=prot_need,
        est_protein_intake_g=int(prot_need * ck["meals"] / 100), fluid_goal_cups=10 if v.weight_kg > 70 else 8,
        diet_texture="regular" if ck["swallow"] < 3 else "soft/moist" if ck["swallow"] < 6 else "pureed/liquid",
        feeding_route=route, oral_nutrition_supplement="1-2 bottles/day" if "ONS" in route else ("3 bottles/day" if ck["meals"] < 60 else "none"),
        pg_sga_score=pgsga, pg_sga_category=cat, pg_sga_triage=triage, glim_criteria=glim,
        dietitian_seen=v.tx_day <= 0 or pgsga >= 4,
        taste_change="yes" if v.tx_day > 10 else "no", synthetic=True,
    ))
nutrition = pd.DataFrame(nut_rows)

# --------------------------------------------------------------------------- 5. imaging
img_rows = []
LAT = ["right", "left"]


def add_img(pid, dt, tx_day, modality, region, indication, findings, impression, status="final", contrast=""):
    img_rows.append(dict(patient_id=pid, date=dt.isoformat(), tx_day=int(tx_day), modality=modality, body_region=region,
                         contrast=contrast, indication=indication, findings=findings, impression=impression, status=status))


for p in patients.itertuples():
    pf = PROF.loc[p.patient_id]
    side = "right" if "right" in p.diagnosis else LAT[rng.integers(2)]
    s = pf.primary_site_group
    loc = {"oropharyn": f"{side} palatine tonsil", "laryn": "supraglottic larynx", "hypopharyn": f"{side} piriform sinus",
           "nasopharyn": f"{side} fossa of Rosenmuller", "oral cavity": f"{side} lateral tongue"}.get(s, "primary site")
    suv = r(rng.uniform(8, 18))
    node_cm = r(rng.uniform(1.4, 3.2))
    postop = "post-op" in p.diagnosis
    if postop:
        pet_f = f"Post-surgical changes of the {loc} with expected mild uptake (SUVmax {r(rng.uniform(2.5, 4))}). No discrete residual mass. No hypermetabolic cervical nodes. No distant metastasis."
        pet_i = "Post-operative changes. No evidence of residual or metastatic disease."
    else:
        pet_f = f"Hypermetabolic mass of the {loc} (SUVmax {suv}). Hypermetabolic {side} level II lymph node, {node_cm} cm (SUVmax {r(suv * 0.7)}). No hypermetabolic lung, liver, or osseous lesions."
        pet_i = f"Primary {loc} malignancy with {side} cervical nodal metastasis. No distant metastatic disease."
    add_img(p.patient_id, tx_date(p, -30), -30, "FDG PET/CT", "skull base to mid-thigh", "Initial staging", pet_f, pet_i)
    mri_region = "nasopharynx and skull base" if s == "nasopharyn" else "neck and oral cavity"
    add_img(p.patient_id, tx_date(p, -27), -27, "MRI", mri_region, "Local extent for RT planning",
            f"{'Post-operative bed without enhancing residual tumor' if postop else 'Enhancing mass at the ' + loc}; no perineural spread identified."
            + (" No skull-base invasion." if s == "nasopharyn" else ""),
            "Findings as above, used for target delineation.", contrast="gadolinium")
    add_img(p.patient_id, tx_date(p, -21), -21, "Panoramic dental X-ray", "mandible and maxilla", "Pre-radiation dental evaluation",
            rng.choice(["No periapical lucency. Restorations intact.", "Non-restorable left lower molar, extracted pre-RT.", "Mild generalized bone loss; no acute pathology."]),
            "Dental clearance for radiation.")
    add_img(p.patient_id, tx_date(p, -12), -12, "CT simulation", "head and neck", "Radiation treatment planning",
            "Thermoplastic mask immobilization. Planning CT acquired at 2 mm slices.", "Simulation completed.", contrast="IV iodinated")
    if pf.smoking_status != "never":
        add_img(p.patient_id, tx_date(p, -29), -29, "CT", "chest", "Staging (smoking history)",
                rng.choice(["No pulmonary nodules.", "4 mm right upper lobe nodule, likely benign; no follow-up needed per criteria.", "Mild centrilobular emphysema. No nodules."]),
                "No evidence of pulmonary metastasis.", contrast="IV iodinated")
    e = event_for(p.patient_id)
    if e is not None and e.event_type == "neutropenic_fever":
        ed = int(e.counterfactual_ed_tx_day)
        add_img(p.patient_id, tx_date(p, ed), ed, "X-ray (2 views)", "chest", "Fever with neutropenia",
                "Lungs clear. No focal consolidation, effusion, or pneumothorax." + (" Port catheter tip at cavoatrial junction." if pf.port_placed else ""),
                "No acute cardiopulmonary process.")
    ex = exams[exams.patient_id == p.patient_id]
    if (ex.dysphagia_grade_ctcae >= 3).any():
        dday = int(ex[ex.dysphagia_grade_ctcae >= 3].tx_day.iloc[0])
        add_img(p.patient_id, tx_date(p, dday + 1), dday + 1, "Modified barium swallow (videofluoroscopy)", "oropharynx and esophagus",
                "Severe odynophagia/dysphagia on treatment", "Delayed swallow initiation and vallecular residue with thin liquids. Trace penetration without aspiration.",
                "Mild-moderate oropharyngeal dysphagia; speech-language pathology to follow.")
    end_rt = 42 if pf.planned_rt_dose_gy == 60 else 49
    add_img(p.patient_id, tx_date(p, end_rt + 84), end_rt + 84, "FDG PET/CT", "skull base to mid-thigh",
            "12-week post-treatment response assessment", "", "", status="scheduled")
imaging = pd.DataFrame(img_rows).sort_values(["patient_id", "tx_day"])

# --------------------------------------------------------------------------- 6. medications
med_rows = []


def add_med(pid, name, dose, route, start, stop, indication, status="active"):
    med_rows.append(dict(patient_id=pid, medication=name, dose=dose, route_frequency=route, start_tx_day=start,
                         stop_tx_day=stop if stop is not None else "", indication=indication, status=status))


for p in patients.itertuples():
    pf = PROF.loc[p.patient_id]
    pid = p.patient_id
    ct = chemo_type(p)
    end_rt = 42 if pf.planned_rt_dose_gy == 60 else 49
    for c in [x for x in pf.comorbidities.split("; ") if x in COMORB_MEDS]:
        n, dose, rt_ = COMORB_MEDS[c]
        add_med(pid, n, dose, rt_, -60, None, c)
    if ct == "weekly":
        add_med(pid, "cisplatin", "40 mg/m2", "IV weekly", 1, 36, "concurrent chemoradiation", "completed")
    if ct == "q3w":
        add_med(pid, "cisplatin", "100 mg/m2", "IV every 3 weeks", 1, 43, "concurrent chemoradiation", "completed")
        add_med(pid, "fosaprepitant", "150 mg", "IV with each cisplatin dose", 1, 43, "CINV prophylaxis", "completed")
    if ct != "none":
        add_med(pid, "ondansetron", "8 mg", "PO q8h PRN nausea", 1, None, "CINV")
        add_med(pid, "dexamethasone", "8 mg", "PO daily days 2-3 after cisplatin", 1, None, "delayed CINV")
        add_med(pid, "olanzapine", "5 mg", "PO nightly x4 days after cisplatin", 1, None, "CINV")
        add_med(pid, "IV normal saline pre/post-hydration", "1-2 L", "IV with cisplatin", 1, None, "nephroprotection")
        add_med(pid, "magnesium sulfate", "2 g", "IV with cisplatin", 1, None, "cisplatin-related magnesium wasting")
    add_med(pid, "sodium bicarbonate / salt mouth rinse", "15 mL", "swish 4-6x/day", 1, None, "oral care during RT")
    add_med(pid, "fluoride gel 1.1%", "trays", "nightly", -14, None, "radiation caries prevention")
    add_med(pid, "topical moisturizer (radiation skin care)", "thin layer", "BID to neck", 1, None, "radiation dermatitis")
    ex = exams[exams.patient_id == pid]
    m2 = ex[ex.oral_mucositis_grade_ctcae >= 2]
    if not m2.empty:
        t = int(m2.tx_day.iloc[0])
        add_med(pid, "viscous lidocaine-diphenhydramine-antacid mouthwash", "10 mL", "swish/spit q4h PRN", t, None, "mucositis pain")
        add_med(pid, "gabapentin", "300 mg", "PO TID", t, None, "mucositis neuropathic pain")
    m3 = ex[ex.oral_mucositis_grade_ctcae >= 3]
    if not m3.empty:
        t = int(m3.tx_day.iloc[0])
        add_med(pid, "oxycodone oral solution", "5 mg", "PO q4h PRN", t, None, "severe mucositis pain")
        add_med(pid, "senna-docusate", "8.6/50 mg", "PO BID", t, None, "opioid-induced constipation")
    if ex.oral_candidiasis.any():
        t = int(ex[ex.oral_candidiasis].tx_day.iloc[0])
        add_med(pid, "fluconazole", "100 mg", "PO daily x7 days", t, t + 7, "oral candidiasis", "completed")
    e = event_for(pid)
    if e is not None:
        ed = int(e.counterfactual_ed_tx_day)
        if e.event_type == "dehydration":
            add_med(pid, "IV normal saline bolus", "2 L", "IV in ED", ed, ed, "dehydration / AKI", "completed")
            add_med(pid, "IV hydration clinic", "1 L", "IV 3x/week", ed + 1, min(ed + 14, end_rt), "prevent recurrent dehydration", "completed")
        else:
            pcn = "penicillin" in pf.allergies
            add_med(pid, "aztreonam" if pcn else "cefepime", "2 g", "IV q8h", ed, ed + 5, "febrile neutropenia (empiric)", "completed")
            add_med(pid, "filgrastim", "5 mcg/kg", "SC daily until ANC recovery", ed, ed + 4, "febrile neutropenia", "completed")
            add_med(pid, "acetaminophen", "650 mg", "PO q6h PRN fever", ed, None, "fever")
meds = pd.DataFrame(med_rows)

# --------------------------------------------------------------------------- 7. CTCAE toxicities
tox_rows = []


def add_tox(pid, dt, tx_day, term, grade, source):
    if grade >= 1:
        tox_rows.append(dict(patient_id=pid, date=dt, tx_day=int(tx_day), ctcae_v5_term=term, grade=int(grade), source=source))


for x in exams.itertuples():
    ck = week_checkin(x.patient_id, x.tx_day)
    add_tox(x.patient_id, x.date, x.tx_day, "Oral mucositis", x.oral_mucositis_grade_ctcae, "exam")
    add_tox(x.patient_id, x.date, x.tx_day, "Dysphagia", x.dysphagia_grade_ctcae, "exam")
    add_tox(x.patient_id, x.date, x.tx_day, "Dermatitis radiation", x.radiation_dermatitis_grade_ctcae, "exam")
    add_tox(x.patient_id, x.date, x.tx_day, "Dry mouth", x.xerostomia_grade_ctcae, "exam")
    add_tox(x.patient_id, x.date, x.tx_day, "Peripheral sensory neuropathy", x.peripheral_neuropathy_grade_ctcae, "exam")
    if x.hearing == "reports tinnitus":
        add_tox(x.patient_id, x.date, x.tx_day, "Tinnitus", 1, "patient-reported")
    if x.oral_candidiasis:
        add_tox(x.patient_id, x.date, x.tx_day, "Oral cavity fungal infection", 2, "exam")
    if x.tx_day > 0:
        add_tox(x.patient_id, x.date, x.tx_day, "Nausea", 0 if ck["nausea"] < 1 else 1 if ck["nausea"] < 4 else 2 if ck["nausea"] < 7 else 3, "patient-reported")
        add_tox(x.patient_id, x.date, x.tx_day, "Fatigue", 0 if ck["fatigue"] < 2 else 1 if ck["fatigue"] < 5 else 2 if ck["fatigue"] < 8 else 3, "patient-reported")
    v = vitals[(vitals.patient_id == x.patient_id) & (vitals.tx_day == x.tx_day)].iloc[0]
    wl = -v.weight_change_pct
    add_tox(x.patient_id, x.date, x.tx_day, "Weight loss", 0 if wl < 5 else 1 if wl < 10 else 2 if wl < 20 else 3, "vitals")
for pid, g in labs.groupby("patient_id"):
    base_cr = g[(g.tx_day == -6) & (g.test == "creatinine")].value.iloc[0]
    for (dt, tx), gg in g.groupby(["date", "tx_day"]):
        if tx <= 0:
            continue
        v = dict(zip(gg.test, gg.value))
        anc, hgb, plt, na, mg, cr = v["anc"], v["hemoglobin"], v["platelets"], v["sodium"], v["magnesium"], v["creatinine"]
        add_tox(pid, dt, tx, "Neutrophil count decreased", 0 if anc >= 1.5 else 2 if anc >= 1.0 else 3 if anc >= 0.5 else 4, "lab")
        add_tox(pid, dt, tx, "Anemia", 0 if hgb >= 12 else 1 if hgb >= 10 else 2 if hgb >= 8 else 3, "lab")
        add_tox(pid, dt, tx, "Platelet count decreased", 0 if plt >= 150 else 1 if plt >= 75 else 2 if plt >= 50 else 3, "lab")
        add_tox(pid, dt, tx, "Hyponatremia", 0 if na >= 135 else 1 if na >= 130 else 3, "lab")
        add_tox(pid, dt, tx, "Hypomagnesemia", 0 if mg >= 1.7 else 1 if mg >= 1.2 else 2, "lab")
        ratio = cr / base_cr
        add_tox(pid, dt, tx, "Creatinine increased", 0 if ratio <= 1.0 or cr <= 1.2 and ratio < 1.5 else 1 if ratio < 1.5 else 2 if ratio < 3 else 3, "lab")
for e in events[events.should_alert].itertuples():
    p = patients[patients.patient_id == e.patient_id].iloc[0]
    ed = int(e.counterfactual_ed_tx_day)
    dt = tx_date(p, ed).isoformat()
    if e.event_type == "neutropenic_fever":
        add_tox(e.patient_id, dt, ed, "Febrile neutropenia", 3, "ED encounter")
    else:
        add_tox(e.patient_id, dt, ed, "Dehydration", 2, "ED encounter")
        add_tox(e.patient_id, dt, ed, "Acute kidney injury", 1, "ED encounter")
tox = pd.DataFrame(tox_rows).sort_values(["patient_id", "tx_day", "ctcae_v5_term"])

# --------------------------------------------------------------------------- 8. encounters
enc_rows = []


def add_enc(pid, dt, tx_day, kind, dept, reason, disposition="", los=0, natural_course=False):
    enc_rows.append(dict(patient_id=pid, date=dt.isoformat() if hasattr(dt, "isoformat") else dt, tx_day=int(tx_day),
                         encounter_type=kind, department=dept, reason=reason, disposition=disposition,
                         length_of_stay_days=los, natural_course_counterfactual=natural_course))


for p in patients.itertuples():
    pf = PROF.loc[p.patient_id]
    pid = p.patient_id
    add_enc(pid, tx_date(p, -35), -35, "new patient consult", "Head and neck surgery / ENT", "biopsy-proven diagnosis")
    add_enc(pid, tx_date(p, -26), -26, "new patient consult", "Radiation oncology", "treatment planning")
    if chemo_type(p) != "none":
        add_enc(pid, tx_date(p, -25), -25, "new patient consult", "Medical oncology", "concurrent cisplatin")
        add_enc(pid, tx_date(p, -20), -20, "baseline audiogram", "Audiology", "pre-cisplatin hearing baseline")
    add_enc(pid, tx_date(p, -21), -21, "dental evaluation", "Dental oncology", "pre-radiation clearance")
    add_enc(pid, tx_date(p, -19), -19, "baseline swallow evaluation", "Speech-language pathology", "prophylactic swallow exercises")
    add_enc(pid, tx_date(p, -18), -18, "nutrition assessment", "Oncology nutrition", "baseline PG-SGA")
    for v in vitals[(vitals.patient_id == pid) & (vitals.tx_day > 0)].itertuples():
        add_enc(pid, v.date, v.tx_day, "on-treatment visit", "Radiation oncology", "weekly OTV and labs", "home")
    e = event_for(pid)
    if e is not None:
        ed = int(e.counterfactual_ed_tx_day)
        if e.event_type == "dehydration":
            adm = rng.random() < 0.4
            add_enc(pid, tx_date(p, ed), ed, "emergency department", "Emergency medicine",
                    "dehydration, acute kidney injury, poor oral intake",
                    "admitted" if adm else "discharged after IV fluids", int(rng.integers(2, 4)) if adm else 0, True)
        else:
            add_enc(pid, tx_date(p, ed), ed, "emergency department", "Emergency medicine", "fever with neutropenia",
                    "admitted to oncology", int(rng.integers(4, 7)), True)
encounters = pd.DataFrame(enc_rows).sort_values(["patient_id", "tx_day"])

# --------------------------------------------------------------------------- 9. last-known clinical snapshot (one row per patient)
snap = []
for p in patients.itertuples():
    pid = p.patient_id
    ex = exams[exams.patient_id == pid].sort_values("tx_day").iloc[-1]
    nu = nutrition[nutrition.patient_id == pid].sort_values("tx_day").iloc[-1]
    lw = labs[labs.patient_id == pid]
    last_day = lw.tx_day.max()
    lv = dict(zip(lw[lw.tx_day == last_day].test, lw[lw.tx_day == last_day].value))
    snap.append(dict(patient_id=pid, last_exam_date=ex.date, last_exam_tx_day=ex.tx_day, ecog=ex.ecog,
                     last_exam_summary=ex.exam_summary, last_weight_kg=nu.weight_kg,
                     weight_change_pct=nu.weight_change_pct_from_baseline, pg_sga_category=nu.pg_sga_category,
                     last_lab_date=lw[lw.tx_day == last_day].date.iloc[0], last_anc=lv["anc"], last_hgb=lv["hemoglobin"],
                     last_platelets=lv["platelets"], last_sodium=lv["sodium"], last_creatinine=lv["creatinine"],
                     last_albumin=lv["albumin"]))
snapshot = pd.DataFrame(snap)

# --------------------------------------------------------------------------- write
labs_ext = labs_ext.sort_values(["patient_id", "tx_day", "panel", "test"])
orig = labs.assign(encounter=lambda x: np.where(x.tx_day <= 0, "baseline pre-treatment", "weekly on-treatment labs"),
                   panel=lambda x: x.test.map({"wbc": "CBC", "anc": "CBC diff", "hemoglobin": "CBC", "platelets": "CBC",
                                               "sodium": "BMP", "potassium": "BMP", "bun": "BMP", "creatinine": "BMP",
                                               "magnesium": "BMP", "albumin": "CMP"}),
                   specimen="serum", result_text="")
labs_all = pd.concat([orig[labs_ext.columns], labs_ext]).sort_values(["patient_id", "tx_day", "encounter", "panel", "test"])

out = {
    "patient_clinical_profile.csv": prof, "labs_extended.csv": labs_ext, "labs_all.csv": labs_all,
    "physical_exams.csv": exams, "nutrition_assessments.csv": nutrition, "imaging.csv": imaging,
    "medications.csv": meds, "toxicities_ctcae.csv": tox, "encounters.csv": encounters,
    "clinical_snapshot.csv": snapshot,
}
for name, df in out.items():
    df.to_csv(HERE / name, index=False)
    print(f"{name:32s} {len(df):6d} rows")
