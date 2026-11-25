#!/bin/sh
# Container entrypoint: customer portal on :8701 (background) and analyst console on :8700 (foreground).
# Both share uc4/onboarding.db, which the console builds on first start.
set -e
cd /app/src/uc4
python tools/serve_portal.py --host 0.0.0.0 --port 8701 --no-browser &
exec python tools/serve.py --host 0.0.0.0 --port 8700 --no-browser
