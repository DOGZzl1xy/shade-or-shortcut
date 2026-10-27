#!/usr/bin/env bash
# Publish the project to its own public GitHub repository (D-048, D-049):
#   main     = code, reports and website source (new history; no data/, no .env)
#   gh-pages = the rendered website, served by GitHub Pages
#
#   scripts/publish_site.sh prepare   render the site and commit both branches in data/publish/ (no network)
#   scripts/publish_site.sh scan      scan both branches: Census key, local absolute paths, .env files, files > 50 MB
#   scripts/publish_site.sh push      create the GitHub repo if needed, push both branches, serve Pages from gh-pages
#                                     (FORCE=1 replaces the remote history, e.g. after rebuilding data/publish from scratch)
set -euo pipefail

OWNER="${OWNER:-DOGZzl1xy}"
REPO="${REPO:-shade-or-shortcut}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MAIN="$ROOT/data/publish/repo"       # main branch working tree
PAGES="$ROOT/data/publish/pages"     # gh-pages worktree of the same repository
# what goes on main (paths relative to the project root); notes/ (working notes) stays in the course repository
KEEP=(src scripts notebooks tests reports annotate site config.yaml pyproject.toml uv.lock .python-version .gitignore README.md LICENSE)
EXCLUDE=(--exclude .DS_Store --exclude __pycache__ --exclude .pytest_cache --exclude .env --exclude _site --exclude .quarto)

case "${1:-}" in
  prepare)
    (cd "$ROOT/site" && QUARTO_PYTHON="$ROOT/.venv/bin/python" quarto render)
    if [ ! -d "$MAIN/.git" ]; then
      mkdir -p "$MAIN" && git -C "$MAIN" init -q -b main
    fi
    # commits in the public repo use the GitHub noreply address, not the local git identity
    git -C "$MAIN" config user.name "$OWNER"
    git -C "$MAIN" config user.email "${AUTHOR_EMAIL:-87157726+DOGZzl1xy@users.noreply.github.com}"
    # main: replace the working tree with the current project files
    find "$MAIN" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    for p in "${KEEP[@]}"; do rsync -a "${EXCLUDE[@]}" "$ROOT/$p" "$MAIN/"; done
    git -C "$MAIN" add -A
    if git -C "$MAIN" rev-parse -q --verify HEAD >/dev/null; then msg="${MSG:-Update code and website ($(date +%Y-%m-%d))}"
    else msg="Shade or Shortcut: code, reports and website source"; fi
    git -C "$MAIN" commit -q -m "$msg" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || echo "main: nothing changed"
    # gh-pages: an orphan branch in a worktree, holding only the build
    if [ ! -d "$PAGES" ]; then
      git -C "$MAIN" worktree add -q --orphan -b gh-pages "$PAGES"
    fi
    find "$PAGES" -mindepth 1 -maxdepth 1 ! -name .git -exec rm -rf {} +
    rsync -a "$ROOT/site/_site/" "$PAGES/"
    touch "$PAGES/.nojekyll"
    git -C "$PAGES" add -A
    git -C "$PAGES" commit -q -m "Website build $(date +%Y-%m-%d)" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>" || echo "gh-pages: nothing changed"
    git -C "$MAIN" log --oneline --all --decorate | head -4
    for b in main gh-pages; do echo "$b: $(git -C "$MAIN" ls-tree -r --name-only "$b" | wc -l | tr -d ' ') files"; done
    ;;
  scan)
    "$ROOT/.venv/bin/python" "$ROOT/scripts/publish_scan.py" "$MAIN" main gh-pages
    ;;
  push)
    [ -d "$MAIN/.git" ] || { echo "run prepare first"; exit 1; }
    if ! gh repo view "$OWNER/$REPO" >/dev/null 2>&1; then
      gh repo create "$OWNER/$REPO" --public \
        --description "Shade or Shortcut: sun and shade on walks from transit in Philadelphia (MUSA 5500)" \
        --homepage "https://$(echo "$OWNER" | tr '[:upper:]' '[:lower:]').github.io/$REPO/"
    fi
    git -C "$MAIN" remote get-url origin >/dev/null 2>&1 || git -C "$MAIN" remote add origin "https://github.com/$OWNER/$REPO.git"
    git -C "$MAIN" push ${FORCE:+--force} -u origin main gh-pages
    gh api "repos/$OWNER/$REPO/pages" >/dev/null 2>&1 ||
      gh api -X POST "repos/$OWNER/$REPO/pages" -f "source[branch]=gh-pages" -f "source[path]=/" >/dev/null
    gh api "repos/$OWNER/$REPO/pages" --jq .html_url
    ;;
  *)
    echo "usage: $0 prepare|scan|push"; exit 2 ;;
esac
