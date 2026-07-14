#!/usr/bin/env bash
# RUNECLAW deploy helper — make .env and data/ survive redeploys.
#
# The 2026-07-14 incident: a redeploy re-cloned the repo directory, which
# wiped BOTH the .env (secrets) and ./data (position state, shadow book).
# The keys were hand-re-entered with quotes → Bitget 40006 → a live AMD
# position sat unprotected. Root cause: mutable state living inside the
# redeploy path.
#
# This script keeps the real .env and data/ OUTSIDE the repo (in a
# persistent directory the redeploy never touches) and symlinks them back
# in on every deploy. Run it once after each redeploy, before starting the
# stack, or wire it into your platform's deploy/start hook.
#
#   ./deploy.sh              # uses ~/runeclaw-persist
#   PERSIST_DIR=/srv/rc ./deploy.sh
#
# Idempotent and non-destructive: an existing real .env/data is MOVED into
# the persistent store on first run (never overwritten if already there),
# then symlinked. A symlink already pointing at the store is left alone.
set -euo pipefail

PERSIST_DIR="${PERSIST_DIR:-$HOME/runeclaw-persist}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "RUNECLAW deploy — persisting state to: $PERSIST_DIR"
mkdir -p "$PERSIST_DIR"

link_persistent() {
  # $1 = name of the path under the repo (e.g. ".env" or "data")
  local name="$1"
  local repo_path="$REPO_DIR/$name"
  local store_path="$PERSIST_DIR/$name"

  if [ -L "$repo_path" ]; then
    # Already a symlink — repoint it at the store to be safe and return.
    ln -sfn "$store_path" "$repo_path"
    echo "  $name -> already linked"
    return
  fi

  if [ -e "$repo_path" ] && [ ! -e "$store_path" ]; then
    # First run with a real file/dir in the repo: move it into the store.
    mv "$repo_path" "$store_path"
    echo "  $name -> moved into persistent store"
  elif [ -e "$repo_path" ] && [ -e "$store_path" ]; then
    # Both exist (fresh clone brought a copy): the store is authoritative.
    rm -rf "$repo_path"
    echo "  $name -> repo copy discarded; persistent store kept"
  fi

  ln -sfn "$store_path" "$repo_path"
  echo "  $name -> linked to persistent store"
}

# data/ must exist as a directory in the store so the symlink resolves.
mkdir -p "$PERSIST_DIR/data"
link_persistent "data"
link_persistent ".env"

if [ ! -e "$PERSIST_DIR/.env" ]; then
  echo ""
  echo "  ⚠  No .env in the persistent store yet."
  echo "     cp $REPO_DIR/.env.example $PERSIST_DIR/.env  and fill in your keys."
  echo "     (No quotes/spaces around values — a quoted key fails auth: 40006.)"
fi

echo "Done. .env and data/ now survive redeploys."
