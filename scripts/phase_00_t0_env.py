"""T0: import every dependency, print versions, check system tools."""

import importlib
import json
import shutil
import subprocess
import sys

from coolpath import load_config

MODULES = [
    "laspy", "lazrs", "rasterio", "xarray", "rioxarray", "numpy", "numba",
    "geopandas", "shapely", "pyproj", "pyarrow", "osmnx", "networkx",
    "pvlib", "sklearn", "statsmodels", "pandas", "scipy", "joblib",
    "boto3", "requests", "census", "pygris", "matplotlib", "contextily",
    "yaml", "pytest",
]
TOOLS = ["quarto", "tippecanoe"]


def main():
    cfg = load_config()
    out = {"python": sys.version.split()[0], "modules": {}, "tools": {}}
    for m in MODULES:
        try:
            mod = importlib.import_module(m)
            out["modules"][m] = getattr(mod, "__version__", "ok")
        except Exception as e:  # noqa: BLE001
            out["modules"][m] = f"FAIL: {e}"
    import laspy
    out["laz_backends"] = [str(b) for b in laspy.LazBackend.detect_available()]
    for t in TOOLS:
        p = shutil.which(t)
        if p:
            v = subprocess.run([t, "--version"], capture_output=True, text=True)
            out["tools"][t] = (v.stdout or v.stderr).strip().splitlines()[0]
        else:
            out["tools"][t] = None
    for k, v in out["modules"].items():
        print(f"{k:12s} {v}")
    print("laz backends:", out["laz_backends"])
    for k, v in out["tools"].items():
        print(f"{k:12s} {v or 'NOT INSTALLED'}")
    (cfg["paths"]["tables"] / "t0_env.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
