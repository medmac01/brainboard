#!/bin/bash
# Usage: deploy/deploy.sh user@host   — sync code to ~/apps/brainboard and restart the user service.
# First run on the host: create ~/.config/brainboard/server.env (chmod 600) with
#   BRAINBOARD_PASSWORD=…  BRAINBOARD_HOST=0.0.0.0  BRAINBOARD_PORT=2001
#   BRAINBOARD_DATA=/home/<user>/.local/share/brainboard/board.json
set -euo pipefail
HOST=${1:?usage: deploy.sh user@host}
cd "$(dirname "$0")/.."
rsync -az --delete --exclude .git --exclude data/ --exclude __pycache__ ./ "$HOST:apps/brainboard/"
ssh "$HOST" 'mkdir -p ~/.config/systemd/user && cp ~/apps/brainboard/deploy/brainboard.service ~/.config/systemd/user/ \
  && systemctl --user daemon-reload && systemctl --user enable --now brainboard && systemctl --user restart brainboard \
  && sleep 1 && systemctl --user --no-pager status brainboard | head -5'
