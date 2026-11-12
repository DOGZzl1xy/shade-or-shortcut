"""Solar azimuth / elevation for the study dates and hours (pvlib), and the time labels used everywhere."""

import pandas as pd
import pvlib


def label(date: str, hour: int) -> str:
    """'2025-06-24', 15 -> '0624_15' (the column suffix of every per-time table)."""
    return f"{date[5:7]}{date[8:10]}_{hour:02d}"


def key_labels(cfg: dict) -> list[str]:
    """The key hour (study.key_hour) on every study date; summaries at 'the key hour' pool these."""
    return [label(d, cfg["study"]["key_hour"]) for d in cfg["study"]["dates"]]


def sun_table(cfg: dict) -> pd.DataFrame:
    """One row per (date, hour): local time, azimuth (deg clockwise from N), elevation (deg).

    study.sun_offset_min (default 0) moves the sun position within the hour: -30 puts the sun of hour h at h - 0:30,
    the time SOLWEIG uses for hourly-mean radiation (phase 10), so shade and heat describe the same moment."""
    st, tz = cfg["study"], cfg["project"]["timezone"]
    times = pd.DatetimeIndex([pd.Timestamp(f"{d} {h:02d}:00", tz=tz) for d in st["dates"] for h in st["hours"]])
    sun_times = times + pd.Timedelta(minutes=st.get("sun_offset_min", 0))
    p = st["sun_reference_point"]
    sp = pvlib.solarposition.get_solarposition(sun_times, p["lat"], p["lon"])
    out = pd.DataFrame({"time": times, "sun_time": sun_times, "date": times.strftime("%Y-%m-%d"), "hour": times.hour,
                        "azimuth": sp["azimuth"].values, "elevation": sp["apparent_elevation"].values})
    out["label"] = [label(d, h) for d, h in zip(out.date, out.hour)]
    return out.reset_index(drop=True)
