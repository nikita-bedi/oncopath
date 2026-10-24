# Implementation Plan — Between Visits

- [x] 1. Scaffold project and load the synthetic cohort
  - Create backend (FastAPI) and frontend project structure in the workspace. (Frontend is server-rendered HTML/CSS/JS, not React/Vite — the environment has Python but no Node toolchain; see design.md.)
  - Unzip/stage `recovery_coach_data` and add a loader that reads `patients.csv`, `whoop_recovery.csv`, `whoop_sleep.csv`, `whoop_cycle.csv`, `checkins.csv`, `labs.csv`, `clinic_vitals.csv`, `treatment_schedule.csv`, `portal_messages.csv`, and `events_ground_truth.csv` into typed models.
  - Parse `date`/`tx_day`; mark missing days as missing (no imputation).
  - Add a global "synthetic data" flag/constant surfaced to the frontend.
  - _Requirements: 2.1, 2.2, 2.3, 10.1, 10.4_

- [x] 2. Support WHOOP-shaped JSON ingestion for demo patients
  - Add a loader path that reads `whoop_api_json/P00X.json` and maps it to the same RecoveryDay/SleepNight/CycleDay models.
  - Add a per-patient source toggle (CSV vs WHOOP JSON) to prove real-WHOOP swap-in.
  - Unit test: CSV and JSON produce equivalent normalized records for a demo patient.
  - _Requirements: 2.4_

- [x] 3. Build the per-patient baseline engine
  - Compute per-signal baseline stats (median/IQR + mean/sd) over `tx_day <= 0`, excluding missing days.
  - Implement `sufficient` based on a configurable `min_valid_days` (default 7); mark insufficient baselines.
  - Expose a deviation function (z-score / delta) that is always baseline-relative, never cross-patient.
  - Unit tests: correct stats, threshold behavior, missing-day exclusion.
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 4. Implement the drift detector with reasons
- [ ] 4.1 Dehydration pattern
  - Fire only when RHR elevated AND HRV depressed vs baseline, sustained across N consecutive days (default 3), AND check-in `fluid_cups` trending down.
  - Emit `reasons[]` (signal, current, baseline, deviation, window) and a `risk_score`.
  - _Requirements: 3.1, 3.3, 3.6, 11.3_

- [ ] 4.2 Infection / possible neutropenic fever pattern
  - Fire on overnight `skin_temp_celsius` + resting HR spike vs baseline with `respiratory_rate`/`spo2_percentage` corroboration (multi-signal same-night allowed).
  - Emit reasons + risk score.
  - _Requirements: 3.2, 3.3, 3.6, 11.3_

- [ ] 4.3 Single-bad-night suppression
  - Ensure a lone out-of-range day/night (single signal) cannot alert; require persistence (dehydration) or multi-signal corroboration (infection).
  - Unit test explicitly against the `false_alarm_confounder` cases (P003, P102).
  - _Requirements: 3.4, 11.4_

- [x] 5. Ground-truth evaluation routine and correctness gate
  - Run the detector across the full cohort and compare to `events_ground_truth.csv`.
  - For `should_alert = True`: confirm an alert fires within `[onset_tx_day, earliest_reasonable_alert_tx_day]`.
  - For `should_alert = False`: confirm no alert fires.
  - Report timeliness and false-alarm rate; expose as `GET /eval/ground-truth`.
  - Treat this as the primary correctness test; tune thresholds here (never show ground truth as an alert cause).
  - _Requirements: 3.5, 10.2, 10.3_

- [x] 6. Alert lifecycle service
  - Persist alerts and transitions (`open → acknowledged → in_progress → resolved`, plus `dismissed`) in SQLite.
  - Record actor, timestamp, action, and note per transition; retain history on resolve/dismiss and drop from active ranking.
  - Flag alerts open past a configurable SLA as `overdue`.
  - Ensure no transition happens automatically (clinician-driven only).
  - _Requirements: 7.1, 7.2, 7.3, 7.4, 11.2_

- [x] 7. CMS care-update context card + FHIR export
  - Assemble CMS care-update fields (identity/demographics, status/reason, relevant observations with baselines, decision-support recommendation, timing).
  - Export a FHIR `Bundle` (`Patient` + `Observation` per driving signal + Communication/CarePlan summary) as downloadable JSON.
  - Tag the bundle as synthetic / decision-support (not diagnosis); validate the output in a test.
  - _Requirements: 5.1, 5.2, 5.3, 11.1_

- [x] 8. Nearest suitable hospital (MassGIS)
  - Load the MassGIS hospitals dataset; implement nearest-suitable selection (distance + acute-care/ED filter) from the patient's synthetic location.
  - Return name, address, distance, and selection reason, framed as a clinician-confirmed suggestion.
  - Handle "none within range" explicitly.
  - Tests for nearest result and no-result paths.
  - _Requirements: 6.1, 6.2, 6.3_

