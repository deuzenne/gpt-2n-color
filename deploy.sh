#!/bin/zsh
# Déploiement natif sur Mac + conteneur Colima.
set -euo pipefail
cd -- "$(dirname -- "$0")"
for c in colima docker python3; do
  command -v "$c" >/dev/null || { echo "Commande absente : $c" >&2; exit 1; }
done
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose v2 requis (docker compose)." >&2
  exit 1
fi
if [[ ! -f .env ]]; then
  cp .env.example .env
  python3 - <<'PY'
from pathlib import Path
import secrets
p=Path(".env")
s=p.read_text().replace("GENERATED_ON_DEPLOY", secrets.token_hex(32))
p.write_text(s)
p.chmod(0o600)
PY
fi
if [[ $# -gt 0 ]]; then
  python3 - "$1" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]).expanduser().resolve()
if not root.is_dir():
    raise SystemExit(f"MEDIA_ROOT inexistant : {root}")
p=Path(".env")
lines=[("MEDIA_ROOT="+str(root)) if l.startswith("MEDIA_ROOT=") else l for l in p.read_text().splitlines()]
p.write_text("\n".join(lines)+"\n")
p.chmod(0o600)
PY
fi
# Lire les clés littéralement : un chemin média peut contenir des espaces.
MEDIA_ROOT="$(sed -n 's/^MEDIA_ROOT=//p' .env | tail -n 1)"
PORT="$(sed -n 's/^PORT=//p' .env | tail -n 1)"
AGENT_TOKEN="$(sed -n 's/^AGENT_TOKEN=//p' .env | tail -n 1)"
export MEDIA_ROOT PORT AGENT_TOKEN
export HOST_UID="$(id -u)" HOST_GID="$(id -g)"
if [[ ! -d "$MEDIA_ROOT" ]]; then
  echo "MEDIA_ROOT absent : $MEDIA_ROOT" >&2; exit 1
fi
if [[ "${AGENT_TOKEN:-}" == "GENERATED_ON_DEPLOY" || -z "${AGENT_TOKEN:-}" ]]; then
  echo "AGENT_TOKEN invalide dans .env" >&2; exit 1
fi
if ! colima status >/dev/null 2>&1; then
  echo "Démarrage de Colima…"
  colima start
fi
docker context use colima >/dev/null 2>&1 || true
echo "Montage : $MEDIA_ROOT → /library (lecture seule)"
docker compose up -d --build
mkdir -p data
if [[ -f data/mac-agent.pid ]]; then
  old="$(cat data/mac-agent.pid)"
  if [[ "$old" == <-> ]] && kill -0 "$old" 2>/dev/null; then
    kill "$old" 2>/dev/null || true
  fi
  rm -f data/mac-agent.pid
fi
nohup python3 -u mac_agent.py >> data/mac-agent.log 2>&1 < /dev/null &
echo "$!" > data/mac-agent.pid
echo ""
echo "2N Color : http://127.0.0.1:${PORT:-8765}"
echo "Agent macOS PID : $(cat data/mac-agent.pid)"
echo "Logs agent : tail -f data/mac-agent.log"
echo "Pour arrêter : ./stop.sh"
echo "Si les fichiers sont absents, vérifier les mounts Colima."
