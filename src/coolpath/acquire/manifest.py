"""Download manifest: source URL, download date and size of every acquired file."""

import json
from datetime import date
from pathlib import Path


def _size(paths) -> int:
    total = 0
    for p in paths:
        p = Path(p)
        total += sum(f.stat().st_size for f in p.rglob("*") if f.is_file()) if p.is_dir() else p.stat().st_size
    return total


def record(cfg: dict, name: str, url: str, paths, **extra) -> dict:
    """Upsert one manifest entry in data/raw/manifest.json and return it."""
    mpath = cfg["paths"]["raw"] / "manifest.json"
    man = json.loads(mpath.read_text()) if mpath.exists() else {}
    paths = [Path(p) for p in (paths if isinstance(paths, (list, tuple)) else [paths])]
    root = cfg["paths"]["raw"].parent.parent
    entry = {"url": url, "downloaded": man.get(name, {}).get("downloaded", date.today().isoformat()),
             "files": [str(p.relative_to(root)) for p in paths], "bytes": _size(paths), **extra}
    man[name] = entry
    mpath.write_text(json.dumps(man, indent=2, default=str))
    return entry
