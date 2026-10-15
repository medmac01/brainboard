#!/bin/bash
# Usage: deploy/deploy.sh user@host   — sync code to ~/apps/brainboard and restart the user service.
# First run on the host: create ~/.config/brainboard/server.env (chmod 600) with
#   BRAINBOARD_PASSWORD=…  BRAINBOARD_HOST=0.0.0.0  BRAINBOARD_PORT=2001
#   BRAINBOARD_HOME=/home/<user>/.local/share/brainboard   (older installs: BRAINBOARD_DATA=…/board.json still works)
# Voice input (optional): BRAINBOARD_ASR_URL=http://127.0.0.1:8010/v1/audio/transcriptions, plus HTTPS so browsers
#   allow the mic: BRAINBOARD_TLS_PORT=2002 BRAINBOARD_TLS_CERT=… BRAINBOARD_TLS_KEY=…
#   If ~/miniconda3/envs/qwen3-asr exists, the brainboard-asr unit (asr/server.py) is installed and restarted too.
set -euo pipefail
HOST=${1:?usage: deploy.sh user@host}
cd "$(dirname "$0")/.."
rsync -az --delete --exclude .git --exclude data/ --exclude __pycache__ ./ "$HOST:apps/brainboard/"
ssh "$HOST" 'mkdir -p ~/.config/systemd/user && cp ~/apps/brainboard/deploy/brainboard.service ~/.config/systemd/user/ \
  && systemctl --user daemon-reload && systemctl --user enable --now brainboard && systemctl --user restart brainboard \
  && sleep 1 && systemctl --user --no-pager status brainboard | head -5
  if [ -d ~/miniconda3/envs/qwen3-asr ]; then cp ~/apps/brainboard/deploy/brainboard-asr.service ~/.config/systemd/user/ \
    && systemctl --user daemon-reload && systemctl --user enable --now brainboard-asr && systemctl --user restart brainboard-asr \
    && echo "brainboard-asr restarted (model loads in the background; see journalctl --user -u brainboard-asr)"; fi'
