#!/bin/bash
# Usage: deploy/deploy.sh [user@host] [--asr]   — sync code to ~/apps/brainboard and restart the user service.
#   Default host: medmac@lab.um6pmisc.me (sraqzit). The ASR unit is only restarted when asr/ or its unit file
#   changed (restarting it reloads the model), or with --asr. After the restart the deployed files are checked
#   against the local ones, so a sync that silently copied nothing fails loudly.
# First run on the host: create ~/.config/brainboard/server.env (chmod 600) with
#   BRAINBOARD_PASSWORD=…  BRAINBOARD_HOST=0.0.0.0  BRAINBOARD_PORT=2001
#   BRAINBOARD_HOME=/home/<user>/.local/share/brainboard   (older installs: BRAINBOARD_DATA=…/board.json still works)
# Voice input (optional): BRAINBOARD_ASR_URL=http://127.0.0.1:8010/v1/audio/transcriptions, plus HTTPS so browsers
#   allow the mic: BRAINBOARD_TLS_PORT=2002 BRAINBOARD_TLS_CERT=… BRAINBOARD_TLS_KEY=…
#   If ~/miniconda3/envs/qwen3-asr exists, the brainboard-asr unit (asr/server.py) is installed too.
set -euo pipefail
HOST=medmac@lab.um6pmisc.me; ASR=0
for a in "$@"; do case "$a" in --asr) ASR=1 ;; -h|--help) sed -n 2,11p "$0"; exit 0 ;; *) HOST=$a ;; esac; done
cd "$(dirname "$0")/.."

changed=$(rsync -az --delete --itemize-changes --exclude .git --exclude data/ --exclude __pycache__ ./ "$HOST:apps/brainboard/")
echo "${changed:-(no file changes)}"
grep -qE ' (asr/|deploy/brainboard-asr\.service)' <<<"$changed" && ASR=1

ssh "$HOST" ASR=$ASR bash -s <<'EOS'
set -e
mkdir -p ~/.config/systemd/user && cp ~/apps/brainboard/deploy/brainboard.service ~/.config/systemd/user/
systemctl --user daemon-reload && systemctl --user enable --now brainboard && systemctl --user restart brainboard
sleep 1 && systemctl --user --no-pager status brainboard | head -5
if [ -d ~/miniconda3/envs/qwen3-asr ]; then
  cp ~/apps/brainboard/deploy/brainboard-asr.service ~/.config/systemd/user/ && systemctl --user daemon-reload
  systemctl --user enable --now brainboard-asr
  if [ "$ASR" = 1 ]; then systemctl --user restart brainboard-asr
    echo "brainboard-asr restarted (model loads in the background; see journalctl --user -u brainboard-asr)"
  else echo "brainboard-asr left running (asr/ unchanged; pass --asr to force)"; fi
fi
EOS

# verify: deployed files match local ones
files="brainboard/index.html brainboard/server.py brainboard/core.py brainboard/cli.py"
local_sums=$(for f in $files; do printf '%s  %s\n' "$(shasum -a 256 "$f" | cut -d' ' -f1)" "$f"; done)
remote_sums=$(ssh "$HOST" "cd ~/apps/brainboard && sha256sum $files")
if [ "$local_sums" != "$remote_sums" ]; then
  echo "DEPLOY MISMATCH: files on $HOST differ from local:" >&2; diff <(echo "$local_sums") <(echo "$remote_sums") >&2; exit 1
fi
ssh "$HOST" 'systemctl --user is-active --quiet brainboard' || { echo "brainboard is not running on $HOST" >&2; exit 1; }
echo "deployed to $HOST: files verified, brainboard active"
