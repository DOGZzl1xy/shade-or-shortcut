"""pytest: clear conda PROJ/GDAL paths before geospatial imports (D-002)."""
import os

for _v in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "GDAL_DRIVER_PATH"):
    os.environ.pop(_v, None)
