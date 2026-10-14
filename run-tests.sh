#!/bin/sh
# Runs the whole test suite with one command: the Python backend (pytest), the
# alignment code (Node) and the app in headless Chrome. Needs the backend venv
# with backend/requirements-dev.txt installed, Node 22+ and Google Chrome.
cd "$(dirname "$0")" || exit 1
status=0
echo "== Python (pytest) =="
backend/venv/bin/python -m pytest || status=1
echo "== Node (alignment) =="
node --test tests/node/*.test.js || status=1
echo "== Browser (headless Chrome) =="
node tests/browser/run.mjs || status=1
exit $status
