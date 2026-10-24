# Requirements — OncoPath

## Introduction

OncoPath is a web application that turns wearable data into completed care for
cancer patients during the weeks of chemoradiation between their scheduled clinic
visits. Patients going through head-and-neck chemoradiation deteriorate quietly at
home: dehydration from mucositis, or neutropenic fever after a chemo cycle, often
builds over several days before anyone calls. Wearable physiology (resting heart rate,
HRV, sleep, skin temperature, strain) plus a short daily check-in frequently drift
before labs or a phone call would catch it.

OncoPath compares each patient's current signals against **their own** two-week
pre-treatment baseline, raises an explained alert for the care-team nurse when signals
drift in a clinically meaningful pattern, ranks the nurse's patient list by risk, and
tracks each alert until care is completed. It is **decision support only** — it never
diagnoses, and a clinician makes every clinical decision.

The system is built and demonstrated entirely on **synthetic data** (the bundled
`recovery_coach_data` cohort: 3 scripted demo patients plus a 20-patient random cohort,
each with ~2 weeks of baseline, ~7 weeks of treatment, and a ground-truth event file).
Every screen is labeled "synthetic data."

### Product principles (non-negotiable)

1. **Decision support, not diagnosis.** The system surfaces signals and suggests; it
   never states a diagnosis and never issues an order.
2. **Clinicians decide.** Every clinical action (contact patient, send to care,
   dismiss) is taken by a human on the care team.
3. **Every alert shows its reasons.** No alert exists without a human-readable list of
   the signals that triggered it, each shown against the patient's own baseline.
4. **One bad night is never enough.** A single out-of-range day or night must never, on
   its own, raise an alert. Drift must persist or combine across signals.
5. **Synthetic only.** No real patient data. Every screen is labeled accordingly.

### Users

- **Patient** — on chemoradiation, uses a phone view to log a daily check-in, see a
  simple status, and message the care team.
- **Caregiver** — may view the patient's status and complete check-ins on the patient's
  behalf (shares the patient view, read/assist access).
- **Care-team nurse (clinician)** — uses a dashboard of a risk-ranked patient list,
  reviews alert context cards, acts on alerts, and closes the loop when care is done.

---

## Requirements

### Requirement 1 — Patient onboarding and personal baseline

**User Story:** As a patient, I want the system to learn my own normal from my
pre-treatment weeks, so that alerts reflect changes in me and not a population average.

#### Acceptance Criteria

1. WHEN a patient record is loaded THEN the system SHALL identify the baseline period as
   the days where `tx_day <= 0` (pre-treatment).
2. WHEN the baseline period contains at least a configurable minimum of valid days
   (default 7 of ~14) THEN the system SHALL compute per-signal baseline statistics
   (mean and standard deviation, or median and IQR) for resting heart rate, HRV
   (`hrv_rmssd_milli`), sleep performance, skin temperature, respiratory rate, and
   strain.
3. WHEN the baseline has too few valid days THEN the system SHALL mark the baseline as
   "insufficient" and SHALL NOT raise baseline-relative alerts for that patient, showing
   a clear status instead.
4. WHEN comparing any current value THEN the system SHALL express the deviation relative
   to that patient's own baseline (e.g. z-score or absolute delta), never against other
   patients.

### Requirement 2 — Daily wearable + check-in ingestion

**User Story:** As a patient, I want my wearable data and a short daily check-in to feed
the system, so that the care team sees how I'm actually doing between visits.

#### Acceptance Criteria

1. WHEN synthetic wearable data is ingested THEN the system SHALL load per-day recovery
   (`resting_heart_rate`, `hrv_rmssd_milli`, `spo2_percentage`, `skin_temp_celsius`,
   `recovery_score`), per-night sleep (stage times, `disturbance_count`,
   `respiratory_rate`, `sleep_performance_percentage`), and per-day cycle (`strain`,
   `average_heart_rate`, `max_heart_rate`) keyed by `patient_id` and `date`.
