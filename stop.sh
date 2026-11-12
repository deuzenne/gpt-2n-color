#!/bin/zsh
set -euo pipefail
cd -- "$(dirname -- "$0")"
if [[ -f data/mac-agent.pid ]]; then
  old="$(cat data/mac-agent.pid)"
  if [[ "$old" == <-> ]] && kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null || true
  fi
  rm -f data/mac-agent.pid
fi
docker compose down
echo "2N Color arrêté (Colima reste démarré)."
