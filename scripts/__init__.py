"""Phase entry points. Importing this package first clears conda's PROJ/GDAL paths (D-002)
before any geospatial library (rasterio via contextily, etc.) is loaded."""

import os

for _v in ("PROJ_DATA", "PROJ_LIB", "GDAL_DATA", "GDAL_DRIVER_PATH"):
    os.environ.pop(_v, None)
