#!/bin/sh
# Start the planner on this computer, reachable only from it (127.0.0.1).
# Used by the Mac mini's launchd agent (docs/mac-mini.md); also fine by hand.
# Settings come from ENV_FILE, default ~/stone-key/stoneplanner.env, which must
# be readable only by you because it holds the login password.
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
env_file=${ENV_FILE:-$HOME/stone-key/stoneplanner.env}
if [ ! -f "$env_file" ]; then
    echo "missing $env_file; see docs/mac-mini.md" >&2
    exit 1
fi
case $(ls -l "$env_file") in
    -rw-------*) ;;
    *) echo "$env_file must be readable only by you: chmod 600 $env_file" >&2; exit 1 ;;
esac
set -a
. "$env_file"
set +a
export FRONTEND_DIST="${FRONTEND_DIST:-$here/frontend/dist}"
cd "$here"
exec "$here/.venv/bin/uvicorn" stoneplanner_api.app:app --host 127.0.0.1 --port "${PORT:-8000}"
