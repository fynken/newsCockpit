#!/usr/bin/env bash
# Refresh the cockpit: fetch every source, then rebuild dist/index.html.
#
#   ./refresh.sh              fetch over the network, then build
#   ./refresh.sh --offline    skip the network; use data/agent-inbox.json + cache
#   ./refresh.sh --only us10y spx
#
set -euo pipefail
cd "$(dirname "$0")"
exec python3 -m cockpit refresh "$@"
