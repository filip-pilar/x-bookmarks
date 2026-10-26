#!/bin/sh
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
exec node "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)/dist/server.mjs"
