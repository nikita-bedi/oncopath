# OncoPath: synthetic head & neck cancer recovery coach data

**All data is synthetic. No real patients.** Wearable fields mirror the WHOOP developer API (recovery, sleep, cycle) so real WHOOP data can be swapped in.

23 patients: 3 scripted demo patients plus a random cohort of 20 (P101–P120). Each generated trajectory has 2 weeks of pre-treatment baseline, 7 weeks of radiation and 1 week after. The dashboard is a point-in-time snapshot dated **2026-10-18**, so patients appear in different treatment weeks according to their start dates; future trajectory rows remain available for detector evaluation. To regenerate: `python generate.py` (seed 42; edit `DEMO_PATIENTS` to change the stories).

## Demo patients

| ID | Patient | Story | Key days (tx day 1 = first radiation fraction) |
|---|---|---|---|
| P001 | Maria, 58F, HPV+ oropharynx, weekly cisplatin | Mouth sores + nausea → drinks less → dehydration | Onset day 15 · wearable drift clear by day 18–19 · labs on day 15 still normal · ED in natural course day 22 (BUN 32, Na 132) |
| P002 | James, 64M, larynx, cisplatin every 3 weeks | Neutrophils drop after cycle 2 → neutropenic fever | Fever starts evening of day 33 · skin temp +1.6 °C, RHR +20 on morning sync day 34 · he messages at 06:15 |
| P003 | Linda, 52F, oral cavity, radiation only | Control: normal fatigue + one late night | Day 10 one-night dip (recovery 24) that should **not** alert |

## Files

| File | Grain | Contents |
|---|---|---|
| `patients.csv` | patient | Demographics, diagnosis, treatment, start dates, story |
| `whoop_recovery.csv` | patient-day | `recovery_score`, `resting_heart_rate`, `hrv_rmssd_milli`, `spo2_percentage`, `skin_temp_celsius` |
| `whoop_sleep.csv` | patient-night | Sleep stage times (ms), `disturbance_count`, `respiratory_rate`, `sleep_performance_percentage`, `sleep_efficiency_percentage` |
| `whoop_cycle.csv` | patient-day | `strain`, `kilojoule`, `average_heart_rate`, `max_heart_rate` |
| `whoop_api_json/P00X.json` | patient | Same data for the demo patients, shaped like WHOOP API responses |
| `checkins.csv` | patient-day | Daily voice check-in: pain, swallow difficulty, nausea, fatigue (0–10), fluid cups, % meals eaten, `transcript` text |
| `labs.csv` | patient-visit-test | Weekly labs (long format): WBC, ANC, Hgb, platelets, Na, K, BUN, creatinine, Mg, albumin, with reference ranges and H/L flags |
| `clinic_vitals.csv` | patient-visit | Weekly weight (and % change), temp, HR, BP |
| `treatment_schedule.csv` | patient-day | Radiation fractions (weekdays) and cisplatin doses |
| `portal_messages.csv` | message | 4 patient messages with true triage level and which data would actually help (one needs no wearable data) |
| `events_ground_truth.csv` | event | Event type, onset, earliest reasonable alert day, counterfactual ED date, `should_alert` |

## Notes

- `tx_day` ≤ 0 is the baseline period. Compare each patient to their own baseline, not to population norms.
- About 4% of days are missing (device off the wrist or a skipped check-in), but never on key event days.
- The dehydration story is the "natural course" with no intervention, so the ED visit actually happens. The demo point is that an alert on day 18–19 would have prevented it.
- The physiology is simplified and tuned to be plausible for a demo. It is not a validated simulation.
