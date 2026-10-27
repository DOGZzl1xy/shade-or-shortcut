"""Solar azimuth / elevation for the study dates and hours (pvlib)."""

import pandas as pd
import pvlib


def sun_table(cfg: dict) -> pd.DataFrame:
    """One row per (date, hour): local time, azimuth (deg clockwise from N), elevation (deg)."""
    st, tz = cfg["study"], cfg["project"]["timezone"]
    times = pd.DatetimeIndex([pd.Timestamp(f"{d} {h:02d}:00", tz=tz) for d in st["dates"] for h in st["hours"]])
    p = st["sun_reference_point"]
    sp = pvlib.solarposition.get_solarposition(times, p["lat"], p["lon"])
    out = pd.DataFrame({"time": times, "date": times.strftime("%Y-%m-%d"), "hour": times.hour,
                        "azimuth": sp["azimuth"].values, "elevation": sp["apparent_elevation"].values})
    out["label"] = out.date.str[5:].str.replace("-", "") + "_" + out.hour.astype(str).str.zfill(2)
    return out.reset_index(drop=True)