- [x] 9. Care-team chat subsystem
- [x] 9.1 Clinician-reviewed config files
  - Create `config/symptom_rules.yaml` with two tiers: `red_flags` (fever; chills/shaking; can't keep fluids 24h; fainting/confusion; trouble breathing; chest pain; bleeding; can't swallow saliva) and `soft_symptoms` (tired, mild nausea, sore throat, a little dizzy, etc.), each with match phrasing/synonyms.
  - Create `config/question_bank.yaml` with COSTaRS-style approved follow-up questions (onset, severity 0–10, fluid intake, temperature if known), each with an id and exact wording.
  - Create the fixed safety-message set (incl. "Contact your care team now. If this is an emergency, call 911").
  - Ensure all code reads these files; no red-flag/soft/question text hardcoded elsewhere.
  - _Requirements: 8.5, 8.6, 8.7_

- [x] 9.2 Conversation thread + turn model
  - Implement ChatThread/ChatTurn persistence (author patient|nurse|system; kind free_text|symptom_report|system_question|safety_message); seed demo threads from `portal_messages.csv`.
  - Add the persistent expectations banner data (not monitored 24/7 + after-hours number).
  - _Requirements: 8.1, 8.11_

- [x] 9.3 Two-tier symptom classifier
  - Classify each inbound patient message against `symptom_rules.yaml`.
  - Soft symptom → attach as nurse-card context only; never alerts on its own, may corroborate a pattern.
  - Red flag → immediate urgent escalation regardless of wearable state + return the fixed safety message verbatim (notification-only, no diagnosis/triage).
  - _Requirements: 8.3, 8.4, 11.2, 11.4_

- [x] 9.4 Free-text symptom parser
  - Extract `symptom`, `severity` (0–10 if stated), `onset` into structured fields for the card.
  - On low confidence, flag the message for nurse review instead of guessing.
  - _Requirements: 8.2_

- [x] 9.5 Scripted follow-up engine
  - Ask follow-ups drawn only from `question_bank.yaml`; record answers; attach them to the alert/nurse card. Never score, triage, or advise.
  - Guard: every system turn's text equals an approved question-bank entry or a fixed safety message.
  - _Requirements: 8.6, 8.7, 8.8_

- [x] 9.6 Scoped data sharing
  - Suggest relevant signals/window (pre-select from `helpful_wearable_context`), require patient approval of exactly what is shared, support "no data needed"; nurse view shows only approved data and shared-vs-withheld.
  - _Requirements: 8.9, 8.10_

- [x] 9.7 Property-based safety tests (critical gate)
  - Using Hypothesis (or equivalent) over generated messages seeded with config terms + random filler:
    - Any message containing a red-flag term always produces an urgent escalation and the exact fixed safety message, for any wearable state.
    - A soft symptom alone never produces an alert/escalation (context only).
    - Every system turn the engine can emit is byte-identical to an approved question-bank entry or fixed safety message (never off-script).
  - Config-source test: behavior changes when `symptom_rules.yaml` changes, with no code edits (no hardcoded duplicates).
  - _Requirements: 8.3, 8.4, 8.5, 8.7, 11.4, 11.5_

- [x] 9.8 Safety-constrained LLM assistant
  - Add a provider-neutral assistant wrapper with Amazon Bedrock support and a deterministic fallback.
  - Run red-flag escalation before/bypassing the model; accept only schema-valid question IDs from the approved bank; never expose raw model text to the patient.
  - Store a short nurse-facing context draft labeled for verification; do not let it affect risk, alert type, or lifecycle.
  - Test approved-ID selection, invalid-ID rejection, provider outage fallback, approved patient-facing output, and red-flag bypass.
  - _Requirements: 8.12, 8.13, 8.14_

- [x] 10. Backend API surface
  - Implement endpoints: `/patients`, `/patients/{id}`, `/patients/{id}/timeseries`, `/patients/{id}/checkins`, `/alerts`, `/alerts/{id}`, `/alerts/{id}/actions`, `/alerts/{id}/fhir`, `/alerts/{id}/hospital`, `/patients/{id}/chat`, `/patients/{id}/chat/followup`, `/patients/{id}/chat/context`, `/config/question-bank`, `/config/symptom-rules`, `/assistant/status`, `/eval/ground-truth`.
  - Return risk-ranked ordering for cohort/alert lists.
  - _Requirements: 3.6, 4.1, 4.2, 5.2, 6.1, 7.2, 8.1, 8.9_

