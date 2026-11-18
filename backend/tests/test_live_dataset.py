"""Live clean flights are added to a training dataset by batch (never by row), on the 0.5 s grid, with no ground truth."""
import json

import numpy as np
import pandas as pd

from app.research import live_dataset as ld
from app.research.threat_data import FEATURE_COLUMNS_ALL


def test_batches_are_read_in_order(tmp_path):
    path = tmp_path / "batches.csv"
    path.write_text("100.0,8.0,area_survey,5,100.0\nEND,200.0\n300.0,12.0,search_spiral,5,100.0\nEND,400.0\n500.0,4.0,perimeter_patrol,5,60.0\n", encoding="utf-8")
    batches = ld.read_batches(path)
    assert [(b["speed"], b["type"]) for b in batches] == [(8.0, "area_survey"), (12.0, "search_spiral")]      # the unfinished third batch is dropped


def make_rows(path, flights=2, seconds=30):
    with path.open("w", encoding="utf-8") as handle:
        for flight in range(flights):
            for second in range(0, seconds, 3):
                ctx = {"aircraft_id": "px4-1", "flight_idx": flight, "t": 10.0 + second, "battery_pct": 90, "rates_hz": {"GPS_RAW_INT": 10.0, "LOCAL_POSITION_NED": 24.0},
                       "mission": None, "gap_restart": [], "alt_m": 30.0, "ground_speed_m_s": 8.0, "heading_deg": 90.0}
                handle.write(json.dumps({"ctx": ctx, "features": {name: 0.1 for name in FEATURE_COLUMNS_ALL}}) + "\n")


def test_live_rows_go_to_the_split_of_their_batch_on_a_half_second_grid(tmp_path):
    dataset = tmp_path / "ds"
    dataset.mkdir()
    columns = ["flight_id", "aircraft_id", "t", *FEATURE_COLUMNS_ALL, "battery_pct", "role", "remaining_fraction", "position_error_m"]
    pd.DataFrame(columns=columns).to_csv(dataset / "features.csv", index=False)
    pd.DataFrame(columns=["flight_id", "aircraft_id", "t", "is_attacked", "attack_type"]).to_csv(dataset / "labels_HIDDEN.csv", index=False)
    pd.DataFrame(columns=["flight_id", "seed", "split", "cell", "attack_type", "tier", "hard_case", "n_aircraft", "duration_s", "attack"]).to_csv(dataset / "flights.csv", index=False)
    jsonl = tmp_path / "live.jsonl"
    make_rows(jsonl)
    batches = [{"speed": 8.0, "type": "area_survey"}, {"speed": 12.0, "type": "search_spiral"}]
    frame = ld.assign_batches(ld.load_live_rows(jsonl), batches)
    added = ld.append_live_clean(dataset, frame, {0: "train", 1: "test"})
    assert added["train"] > 0 and added["test"] > 0 and added["val"] == 0
    flights = pd.read_csv(dataset / "flights.csv")
    assert set(flights.split) == {"train", "test"} and set(flights.cell) == {"live_clean"}
    features = pd.read_csv(dataset / "features.csv")
    assert (np.diff(features[features.flight_id == flights.flight_id[0]].t) == 0.5).all()        # zero-order hold on the 0.5 s grid
    labels = pd.read_csv(dataset / "labels_HIDDEN.csv")
    assert not labels.is_attacked.any() and features.position_error_m.isna().all()                # clean, and no simulator truth
    assert not (set(features[features.flight_id.isin(flights[flights.split == "train"].flight_id)].flight_id) & set(features[features.flight_id.isin(flights[flights.split == "test"].flight_id)].flight_id))
