#!/usr/bin/env bash
# Install the pc-tools skills by symlinking this repo's skills/ directories.
# Idempotent; edits to the repo take effect immediately.
#
#   ./install.sh                  # into ~/.claude/skills (or $CLAUDE_HOME/skills)
#   ./install.sh --into <repo>    # into <repo>/.claude/skills
#   ./install.sh --force          # replace existing files/links (backed up to <path>.bak)
#   ./install.sh --uninstall      # remove links that point here; leave anything else alone

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${CLAUDE_HOME:-$HOME/.claude}/skills"
FORCE=0
UNINSTALL=0

while [ $# -gt 0 ]; do
  case "$1" in
    --into) mkdir -p "$2"; DEST="$(cd "$2" && pwd)/.claude/skills"; shift ;;
    --force) FORCE=1 ;;
    --uninstall) UNINSTALL=1 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

link() {
  local src="$1" dst="$2"
  if [ "$UNINSTALL" -eq 1 ]; then
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
      rm "$dst"; echo "  removed  $dst"
    fi
    return
  fi
  if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
    echo "  ok       $dst"; return
  fi
  if [ -e "$dst" ] || [ -L "$dst" ]; then
    if [ "$FORCE" -eq 1 ]; then
      rm -rf "$dst.bak"; mv "$dst" "$dst.bak"
      echo "  backed up $dst -> $(basename "$dst").bak"
    else
      echo "  SKIP     $dst already exists (re-run with --force)" >&2; return
    fi
  fi
  ln -s "$src" "$dst"; echo "  linked   $dst"
}

mkdir -p "$DEST"
echo "pc-tools skills -> $DEST"
for dir in "$REPO"/skills/*/; do
  name="$(basename "$dir")"
  link "$REPO/skills/$name" "$DEST/$name"
done
echo
[ "$UNINSTALL" -eq 1 ] && echo "Uninstalled." || echo "Done. Start a new session, then type /<skill-name> in any repo."
