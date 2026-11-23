#!/bin/sh
# Assemble the Hugging Face Space folder (deploy/hf/space) from the committed code: the app, its static files and
# the Space's own Dockerfile and README. The GitHub README is not touched. Nothing is uploaded by this script.
set -eu
ROOT=$(git rev-parse --show-toplevel); OUT="$ROOT/deploy/hf/space"
rm -rf "$OUT"; mkdir -p "$OUT"
cd "$ROOT" && git archive HEAD app static requirements.txt compliance_rules.yaml LICENSE | tar -x -C "$OUT"
cp "$ROOT/deploy/hf/Dockerfile" "$ROOT/deploy/hf/README.md" "$ROOT/deploy/hf/.dockerignore" "$OUT/"
echo "Space folder ready: $OUT ($(du -sh "$OUT" | cut -f1))"
