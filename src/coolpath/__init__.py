"""Shade or Shortcut: shade-aware pedestrian routing for Philadelphia."""

import os
from pathlib import Path

# A conda base env in the user's shell exports PROJ/GDAL data paths that do not match the
# wheels' bundled PROJ database; drop them so rasterio / pyproj use their own copies.
for _v in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "GDAL_DRIVER_PATH"):
    os.environ.pop(_v, None)

import yaml  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]


def load_config(path: Path | str | None = None) -> dict:
    """Read config.yaml; resolve `paths` entries to absolute Paths under ROOT."""
    path = Path(path) if path else ROOT / "config.yaml"
    with open(path) as f:
        cfg = yaml.safe_load(f)
    cfg["paths"] = {k: ROOT / v for k, v in cfg["paths"].items()}
    for p in cfg["paths"].values():
        p.mkdir(parents=True, exist_ok=True)
    return cfg
