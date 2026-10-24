# OncoPath: synthetic head & neck cancer recovery coach

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

## Run the OncoPath demo

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8100
```

Open `http://127.0.0.1:8100`. The care-team view is at `/clinician`; the patient
story picker is at `/patient`.

Run all detector, property-based safety, LLM-boundary, FHIR, and UI tests:

```bash
.venv/bin/python -m pytest
```

## Optional Amazon Bedrock assistant

The app works without a model by using deterministic approved-question selection. To
use Bedrock for structured context extraction and question-ID selection, provide normal
AWS credentials with `bedrock:InvokeModel` permission and set:

```bash
export BETWEEN_VISITS_LLM_PROVIDER=bedrock
export BEDROCK_MODEL_ID='<a Bedrock model ID enabled in your AWS account>'
export AWS_REGION='us-east-1'
```

The model cannot write a patient-facing response. It returns a structured question ID,
and the server renders the exact corresponding text from
`config/question_bank.yaml`. Deterministic red-flag escalation runs first and bypasses
the model. Model errors fall back to local rules without blocking the patient's message.
Kiro is the development environment; Bedrock is the optional runtime provider.

## SMART on FHIR and synthetic laboratory data

The clinician view supports a SMART R4 EHR launch. With the app running at port 8100,
open `/smart/setup` and copy the displayed values into the [SMART Health IT
launcher](https://launch.smarthealthit.org/). For the default local setup:

- App Launch URL: `http://localhost:8100/smart/launch?synthetic_patient=P001`
- Redirect URI: `http://localhost:8100/smart/callback`
- Client ID: `between-visits-demo` (override with `SMART_CLIENT_ID`)
- Scope: `launch openid fhirUser patient/*.read`

The default lab source is the public HAPI R4 test server at
`https://hapi.fhir.org/baseR4`. It is shared and can be reset. Reseed all 23 synthetic
patients and 1,840 routine laboratory Observations before a demo with:

```bash
.venv/bin/python scripts/seed_hapi_fhir.py
```

The seed is idempotent and excludes natural-course counterfactual ED results. Set
`FHIR_LAB_REMOTE_ENABLED=false` to force the bundled FHIR-equivalent fallback. The alert
card always identifies whether it is showing live HAPI results or fallback data.

The SMART Health IT sandbox and public HAPI server must receive synthetic test data only.
No real patient data belongs in either service.

## Free Vercel Hobby deployment

Deploy directly from the public GitHub repository. This path uses Vercel's Git
integration, not a deploy hook, API key, or paid model:

1. Sign in to a free Vercel Hobby account and choose **Add New > Project**.
2. Import `https://github.com/nikita-bedi/between-visits`. Connect the Vercel
   GitHub app if Vercel asks for repository access.
3. Keep the repository root as the Root Directory. Vercel detects the FastAPI
   application at `app/main.py`; leave Build and Output settings at their defaults.
4. Do not add model credentials or paid-service secrets. The application defaults to
   `BETWEEN_VISITS_LLM_PROVIDER=rules`, uses public HAPI for synthetic FHIR labs, and
   automatically falls back to the bundled synthetic FHIR observations when HAPI is
   unavailable.
5. Select **Deploy**. In **Project Settings > Environments > Production > Branch
   Tracking**, verify that `main` is the Production Branch.

Vercel's Git integration creates preview deployments for other branches and a production
deployment for every push to `main`. GitHub Actions only runs the test suite; no webhook
secret or separate deployment job is required.

`vercel.json` explicitly keeps `app/static`, `app/templates`, `config`, and `data` in the
FastAPI function bundle. Vercel sets `VERCEL=1` in the function environment, so mutable
SQLite state is written to `/tmp/between_visits.db` instead of the read-only deployment
source. `/tmp` is ephemeral and is not shared between function instances. Chat messages
and alert workflow changes can therefore disappear on a cold start, redeploy, or instance
change. The fixed synthetic cohort and detector alerts load again at startup. Use a
durable managed database before treating workflow state as persistent.

After deployment, open `/smart/setup` on the generated `vercel.app` URL and use the shown
HTTPS launch and callback values in the SMART Health IT launcher. Keep this public demo
limited to synthetic data.

Content was rephrased for compliance with licensing restrictions. See Vercel's official
[FastAPI deployment guide](https://vercel.com/docs/frameworks/backend/fastapi),
[Git deployment guide](https://vercel.com/docs/deployments/git), and
[Python runtime documentation](https://vercel.com/docs/functions/runtimes/python).
