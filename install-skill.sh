#!/bin/bash
# Install the brainboard skills into an agent harness: brainboard (the board) and brainboard-library
# (answering research questions from the paper library).
#
#   ./install-skill.sh claude            → ~/.claude/skills/brainboard        (Claude Code)
#   ./install-skill.sh codex             → ${CODEX_HOME:-~/.codex}/skills/brainboard   (Codex CLI)
#   ./install-skill.sh agents            → ~/.agents/skills/brainboard        (other Agent-Skills harnesses)
#   ./install-skill.sh --dir PATH [--who NAME]   → PATH/brainboard
#   ./install-skill.sh --snippet [--who NAME]    → print an AGENTS.md block (harnesses without skills)
#
# The skill gets its own copy of the CLI (scripts/cli.py + core.py + library.py) and a `bb` wrapper that signs
# changes as the harness name (claude, codex, …). Re-run after pulling to update.
# Connect it to a server once with:  <skill>/scripts/bb config --url http://host:2001
set -euo pipefail
cd "$(dirname "$0")"
REPO=$(pwd)

target="" ; dir="" ; who="" ; snippet=0
while [ $# -gt 0 ]; do
  case "$1" in
    claude|codex|agents) target=$1 ;;
    --dir) dir=$2; shift ;;
    --who) who=$2; shift ;;
    --snippet) snippet=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1 (try --help)" >&2; exit 2 ;;
  esac
  shift
done

case "$target" in
  claude) dir=${dir:-$HOME/.claude/skills}; who=${who:-claude} ;;
  codex)  dir=${dir:-${CODEX_HOME:-$HOME/.codex}/skills}; who=${who:-codex} ;;
  agents) dir=${dir:-$HOME/.agents/skills}; who=${who:-agent} ;;
esac
who=${who:-agent}

render() {  # $1 = how to invoke the CLI, $2 = the skill file (default: the board skill)
  sed -e "s|{{BB}}|$1|g" -e "s|{{WHO}}|$who|g" "${2:-skill/SKILL.md}"
}

if [ "$snippet" = 1 ]; then
  echo "<!-- brainboard: paste into AGENTS.md / CLAUDE.md / .cursorrules -->"
  render "python3 \"$REPO/brainboard/cli.py\" --who $who" | awk 'BEGIN{n=0} /^---$/{n++; next} n>=2'
  echo
  render "python3 \"$REPO/brainboard/cli.py\" --who $who" skill/library/SKILL.md | awk 'BEGIN{n=0} /^---$/{n++; next} n>=2' | sed 's/^# /## /'
  exit 0
fi
[ -n "$dir" ] || { echo "usage: $0 claude|codex|agents | --dir PATH | --snippet   (see --help)" >&2; exit 2; }

dest="$dir/brainboard"
mkdir -p "$dest/scripts"
cp brainboard/cli.py brainboard/core.py brainboard/library.py "$dest/scripts/"
cat > "$dest/scripts/bb" <<EOF
#!/bin/sh
# brainboard CLI for this harness; signs as "$who" unless BRAINBOARD_WHO is set.
export BRAINBOARD_WHO="\${BRAINBOARD_WHO:-$who}"
export BRAINBOARD_HOME="\${BRAINBOARD_HOME:-$REPO/data}"   # used only when no server URL is configured
exec python3 "$dest/scripts/cli.py" "\$@"
EOF
chmod +x "$dest/scripts/bb"
render "\"$dest/scripts/bb\"" > "$dest/SKILL.md"
mkdir -p "$dir/brainboard-library"
render "\"$dest/scripts/bb\"" skill/library/SKILL.md > "$dir/brainboard-library/SKILL.md"
echo "installed brainboard + brainboard-library skills → $dir  (signs as $who)"
if "$dest/scripts/bb" projects >/dev/null 2>&1; then
  "$dest/scripts/bb" projects
else
  echo "next: connect it to your board:  $dest/scripts/bb config --url http://HOST:PORT"
fi
