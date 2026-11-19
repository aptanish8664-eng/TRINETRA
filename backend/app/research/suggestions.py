"""When does the threat monitor SUGGEST something? One place, named constants, plain explanations.

The probability used here is the detector's anomaly confidence for one aircraft, 0 to 1 (`assessment.anomaly_confidence`; the 0-100 threat score is NOT a probability).
A suggestion is only ever shown to the operator as a button. Nothing here (or anywhere) presses it: every action stays the operator's choice, and every action is available at any
time, including below the lowest threshold and with no threat at all.
"""
from __future__ import annotations

from typing import Any, Optional

# Edit these three numbers to change when the page suggests something. They are NOT the detector's own decision threshold; they only decide what the page offers.
SUGGEST_WATCH = 0.30        # at or above: "Watch". The monitor sees GPS behaviour that differs from the other aircraft. Suggestion: keep monitoring (and set a reminder).
SUGGEST_ACT = 0.60          # at or above: "Suspected". Suggestion: take a protective action (reassign the task to a ready aircraft, otherwise hold).
CONFIRMED_AT = 0.85         # at or above: "Confirmed". Suggestion: reassign to a ready aircraft, otherwise return to launch.

LEVELS = ("clear", "watch", "suspected", "confirmed")
HEADLINES = {"clear": "Clear", "watch": "Watch", "suspected": "Suspected", "confirmed": "Confirmed"}
EXPLANATION = {
    "clear": f"Below {SUGGEST_WATCH:.0%}: nothing is suggested. All actions stay available.",
    "watch": f"{SUGGEST_WATCH:.0%} to {SUGGEST_ACT:.0%}: the monitor is unsure. It suggests carrying on and watching, with a reminder if the probability rises.",
    "suspected": f"{SUGGEST_ACT:.0%} to {CONFIRMED_AT:.0%}: the monitor suspects GPS interference. It suggests protecting the task: hand it to a ready aircraft, otherwise hold.",
    "confirmed": f"{CONFIRMED_AT:.0%} or more: the monitor is confident. It suggests handing the task to a ready aircraft, otherwise returning to launch.",
}


def level_for(probability: Optional[float]) -> str:
    if probability is None:
        return "clear"
    if probability >= CONFIRMED_AT:
        return "confirmed"
    if probability >= SUGGEST_ACT:
        return "suspected"
    if probability >= SUGGEST_WATCH:
        return "watch"
    return "clear"


def next_reminder(probability: Optional[float]) -> Optional[float]:
    """The default "tell me again if it rises to" value: the next threshold above the current probability (None at Confirmed)."""
    for threshold in (SUGGEST_WATCH, SUGGEST_ACT, CONFIRMED_AT):
        if probability is None or probability < threshold:
            return threshold
    return None


def suggestion(level: str, aircraft_id: str, probability: float, evidence: list[str], takers: list[str]) -> Optional[dict[str, Any]]:
    """What to offer for the aircraft with the highest probability. `takers` are other aircraft that are ready to take over (best first)."""
    if level == "clear":
        return None
    saw = "; ".join(evidence[:2]) if evidence else "its GPS track differs from what its other sensors and the other aircraft show"
    reason = f"On {aircraft_id} the monitor sees: {saw}. Probability {probability:.0%}."
    if level == "watch":
        return {"action": "continue_monitoring", "label": "Keep monitoring", "reason": reason + " Nothing needs to change yet.", "level": level}
    if takers:
        verb = "Reassign the task to " + takers[0]
        return {"action": "reassign", "label": verb, "taker": takers[0], "reason": reason, "level": level}
    if level == "suspected":
        return {"action": "hold", "label": "Hold (pause the mission)", "reason": reason + " No other aircraft is ready to take over.", "level": level}
    return {"action": "return_to_launch", "label": "Return to launch", "reason": reason + " No other aircraft is ready to take over.", "level": level}