2. WHEN a daily check-in is submitted THEN the system SHALL capture pain (0–10), swallow
   difficulty (0–10), nausea (0–10), fatigue (0–10), fluid cups, and percent of meals
   eaten, matching the `checkins.csv` schema.
3. WHEN a day's wearable record or check-in is missing (device off-wrist or skipped
   check-in; ~4% of days) THEN the system SHALL treat that signal as missing for that
   day and SHALL NOT infer a value or let a gap by itself trigger an alert.
4. WHEN data is ingested THEN the system SHALL support both the flat CSV files and the
   WHOOP-shaped JSON (`whoop_api_json/P00X.json`) for the demo patients, so real WHOOP
   data can be swapped in later.

### Requirement 3 — Drift detection and alerting (with reasons)

**User Story:** As a nurse, I want the system to raise an explained alert when a
patient's signals drift in a known deterioration pattern, so that I can intervene before
it becomes an ED visit.

#### Acceptance Criteria

1. WHEN resting heart rate rises and HRV falls relative to baseline across several
   consecutive days while check-in fluid intake trends down THEN the system SHALL raise
   a **dehydration-pattern** alert.
2. WHEN overnight skin temperature and resting heart rate spike together (with support
   from respiratory rate / SpO2) relative to baseline THEN the system SHALL raise an
   **infection / possible-neutropenic-fever** alert.
3. WHEN any alert is raised THEN the system SHALL attach a reason list, each reason
   naming the signal, its current value, its baseline, and the direction/size of the
   deviation, plus the window over which it persisted.
4. WHEN only a single day or night is out of range THEN the system SHALL NOT raise an
   alert from that alone; a pattern SHALL require persistence across a configurable
   number of days OR corroboration across multiple independent signals.
5. WHEN the detection logic runs against the ground-truth cohort THEN for the **scripted
   demo patients** (P001–P003), where onset is an authored fact, each raised alert SHALL
   NOT fire before the event's `onset_tx_day` and SHALL aim to fire on or before the
   `earliest_reasonable_alert_tx_day`; for the **random cohort** (P101+), whose physiology
   is stochastic, an earlier genuine reason-backed fire is acceptable and reported as
   "early (acceptable)" rather than a violation. In all cases the detector SHALL NOT fire
   for patients whose ground-truth `should_alert` is False (e.g. the
   `false_alarm_confounder` single-night dip on P003/P102).
6. WHEN an alert is created THEN the system SHALL assign a severity/risk score used to
   rank the nurse's patient list.

### Requirement 4 — Clinician dashboard: risk-ranked list and context cards

**User Story:** As a nurse, I want a patient list ranked by risk and a context card per
alert, so that I can triage the sickest patients first with the information I need.

#### Acceptance Criteria

1. WHEN the nurse opens the dashboard THEN the system SHALL display the cohort as a list
   ranked by current risk, with the highest-risk / open alerts at the top.
2. WHEN the nurse selects an alerting patient THEN the system SHALL show a **context
   card** containing a prominent, readable patient summary (age, sex, diagnosis, TNM
   stage, treatment regimen, and treatment day), the CMS care-update fields (see
   Requirement 5), trend plots of the driving signals against baseline, and the reason
   list from Requirement 3.
3. WHEN a patient has no active alert THEN the list SHALL still show their current
   status (baseline OK, insufficient baseline, or watch) without implying risk.
4. WHEN the dashboard renders THEN every screen SHALL display a visible "synthetic data"
   label.

### Requirement 5 — CMS care-update context card and FHIR export

**User Story:** As a nurse, I want the context card to follow the CMS care-update fields
and export as FHIR, so that the information fits our systems and standards.

#### Acceptance Criteria

1. WHEN a context card is shown THEN it SHALL include the CMS care-update fields:
   patient identification/demographics, current clinical status/reason for update,
   relevant observations (the driving wearable signals and check-in values), the
   care-team recommendation framed as decision support, and timing.