- [x] 11. Patient phone view (frontend)
  - Mobile-first daily check-in form (pain, swallow, nausea, fatigue, fluid cups, meals %) posting to the check-in endpoint.
  - Plain-language, non-diagnostic status; no risk score or diagnosis shown to the patient; direct urgent concerns to the care team.
  - Care-team chat UI: conversation thread, free-text symptom reporting, scripted follow-up answers, scoped-share approval step, persistent expectations banner, and fixed safety message on red-flag.
  - "Synthetic data" label on every patient screen.
  - _Requirements: 9.1, 9.2, 9.3, 8.1, 8.4, 8.9, 8.11, 11.1_

- [x] 12. Clinician dashboard (frontend)
  - Risk-ranked cohort list; non-alerting patients show status without implying risk.
  - Context card: a prominent readable clinical summary (age, sex, TNM stage, diagnosis, regimen), CMS fields, baseline-vs-current trend plots of driving signals, always-visible reason list, nearest-hospital suggestion, lifecycle controls (acknowledge/in_progress/resolve/dismiss with note), FHIR export button, overdue flag.
  - Chat-derived context on the card: parsed patient-reported symptoms with tier, follow-up answers, red-flag urgent escalations, messages flagged for nurse review, and a clearly labeled AI-assisted draft requiring nurse verification.
  - "Synthetic data" label on every clinician screen.
  - _Requirements: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 6.2, 7.1, 7.2, 7.4, 8.4, 8.8, 11.1, 11.2, 11.3_

- [x] 13. Safety-guardrail pass across the app
  - Audit all alerts/cards/suggestions/chat turns: decision-support framing, no diagnosis, reasons always visible, no auto clinical actions, chat never off-script.
  - Confirm the red-flag escalation is notification-only (no diagnosis/triage/decision) and consistent with the "clinicians decide" rule.
  - Add a test/checklist verifying each guardrail (incl. single-bad-night rule, soft-symptom-alone rule, reason-presence, and question-bank-only chat output).
  - _Requirements: 11.1, 11.2, 11.3, 11.4, 11.5_

- [x] 14. Deploy the demo with Vercel Hobby Git integration
  - Configure the existing FastAPI ASGI app for one Vercel Function and bundle the
    server-rendered frontend, config, and synthetic data.
  - Import the public GitHub repository into Vercel with `main` as the Production Branch;
    branch pushes create previews and every `main` push creates a production deployment.
  - Keep deployment free and secretless: deterministic assistant by default, public HAPI
    labs with bundled fallback, and no deploy webhook or paid model credentials.
  - Store SQLite under ephemeral `/tmp` on Vercel while preserving the local data path;
    document that mutable demo state can reset and is not shared across instances.
  - _Requirements: 12.1, 12.2, 12.3, 10.4_

- [x] 15. SMART on FHIR EMR integration
  - Implement SMART R4 EHR launch discovery, OAuth code exchange with PKCE, server-side token sessions, issuer allowlisting, setup instructions, and disconnect/status routes.
  - Seed all 23 synthetic patients and 1,840 routine mapped labs to public HAPI R4 with stable IDs; exclude counterfactual ED rows and provide a repeatable script.
  - Read `Observation?category=laboratory`, map the ten required LOINCs, select latest + previous per code, show reference ranges/flags/trends, and warn when the latest panel is over five days old.
  - Keep an explicitly labeled bundled-FHIR fallback for public-sandbox resets.
  - Export a valid standalone FHIR R4 `Communication` nurse handoff; no automatic write-back.
  - Add property-based latest-per-LOINC tests, SMART PKCE/allowlist tests, FHIR schema tests, seed tests, API tests, and UI smoke coverage.
  - Document production CDS Hooks `patient-view`, Bulk FHIR nightly refresh, and approval-gated write-back.
  - _Requirements: 13.1–13.7_

- [x] 16. Restore visible wearable sharing and prepare free deployment
  - Replace the collapsed wearable control with an always-visible per-message consent panel for RHR, HRV, skin temperature, sleep, and strain.
  - Show current values, allow 3/7/14-day windows, persist exact consent, surface it to nurses, and reset selections after each send.
  - Add property and UI tests for exact signal/window persistence and all five controls.
  - Keep the no-paid-LLM default and document ephemeral demo-state limitations.
  - _Requirements: 8.9, 8.10, 9.1, 12.1, 12.3_

- [x] 17. Keep CI test-only and use Vercel native Git deployment
  - Run GitHub Actions CI for Python 3.11 with offline-safe FHIR and deterministic-assistant settings on pushes and pull requests.
  - Remove the Render deploy-hook job, secret, Blueprint, API deploy script, and Procfile.
  - Let the connected Vercel Hobby project create previews and deploy every `main` push;
    no webhook secret or separate deployment job is needed.
  - Test the Vercel entry/config, required bundle paths, default free assistant, CI contract,
    and local-versus-Vercel SQLite path behavior.
  - _Requirements: 12.1, 12.3_
