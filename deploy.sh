#!/usr/bin/env bash
# Deploy agent-recall to a remote machine over SSH.
# Run this from a machine that can SSH to the target.
#
#   ./deploy.sh user@host
#
# It copies cs.py + cs.sh + install.sh to the target and runs the installer there.
set -euo pipefail

if [ "$#" -ne 1 ]; then
  echo "Usage: $0 user@host" >&2
  exit 2
fi

BOX="$1"
HERE="$(cd "$(dirname "$0")" >/dev/null 2>&1 && pwd)"
TMP="/tmp/agent-recall-install"

echo "→ target: $BOX"
echo "→ staging files in $BOX:$TMP"
ssh "$BOX" "mkdir -p '$TMP'"
scp "$HERE/cs.py" "$HERE/cs.sh" "$HERE/install.sh" "$BOX:$TMP/"

echo "→ running installer on $BOX"
ssh "$BOX" "bash '$TMP/install.sh'"

echo "✓ done. SSH into $BOX, open a fresh shell, and run:  cs"