2. WHEN the nurse exports a card THEN the system SHALL produce a valid FHIR resource (a
   `Bundle` containing at minimum `Patient`, the relevant `Observation` resources for
   the driving signals, and a `Communication`/`CarePlan`-style summary) as downloadable
   JSON.
3. WHEN exported THEN the FHIR output SHALL carry a flag or tag marking the content as
   synthetic / decision-support, not a diagnosis.

### Requirement 6 — Nearest suitable hospital (MassGIS)

**User Story:** As a nurse, I want the system to suggest the nearest suitable hospital,
so that I can direct a patient to care quickly when an alert warrants it.

#### Acceptance Criteria

1. WHEN an alert warrants escalation THEN the system SHALL suggest the nearest suitable
   hospital using the MassGIS hospitals dataset, given the patient's (synthetic)
   location.
2. WHEN suggesting a hospital THEN the system SHALL show its name, address/distance, and
   why it was chosen, and SHALL frame it as a suggestion the clinician confirms.
3. WHEN no suitable hospital is found within a reasonable range THEN the system SHALL say
   so rather than returning a misleading result.

### Requirement 7 — Alert lifecycle tracking (close the loop)

**User Story:** As a nurse, I want each alert tracked until care is completed, so that no
patient falls through the cracks.

#### Acceptance Criteria

1. WHEN an alert is raised THEN it SHALL enter an `open` state and SHALL be tracked with
   a status lifecycle: `open → acknowledged → in_progress → resolved` (with a
   `dismissed` path for false alarms).
2. WHEN a clinician acts on an alert THEN the system SHALL record who acted, when, the
   action taken, and an optional note, and SHALL advance the status.
3. WHEN an alert is resolved or dismissed THEN the system SHALL retain the full history
   for audit and SHALL remove it from the active risk ranking.
4. WHEN an alert remains open beyond a configurable time THEN the system SHALL visibly
   flag it as overdue.

### Requirement 8 — Care-team chat: conversation, symptom reporting, scoped sharing

**User Story:** As a patient, I want a conversational chat with my care team where I can
report symptoms any time, answer follow-up questions, and approve what wearable data I
share, so that I stay in control and the team gets what they need between visits.

This replaces the one-shot messaging feature with a conversational thread per patient.
The thread carries three kinds of turns: patient turns (free text and/or structured
symptom reports), nurse turns (human follow-ups), and system turns (scripted, non-
diagnostic follow-up questions and fixed safety messages only).

#### Acceptance Criteria

1. WHEN a patient opens the chat THEN the system SHALL present a persistent conversation
   thread with the care team and SHALL let the patient report symptoms (e.g. dizziness,
   nausea) as free text at any time, independent of the structured daily check-in.
2. WHEN a patient message is submitted THEN the system SHALL parse it into structured
   symptom fields (`symptom`, `severity`, `onset`) for the nurse card; IF the parser is
   not confident THEN the system SHALL flag the message for nurse review rather than
   guessing a value.
3. WHEN a patient reports a **soft symptom** (e.g. tired, mild nausea, sore throat, a
   little dizzy) THEN the system SHALL NOT raise an alert from that symptom on its own;
   it SHALL attach the symptom as context on the nurse card where it MAY corroborate a
   wearable or check-in pattern.
