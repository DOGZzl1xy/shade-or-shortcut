"""Helpers shared by the website notebooks: table / figure loading and the dark chart style."""

import io
import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd
from IPython.display import Image, Markdown
from PIL import Image as PILImage

from coolpath import ROOT

TABLES = ROOT / "reports" / "tables"
FIGS = ROOT / "reports" / "figures"
WEB = ROOT / "data" / "interim" / "web_figures"     # downsized copies for the website (not in git)
C = {"sun": "#ff8a3d", "bldg": "#5b8cff", "tree": "#43c27a", "indoor": "#b48cff", "cool": "#35d0e0",
     "ink": "#e8e6df", "dim": "#8b8a84", "bg": "#07080b", "line": "#2a2c31"}

plt.rcParams.update({
    "figure.facecolor": C["bg"], "axes.facecolor": C["bg"], "savefig.facecolor": C["bg"],
    "axes.edgecolor": C["line"], "axes.labelcolor": C["ink"], "text.color": C["ink"],
    "xtick.color": C["dim"], "ytick.color": C["dim"], "grid.color": C["line"], "axes.grid": True,
    "axes.spines.top": False, "axes.spines.right": False, "font.size": 11, "legend.frameon": False,
    "figure.dpi": 110,
})


def table(name):
    p = TABLES / name
    return json.loads(p.read_text()) if p.suffix == ".json" else pd.read_csv(p)


def md(df, floatfmt=".1f"):
    return Markdown(df.to_markdown(index=False, floatfmt=floatfmt))


def fig(name, width=None, max_px=1600):
    """A map figure from the reports for the web: at most max_px wide, as a JPEG (the report PNGs are up to 4 MB)."""
    WEB.mkdir(parents=True, exist_ok=True)
    out = WEB / (Path(name).stem + ".jpg")
    src = FIGS / name
    if not out.exists() or out.stat().st_mtime < src.stat().st_mtime:
        im = PILImage.open(src).convert("RGB")
        if im.width > max_px:
            im = im.resize((max_px, round(im.height * max_px / im.width)), PILImage.LANCZOS)
        im.save(out, "JPEG", quality=85, optimize=True, progressive=True)
    return Image(filename=str(out), width=width)


def chart(f, dpi=150):
    """A statistical chart as a compressed PNG: palette-quantised (charts use few colours), so text stays sharp."""
    buf = io.BytesIO()
    f.savefig(buf, format="png", dpi=dpi, bbox_inches="tight")
    plt.close(f)
    im = PILImage.open(buf).convert("RGB").quantize(colors=128, method=PILImage.Quantize.MEDIANCUT, dither=PILImage.Dither.NONE)
    out = io.BytesIO()
    im.save(out, format="PNG", optimize=True)
    return Image(data=out.getvalue(), format="png", width=round(f.get_figwidth() * 96))


def pct(x, d=1):
    return f"{100 * x:.{d}f}%"
