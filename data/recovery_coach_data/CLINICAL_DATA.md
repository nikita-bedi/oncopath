# Extended clinical data (synthetic)

**All data is synthetic. No real patients. Not a validated clinical simulation.**

These files extend the original OncoPath dataset with the clinical record a cancer care team would have. They are keyed on `patient_id` and `tx_day` (tx day 1 = first radiation fraction), exactly like the original files. The original files are unchanged.

To regenerate: `python generate_clinical.py` (seed 7). It reads the original CSVs, so run `generate.py` first if you change the stories.

## How the new data follows the existing stories

| Patient | What the new data shows |
|---|---|
| P001 dehydration | Weekly labs, exam and nutrition stay normal on tx day 15 (the wearable is the only early signal). On the natural-course ED day 22: BUN/Cr ratio > 20, low bicarbonate, urine specific gravity > 1.030, urine ketones, low urine sodium, eGFR drop, positive orthostatics, dry mucous membranes, PG-SGA category C. |
| P002 neutropenic fever | ED panel on tx day 34: ANC < 0.5 (flag `LL`), CRP > 90, procalcitonin > 0.5, lactate > 2, blood cultures growing E. coli, clear chest X-ray. Penicillin allergy, so empiric aztreonam, plus filgrastim. No home thermometer (matches his message). |
| P003 control | No ED visit. Expected radiation effects only (mucositis, dermatitis, falling lymphocytes, slowly falling prealbumin). |
| Cohort events | Every `should_alert = True` event in `events_ground_truth.csv` gets a matching ED panel, encounter, and medications on its `counterfactual_ed_tx_day`. |

## Files

| File | Grain | Contents |
|---|---|---|
| `labs_all.csv` | patient-visit-test | **Use this one.** Original 10 tests plus every new test, in one long table. |
| `labs_extended.csv` | patient-visit-test | New tests only (see list below). |
| `physical_exams.csv` | patient-visit | Pre-treatment H&P and weekly on-treatment exams: ECOG, vitals incl. RR and SpO2, orthostatics, hydration signs, CTCAE grades for mucositis, dysphagia, dermatitis, xerostomia and neuropathy, candidiasis, nodes, hearing, feeding tube, summary sentence. |
| `nutrition_assessments.csv` | patient-visit | Weight, height, BMI, % weight change, 7-day intake from check-ins, energy and protein need vs intake, fluid goal, diet texture, feeding route, supplements, PG-SGA score/category/triage, GLIM malnutrition. |
| `imaging.csv` | study | Staging PET/CT, MRI, chest CT (smokers), dental panorex, CT simulation, chest X-ray at neutropenic fever, modified barium swallow when dysphagia reaches grade 3, and the scheduled 12-week post-treatment PET/CT. |
| `medications.csv` | patient-medication | Chemotherapy, antiemetics, hydration, mouth and skin care, pain ladder by mucositis grade, antifungals, event treatment (IV fluids, antibiotics, filgrastim), and home medications for comorbidities. |
| `toxicities_ctcae.csv` | patient-visit-term | CTCAE v5 adverse events, grade ≥ 1, from exam, labs, patient report and ED encounters. |
| `encounters.csv` | encounter | Consults (ENT, radiation oncology, medical oncology, dental, audiology, speech-language pathology, nutrition), weekly visits, and natural-course ED visits/admissions (`natural_course_counterfactual = True`). |
| `patient_clinical_profile.csv` | patient | Height, BMI, ECOG, HPV/EBV status, smoking, alcohol, comorbidities, allergies, language, insurance, **home town, county, lat/lon, distance and drive time to the treating center**, broadband, caregiver, home thermometer, transportation, port, PEG, regimen. |
| `clinical_snapshot.csv` | patient | Latest exam and labs per patient, for the nurse context card. |

## Lab tests added

| Panel | Tests |
|---|---|
| CBC and differential | RBC, hematocrit, MCV, MCH, MCHC, RDW, MPV, absolute lymphocytes, monocytes, eosinophils, basophils |
| Chemistry (CMP/BMP) | Chloride, bicarbonate, anion gap, glucose, calcium, albumin-corrected calcium, phosphate, total protein, uric acid |
| Liver | AST, ALT, alkaline phosphatase, total bilirubin |
| Kidney | eGFR (CKD-EPI 2021, race-free), BUN/creatinine ratio |
| Inflammation and sepsis | CRP, procalcitonin, lactate |
| Nutrition | Prealbumin (weekly), vitamin D (baseline) |
| Baseline only | PT/INR, aPTT, TSH, free T4, HbA1c, ferritin, LDH, hepatitis B (HBsAg, anti-HBc), hepatitis C antibody, HIV Ag/Ab, serum hCG (women under 55), urinalysis |
| Tumor markers | Plasma EBV DNA (nasopharynx), circulating tumor HPV DNA (oropharynx) |
| ED only | Serum osmolality, urine specific gravity, urine ketones, urine sodium, blood cultures x2, urine culture, respiratory viral panel |
| End of treatment | TSH repeat |

Flags: `L` low, `H` high, `LL` critical low (ANC < 0.5), `HH` critical high (lactate ≥ 4), `A` abnormal text result (e.g. positive culture). Text results are in `result_text`; numeric results in `value`.

## Notes and limits

- ED visits are the **natural course with no intervention**, matching the original dataset. The demo point is that OncoPath's alert would have prevented them.
- Lymphocytes fall steadily during radiation, which is expected. The differential is generated independently, so it may not sum exactly to WBC.
- PG-SGA is a simplified score from weight loss, intake, symptoms, activity and age; it is not the full instrument.
- Town coordinates are approximate town centers with small random offsets; drive distance is straight-line distance × 1.3.
- In the original `patients.csv`, some cohort names don't match the recorded sex (e.g. P101). Calculations here use the recorded sex.