4. WHEN a patient message contains any **red-flag symptom** (fever; chills or shaking;
   unable to keep fluids down for 24 hours; fainting or confusion; trouble breathing;
   chest pain; bleeding; unable to swallow saliva) THEN the system SHALL immediately
   escalate an **urgent** item to the care team regardless of what the wearable data
   shows, and SHALL show the patient a fixed safety message ("Contact your care team now.
   If this is an emergency, call 911") that does NOT interpret, triage, or diagnose.
5. WHEN determining red-flag symptoms THEN the system SHALL read the red-flag list from a
   single clinician-reviewed configuration file and SHALL NOT hardcode the list across
   the codebase.
6. WHEN the system asks a follow-up question THEN it SHALL draw only from a fixed
   question bank modeled on published oncology symptom-triage protocols (COSTaRS-style:
   onset, severity 0–10, fluid intake, temperature if known); the system SHALL only ask
   and record answers and SHALL NOT score, triage, or advise in the chat.
7. WHEN the system produces any chat turn THEN its text SHALL come only from the approved
   question bank or the fixed safety messages; the system SHALL NOT emit any other
   free-form text.
8. WHEN follow-up answers are recorded THEN the system SHALL attach them to the relevant
   alert/nurse card so the nurse does not have to chase the information.
9. WHEN a patient sends a message THEN the system SHALL let them review and approve
   sharing only the specific wearable signals/time window relevant to that message before
   it reaches the care team, including a "no data needed" option (mirroring the
   `portal_messages.csv` case where `helpful_wearable_context` is "none needed"), and the
   nurse view SHALL show only approved data and indicate what was shared versus withheld.
10. WHEN scoping a share THEN the system MAY use `helpful_wearable_context` from the
    synthetic messages to pre-select the relevant signals as a suggestion the patient
    confirms.
11. WHEN the chat is displayed THEN it SHALL show a persistent expectations banner stating
    the chat is not monitored 24/7, including the after-hours contact number.
12. WHEN an LLM provider is configured THEN the system MAY use it to extract nurse-facing
    context and select the next follow-up **question ID**, but SHALL NOT use raw model
    text as a patient-facing response; the rendered question SHALL be looked up locally
    from the approved question bank.
13. WHEN the LLM returns an invalid question ID, malformed output, or an error THEN the
    system SHALL fall back to deterministic approved-question selection without blocking
    the patient's message or weakening red-flag escalation.
14. WHEN a red-flag message arrives THEN deterministic red-flag escalation SHALL run
    before and bypass the LLM, so the model can never suppress or delay an urgent item.
    Any model-generated nurse summary SHALL be labeled as a draft requiring verification.

### Requirement 9 — Patient phone view

**User Story:** As a patient, I want a simple phone view, so that I can do my daily
check-in, understand my status in plain language, and chat with my team.

#### Acceptance Criteria

1. WHEN the patient opens the phone view THEN the system SHALL present the daily check-in
   form (pain, swallow, nausea, fatigue, fluid cups, meals %), a plain-language status
   (e.g. "You're tracking okay" / "Your team is looking at your recent readings"), and
   access to the care-team chat (Requirement 8).
2. WHEN showing status THEN the view SHALL NOT present a diagnosis or risk score to the
   patient; it SHALL use reassuring, non-alarming language and SHALL direct urgent
   concerns to contact the care team.
3. WHEN the patient view renders THEN it SHALL be mobile-first and SHALL display the
   "synthetic data" label.

### Requirement 10 — Synthetic cohort and ground truth

**User Story:** As a developer/demoer, I want a synthetic cohort with scripted events and
a ground-truth file, so that I can build and evaluate the system safely and repeatably.

#### Acceptance Criteria

1. WHEN the app runs THEN it SHALL use the bundled synthetic cohort (`recovery_coach_data`:
   23 patients — 3 demo + 20 random — each with ~2 weeks baseline, ~7 weeks treatment,
   ~1 week after) and SHALL NOT require any real patient data.
2. WHEN evaluating detection THEN the system SHALL compare its alerts against
   `events_ground_truth.csv` (dehydration, neutropenic_fever, false_alarm_confounder)
   and report timeliness (fired within `[onset, earliest_reasonable]`) and
   false-alarm rate.
3. WHEN the cohort must be regenerated THEN the bundled `generate.py` (seed 42) SHALL be
   the source of truth for the scripted stories.
4. WHEN any data is displayed anywhere in the app THEN it SHALL be labeled "synthetic
   data."

### Requirement 11 — Safety guardrails across the app

**User Story:** As a care team, we need the product's safety rules enforced everywhere,
so that it stays decision support and clinicians stay in control.

#### Acceptance Criteria

1. WHEN any alert, card, or suggestion is shown THEN the system SHALL present it as
   decision support and SHALL NOT state a diagnosis.
2. WHEN any clinical action is available THEN it SHALL require a human clinician to take
   it; the system SHALL NOT auto-resolve care or make a clinical decision on its own. The
   red-flag chat escalation (Requirement 8.4) is a notification-only safety override: it
   urgently surfaces the message to the care team and shows the patient a fixed safety
   message, and it SHALL NOT constitute a diagnosis, triage score, or clinical decision.
3. WHEN any alert is displayed THEN its reasons SHALL always be visible (no
   reason-less alerts).
4. WHEN detection evaluates a signal THEN a single bad night/day SHALL never, by itself,
   be sufficient to alert (enforced in logic and verified against the false-alarm
   ground-truth cases). A single patient-reported soft symptom (Requirement 8.3) is
   likewise never sufficient on its own.
5. WHEN the system emits any chat turn THEN its text SHALL come only from the approved
   question bank or the fixed safety messages; the system SHALL NOT generate free-form,
   interpretive, or advisory text in the chat.

### Requirement 12 — Deployed AWS demo

**User Story:** As a stakeholder, I want a deployed demo on AWS, so that I can try the
patient phone view and the clinician dashboard live.

#### Acceptance Criteria

1. WHEN the demo is deployed THEN both the patient phone view and the clinician dashboard
   SHALL be reachable at a public URL hosted on AWS.
2. WHEN deployed THEN the deployment SHALL serve the synthetic cohort and SHALL keep the
   "synthetic data" labeling.
3. WHEN deployed THEN the infrastructure SHALL be reproducible (infrastructure-as-code or
   a documented, scripted deploy) so the demo can be stood up again.

### Requirement 13 — SMART on FHIR EMR integration

**User Story:** As a care-team nurse, I want OncoPath to launch in EHR context and
show current laboratory results from FHIR, so that the alert can be reviewed without
manually reconstructing the clinical picture.

#### Acceptance Criteria

1. WHEN launched from an EHR THEN the clinician view SHALL support SMART App Launch R4
   discovery, OAuth authorization-code flow with PKCE, patient context, and a server-side
   token session testable through the SMART Health IT launcher.
2. WHEN loading laboratory data THEN the system SHALL request FHIR R4 `Observation`
   resources with `category=laboratory` and map sodium `2951-2`, potassium `2823-3`,
   creatinine `2160-0`, BUN `3094-0`, magnesium `19123-9`, WBC `6690-2`, ANC `751-8`,
   hemoglobin `718-7`, platelets `777-3`, and albumin `1751-7` by exact LOINC code.
3. WHEN more than one eligible Observation exists for a LOINC code THEN the alert card
   SHALL display the most recent result at or before the alert's simulated date and SHALL
   show its value, unit, collection date, reference range, H/L interpretation, and trend
   versus the previous result.
4. WHEN the latest available laboratory panel is more than five days old THEN the alert
   card SHALL show a freshness warning.
5. WHEN the public HAPI test server is unavailable or reset THEN the synthetic demo SHALL
   use an equivalent bundled-FHIR fallback and clearly identify the active source.
6. WHEN the nurse exports the handoff THEN the system SHALL return a valid FHIR R4
   `Communication` tagged synthetic and decision-support, with clinician verification
   required. The demo SHALL NOT write it into an EHR automatically.
7. WHEN any SMART/FHIR data is displayed or exported THEN it SHALL retain the visible
   synthetic-data label and SHALL NOT be used to weaken deterministic alert safety rules.
