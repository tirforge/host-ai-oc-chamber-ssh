#!/usr/bin/env bash
# Run smallcode fallback harness against same LM Studio (ai:1234) - full tool support for small contexts
# Dual T4 optimized: points at localhost:1234, default qwen3-coder-30b-a3b
set -e
ENDPOINT=${SMALLCODE_ENDPOINT:-http://localhost:1234/v1}
MODEL=${MODEL:-qwen3-coder-30b-a3b}
CWD=${1:-.}
echo "Starting smallcode fallback -> $ENDPOINT model=$MODEL cwd=$CWD"
if [ ! -d harnesses/smallcode ]; then
  git submodule update --init harnesses/smallcode
fi
cd harnesses/smallcode
npm install --silent || npm install
SMALLCODE_ENDPOINT=$ENDPOINT SMALLCODE_MODEL=$MODEL node bin/smallcode.js --cwd "$CWD"
