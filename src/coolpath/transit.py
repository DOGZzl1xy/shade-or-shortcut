"""Home stops for the city sample (D-038): rapid transit + high-frequency surface stops.

Frequency: SEPTA GTFS, one representative weekday (calendar + calendar_dates exceptions), departures
in a time window per stop x route x direction, averaged per hour. A surface stop is high-frequency if
ANY single route-direction reaches the threshold (routes are not added together).
Rapid transit (always included): Regional Rail stations, MFL (L1) / BSL (B1-B3) platforms and the
trolley-tunnel platforms; platforms of one station share a `station` key.
"""

from datetime import date

import geopandas as gpd
import pandas as pd

from coolpath.areas import city

SUBWAY = ["L1", "B1", "B2", "B3"]
# display names for the website: SEPTA Metro names (as in the fall 2026 GTFS route names) with the old name
LINE_NAMES = {"MFL": "L (Market-Frankford)", "BSL": "B (Broad Street)", "Trolley tunnel": "T (trolley tunnel)",
              "Regional Rail": "Regional Rail"}


def station_display(station: str) -> tuple[str, str]:
    """'MFL: 34th St' -> ('34th St', 'L (Market-Frankford)')."""
    line, name = station.split(": ", 1)
    return name, LINE_NAMES.get(line, line)


TUNNEL = ["13th St", "15th St/City Hall", "19th St", "22nd St", "Drexel Station at 30th St", "33rd St",
          "36th St Portal", "36th-Sansom", "37th-Spruce", "40th St Portal"]


def active_services(folder, day: date):
    cal = pd.read_csv(folder / "calendar.txt", dtype={"service_id": str})
    d = int(day.strftime("%Y%m%d"))
    on = set(cal[(cal[day.strftime("%A").lower()] == 1) & (cal.start_date <= d) & (cal.end_date >= d)].service_id)
    cd = pd.read_csv(folder / "calendar_dates.txt", dtype={"service_id": str})
    cd = cd[cd.date == d]
    on |= set(cd[cd.exception_type == 1].service_id)
    on -= set(cd[cd.exception_type == 2].service_id)
    return on


def frequencies(folder, day: date, window):
    """Departures per hour in window for every stop x route x direction on day."""
    trips = pd.read_csv(folder / "trips.txt", dtype={"service_id": str, "trip_id": str, "route_id": str})
    trips = trips[trips.service_id.isin(active_services(folder, day))]
    st = pd.read_csv(folder / "stop_times.txt", usecols=["trip_id", "departure_time", "stop_id"],
                     dtype={"trip_id": str, "stop_id": str, "departure_time": str})
    st = st[(st.departure_time >= window[0]) & (st.departure_time < window[1])]
    st = st.merge(trips[["trip_id", "route_id", "direction_id"]], on="trip_id")
    hours = (pd.Timestamp(f"2000-01-01 {window[1]}") - pd.Timestamp(f"2000-01-01 {window[0]}")).seconds / 3600
    return (st.groupby(["stop_id", "route_id", "direction_id"]).size().rename("per_hour") / hours).reset_index()


def home_stops(cfg) -> gpd.GeoDataFrame:
    """Rapid-transit platforms + surface stops whose best route-direction reaches min_per_hour.

    Columns: stop_id, stop_name, kind (Regional Rail / MFL / BSL / trolley tunnel / surface),
    mode (rapid / bus), station (one key per station; a surface stop is its own station),
    max_route_dir_per_hour (surface stops only)."""
    hs = cfg["trips"]["home_stops"]
    raw, crs = cfg["paths"]["raw"] / "gtfs", cfg["project"]["crs"]
    day = date.fromisoformat(hs["rep_date"])
    stops = pd.read_csv(raw / "google_bus" / "stops.txt", dtype={"stop_id": str})
    stops = stops[stops.location_type.fillna(0).astype(int) == 0]
    rs = pd.read_csv(raw / "google_bus" / "route_stops.txt", dtype={"stop_id": str, "route_id": str})

    rail = pd.read_csv(raw / "google_rail" / "stops.txt", dtype={"stop_id": str}).assign(kind="Regional Rail")
    rail["station"] = "Regional Rail: " + rail.stop_name
    lines = rs[rs.route_id.isin(SUBWAY)].groupby("stop_id").route_id.agg(lambda s: "MFL" if set(s) == {"L1"} else "BSL")
    sub = stops[stops.stop_id.isin(lines.index)].copy()
    sub["kind"] = sub.stop_id.map(lines)
    key = sub.stop_name.str.replace(r"\s+-\s+(NB|SB|EB|WB|MFL|BSL|[BL]\d).*$", "", regex=True).str.strip()
    sub["station"] = sub.kind + ": " + key
    trolley_ids = set(rs[rs.route_id.str.match(r"T\d")].stop_id)
    tun = stops[stops.stop_id.isin(trolley_ids) & stops.stop_name.isin(TUNNEL) & ~stops.stop_id.isin(lines.index)].copy()
    tun["kind"] = "trolley tunnel"
    tun["station"] = "Trolley tunnel: " + tun.stop_name
    rapid = pd.concat([rail, sub, tun], ignore_index=True).assign(mode="rapid")

    f = frequencies(raw / "google_bus", day, hs["window"])
    f = f[~f.route_id.isin(SUBWAY) & ~f.stop_id.isin(set(rapid.stop_id))]
    best = f.groupby("stop_id").per_hour.max()
    best = best[best >= hs["min_per_hour"]]
    surf = stops[stops.stop_id.isin(best.index)].copy()
    surf["max_route_dir_per_hour"] = surf.stop_id.map(best)
    surf = surf.assign(kind="surface", mode="bus", station="bus: " + surf.stop_id)

    cols = ["stop_id", "stop_name", "stop_lat", "stop_lon", "kind", "mode", "station", "max_route_dir_per_hour"]
    d = pd.concat([rapid, surf], ignore_index=True).reindex(columns=cols)
    g = gpd.GeoDataFrame(d, geometry=gpd.points_from_xy(d.stop_lon, d.stop_lat), crs=4326).to_crs(crs)
    return g[g.within(city(cfg).buffer(200))].reset_index(drop=True)
