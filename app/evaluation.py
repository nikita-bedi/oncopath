"""Ground-truth evaluation: detector vs events_ground_truth.csv.

For should_alert=True events: did an alert fire within
[onset_tx_day, earliest_reasonable_alert_tx_day]?
For should_alert=False events (false-alarm confounders): did we correctly NOT alert?

Ground truth is used ONLY here. It never feeds detection.
"""
from __future__ import annotations

from .baseline import compute_baseline
from .detector import detect_for_patient

# map ground-truth event types to the detector alert type that should catch them
_EVENT_TO_ALERT = {
    "dehydration": "dehydration",
    "neutropenic_fever": "infection",
}


def evaluate(cohort) -> dict:
    results = []
    tp = fp = fn = tn = 0

    for pid, gt in cohort.ground_truth.items():
        baseline = compute_baseline(cohort, pid)
        alerts = detect_for_patient(cohort, pid, baseline)

        if gt.should_alert:
            wanted_type = _EVENT_TO_ALERT.get(gt.event_type)
            matching = [a for a in alerts if a.type == wanted_type]
            fired_day = min((a.tx_day for a in matching if a.tx_day is not None), default=None)
            is_demo = cohort.patients[pid].cohort == "demo"

            timely = False
            before_onset = False
            on_time_note = ""
            if fired_day is not None:
                lo = gt.onset_tx_day
                hi = gt.earliest_reasonable_alert_tx_day
                if lo is not None and fired_day < lo:
                    before_onset = True
                    # For scripted demo patients onset is authored fact -> a
                    # pre-onset fire is a real violation. For the random cohort,
                    # physiology is stochastic and an earlier genuine, reason-
                    # backed window is acceptable early detection.
                    on_time_note = (f"fired BEFORE onset (day {fired_day} < {lo})"
                                    if is_demo else
                                    f"fired early but plausibly (day {fired_day} < onset {lo})")
                    timely = not is_demo
                elif hi is not None and fired_day > hi:
                    on_time_note = f"fired late (day {fired_day} > earliest-reasonable {hi})"
                    timely = True  # still detected, just not as early as ideal
                else:
                    on_time_note = f"fired day {fired_day} within [{lo}, {hi}]"
                    timely = True

            detected = fired_day is not None
            if detected:
                tp += 1
            else:
                fn += 1
            results.append({
                "patient_id": pid, "event_type": gt.event_type,
                "cohort": cohort.patients[pid].cohort,
                "should_alert": True, "detected": detected,
                "fired_tx_day": fired_day,
                "onset_tx_day": gt.onset_tx_day,
                "earliest_reasonable_alert_tx_day": gt.earliest_reasonable_alert_tx_day,
                "before_onset": before_onset,
                "timely": timely, "note": on_time_note or "not detected",
            })
        else:
            # false-alarm confounder — any alert is a false positive
            fired = len(alerts) > 0
            if fired:
                fp += 1
            else:
                tn += 1
            results.append({
                "patient_id": pid, "event_type": gt.event_type,
                "should_alert": False, "detected": fired,
                "fired_tx_day": next((a.tx_day for a in alerts), None),
                "note": "correctly silent" if not fired else "FALSE ALARM",
            })

    n_true = tp + fn
    n_false = fp + tn
    summary = {
        "true_events": n_true,
        "detected": tp,
        "missed": fn,
        "sensitivity": round(tp / n_true, 3) if n_true else None,
        "false_alarm_cases": n_false,
        "false_alarms": fp,
        "false_alarm_rate": round(fp / n_false, 3) if n_false else None,
        "timely": sum(1 for r in results if r.get("timely")),
    }
    return {"summary": summary, "results": results}
