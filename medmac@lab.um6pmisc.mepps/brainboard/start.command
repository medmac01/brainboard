#!/bin/bash
# macOS: double-click to run a local board.
cd "$(dirname "$0")"
if curl -s -o /dev/null http://127.0.0.1:8765/api/version; then open http://127.0.0.1:8765/; else python3 brainboard/server.py; fi
