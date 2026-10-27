"""Pre-push scan of the publish repository (scripts/publish_site.sh scan).

    python scripts/publish_scan.py <repo> <branch> [<branch> ...]

For every file committed on each branch: the Census API key from .env (exact value, never printed), anything
that looks like a 40-character hex key, local absolute paths (/Users/, /home/, /private/, /var/folders/,
C:\\Users), Chinese text, .env files, and files larger than 50 MB. Prints file names and line numbers only.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIMIT = 50 * 1024 * 1024
PATHS = re.compile(rb"/Users/|/home/[a-z]|/private/(tmp|var)|/var/folders/|[A-Za-z]:\\\\?Users\\\\")
HEX40 = re.compile(rb"(?<![0-9a-fA-F])[0-9a-f]{40}(?![0-9a-fA-F])")
CJK = re.compile("[\u4e00-\u9fff\u3000-\u303f\uff00-\uffef]")   # Chinese characters and full-width punctuation


def census_key():
    env = ROOT / ".env"
    if not env.exists():
        return None
    for line in env.read_text().splitlines():
        k, _, v = line.partition("=")
        if k.strip() == "CENSUS_API_KEY" and v.strip():
            return v.strip().strip("'\"").encode()
    return None


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True).stdout


def lines_matching(data, rx=None, needle=None):
    out = []
    for i, line in enumerate(data.split(b"\n"), 1):
        if (needle is not None and needle in line) or (rx is not None and rx.search(line)):
            out.append(i)
    return out


def scan(repo, branch, key):
    files = git(repo, "ls-tree", "-r", "-l", branch).decode().splitlines()
    found = {"census_key": [], "hex40": [], "abs_path": [], "chinese": [], "env_file": [], "over_50MB": []}
    total = 0
    for row in files:
        meta, name = row.split("\t", 1)
        size = int(meta.split()[3])
        total += size
        if Path(name).name.startswith(".env"):
            found["env_file"].append(name)
        if size > LIMIT:
            found["over_50MB"].append(f"{name} ({size / 1e6:.1f} MB)")
        data = git(repo, "show", f"{branch}:{name}")
        if key and key in data:
            found["census_key"].append(f"{name}: lines {lines_matching(data, needle=key)[:10]}")
        if b"\0" not in data[:4096]:
            ln = [i for i, line in enumerate(data.decode("utf-8", "ignore").split("\n"), 1) if CJK.search(line)]
            if ln:
                found["chinese"].append(f"{name}: lines {ln[:10]}{' …' if len(ln) > 10 else ''}")
        for label, rx in (("hex40", HEX40), ("abs_path", PATHS)):
            if b"\0" in data[:4096]:
                hit = bool(rx.search(data))
                if hit:
                    found[label].append(f"{name}: binary file")
                continue
            ln = lines_matching(data, rx=rx)
            if ln:
                found[label].append(f"{name}: lines {ln[:10]}{' …' if len(ln) > 10 else ''}")
    print(f"\n== {branch}: {len(files)} files, {total / 1e6:.1f} MB")
    print(f"   Census key from .env: {'checked' if key else 'NOT CHECKED (no key in .env)'}")
    for k, v in found.items():
        print(f"   {k:11s} {len(v):3d}" + "".join(f"\n      - {x}" for x in v))
    return found


if __name__ == "__main__":
    repo, branches = Path(sys.argv[1]), sys.argv[2:]
    key = census_key()
    for b in branches:
        scan(repo, b, key)
