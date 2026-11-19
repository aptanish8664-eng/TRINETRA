"""Excel export of Attack Lab live run records (same layout idea as the Part 0 dataset export: README sheet first, then tables)."""
from __future__ import annotations

import io
from typing import Any

import xlsxwriter

README_ROWS = [
    ("What this is", "Run records of Attack Lab experiments that ran automatically during simulated flights (software only)."),
    ("Source", "Every row was measured by TRINETRA's live threat monitor during a PX4 SITL or simulator flight. Nothing here is real flight evidence."),
    ("Attack", "A simulated GPS fault applied to the monitor's software copy of the GPS stream. The aircraft itself kept its real position."),
    ("Times", "Seconds of flight time (since the aircraft armed). 'latency_s' = first Medium-or-higher assessment minus the scheduled attack start."),
    ("Outcome", "detected / missed / not_reached (the flight ended before the attack began) / no_attack (baseline)."),
    ("False alarms", "Episodes of a Medium-or-higher assessment outside every attack window (plus 30 s of alert hold)."),
    ("Caveat", "One run is one sample. These are measurements from single flights, not a performance guarantee."),
]
RUN_COLUMNS = ["result_id", "experiment", "experiment_deleted", "scenario_version_id", "execution_id", "started_at", "ended_at", "run_seconds", "end_reason",
               "outcome", "false_alarms", "attacks", "operator_responses", "assessments_recorded", "seed", "config_hash"]
ATTACK_COLUMNS = ["result_id", "experiment", "target_aircraft", "type", "start_s", "duration_s", "magnitude", "direction_deg", "reached", "fully_run", "outcome",
                  "first_reaction_s", "first_reaction_tier", "latency_s", "first_alert_s", "first_alert_latency_s", "peak_score", "peak_tier"]
RESPONSE_COLUMNS = ["result_id", "experiment", "at", "after_run_start_s", "aircraft_id", "action", "tier", "threat_score", "by"]
STORY_COLUMNS = ["result_id", "experiment", "at", "kind", "aircraft", "by", "text"]
TIMELINE_COLUMNS = ["result_id", "experiment", "aircraft_id", "flight_time_s", "threat_score", "tier", "attack_active"]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    return value if isinstance(value, (int, float, str, bool)) else str(value)


def build_workbook(results: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    book = xlsxwriter.Workbook(buffer, {"in_memory": True, "nan_inf_to_errors": True})
    head = book.add_format({"bold": True, "bg_color": "#D9E2F3", "border": 1, "text_wrap": True, "valign": "top"})
    wrap = book.add_format({"text_wrap": True, "valign": "top"})

    def sheet(name: str, columns: list[str], rows: list[list[Any]], widths: int = 18) -> None:
        ws = book.add_worksheet(name)
        for column, title in enumerate(columns):
            ws.write(0, column, title, head)
        for row_number, row in enumerate(rows, start=1):
            for column, value in enumerate(row):
                ws.write(row_number, column, _cell(value))
        ws.freeze_panes(1, 0)
        ws.set_column(0, len(columns) - 1, widths)

    readme = book.add_worksheet("README")
    readme.set_column(0, 0, 18)
    readme.set_column(1, 1, 110)
    for index, (key, text) in enumerate(README_ROWS):
        readme.write(index, 0, key, head)
        readme.write(index, 1, text, wrap)
    live = [item for item in results if (item.get("result") or {}).get("kind") == "live_run"]
    readme.write(len(README_ROWS) + 1, 0, "Records", head)
    readme.write(len(README_ROWS) + 1, 1, f"{len(live)} live run record(s) of {len(results)} stored result(s).", wrap)
    runs, attacks, responses, timeline, story = [], [], [], [], []
    for item in live:
        record = item["result"]
        name = item["experiment_name"]
        runs.append([item["id"], name, "yes" if item.get("from_deleted_experiment") else "no", item["scenario_version_id"], record.get("execution_id"),
                     record.get("started_at"), record.get("ended_at"), record.get("run_seconds"), record.get("end_reason"), record.get("outcome"),
                     record.get("false_alarms"), len(record.get("attacks", [])), len(record.get("operator_responses", [])), record.get("assessments_recorded"),
                     record.get("seed"), record.get("config_hash")])
        for attack in record.get("attacks", []):
            attacks.append([item["id"], name] + [attack.get(column) for column in ATTACK_COLUMNS[2:]])
        for response in record.get("operator_responses", []):
            responses.append([item["id"], name] + [response.get(column) for column in RESPONSE_COLUMNS[2:]])
        for event in record.get("attack_story", []):
            story.append([item["id"], name] + [event.get(column) for column in STORY_COLUMNS[2:]])
        for aircraft, points in (record.get("timeline") or {}).items():
            for point in points:
                timeline.append([item["id"], name, aircraft, point["t"], point["score"], point["tier"], point["attacked"]])
    sheet("Runs", RUN_COLUMNS, runs)
    sheet("Attacks", ATTACK_COLUMNS, attacks)
    sheet("Operator responses", RESPONSE_COLUMNS, responses)
    sheet("Attack story", STORY_COLUMNS, story, 30)
    sheet("Timeline", TIMELINE_COLUMNS, timeline, 14)
    book.close()
    return buffer.getvalue()
