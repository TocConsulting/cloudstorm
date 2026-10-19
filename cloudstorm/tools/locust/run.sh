#!/bin/bash
#
# Locust load test execution script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Required environment variables:
#   TARGET_URL    - Target URL to test (e.g., https://api.example.com)
#   USERS         - Number of concurrent users
#   SPAWN_RATE    - Users spawned per second
#   DURATION      - Test duration (e.g., 60s, 5m, 1h)
#
# Optional environment variables:
#   LOCUST_EXTRA_ARGS - Additional locust arguments
#
# Test manually:
#   TARGET_URL=https://example.com USERS=10 SPAWN_RATE=2 DURATION=30s bash run.sh
#
set -e

echo "=== Starting Locust Load Test ==="

# Validate required variables
if [ -z "$TARGET_URL" ]; then
    echo "ERROR: TARGET_URL is required"
    exit 1
fi

if [ -z "$USERS" ]; then
    echo "ERROR: USERS is required"
    exit 1
fi

if [ -z "$SPAWN_RATE" ]; then
    echo "ERROR: SPAWN_RATE is required"
    exit 1
fi

if [ -z "$DURATION" ]; then
    echo "ERROR: DURATION is required"
    exit 1
fi

echo "Configuration:"
echo "  Target URL:  $TARGET_URL"
echo "  Users:       $USERS"
echo "  Spawn Rate:  $SPAWN_RATE"
echo "  Duration:    $DURATION"

# Determine the local bin path (works for both root and non-root)
if [ "$(id -u)" -eq 0 ]; then
    LOCAL_BIN="/root/.local/bin"
else
    LOCAL_BIN="$HOME/.local/bin"
fi

export PATH="$LOCAL_BIN:$PATH"

# Create temporary directory for test files
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"

# If custom script provided, decode and use it
if [ -n "$CUSTOM_SCRIPT_B64" ]; then
    echo "$CUSTOM_SCRIPT_B64" | base64 -d > locustfile.py
    echo "Using custom locustfile"
    CUSTOM_SCRIPT=1
fi

# Parse ENDPOINTS (comma-separated, default: "/")
IFS=',' read -ra ENDPOINT_LIST <<< "${ENDPOINTS:-/}"

# Generate locustfile dynamically -- endpoints read from env var at runtime (no shell interpolation)
if [ -z "$CUSTOM_SCRIPT" ]; then
{
    echo 'import os, json, threading, time'
    echo 'from locust import HttpUser, task, between, events'
    echo ''
    echo '_ENDPOINTS = [e.strip() for e in os.environ.get("ENDPOINTS", "/").split(",")]'
    echo '_req_count = 0'
    echo '_err_count = 0'
    echo '_lock = threading.Lock()'
    echo ''
    echo '@events.request.add_listener'
    echo 'def on_request(request_type, name, response_time, response_length, exception, **kwargs):'
    echo '    global _req_count, _err_count'
    echo '    with _lock:'
    echo '        _req_count += 1'
    echo '        if exception:'
    echo '            _err_count += 1'
    echo ''
    echo 'def _progress_writer():'
    echo '    while True:'
    echo '        time.sleep(2)'
    echo '        with _lock:'
    echo '            r, e = _req_count, _err_count'
    echo '        try:'
    echo '            with open("/tmp/cloudstorm-progress.json", "w") as f:'
    echo '                json.dump({"requests": r, "errors": e}, f)'
    echo '        except: pass'
    echo ''
    echo 'threading.Thread(target=_progress_writer, daemon=True).start()'
    echo ''
    echo 'class CloudstormUser(HttpUser):'
    echo '    wait_time = between(0.5, 2)'
    echo ''
    echo '    @task'
    echo '    def hit_endpoints(self):'
    echo '        for ep in _ENDPOINTS:'
    echo '            self.client.get(ep, name=ep)'
} > locustfile.py

echo "Created locustfile.py with ${#ENDPOINT_LIST[@]} endpoint(s): ${ENDPOINTS:-/}"
fi

# Build locust command
LOCUST_CMD="locust"
if [ -x "/opt/cloudstorm/locust-venv/bin/locust" ]; then
    LOCUST_CMD="/opt/cloudstorm/locust-venv/bin/locust"
elif [ -x "$HOME/.local/bin/locust" ]; then
    LOCUST_CMD="$HOME/.local/bin/locust"
fi

# Run locust in headless mode with JSON output
# Progress is written by the locustfile itself via event listener
echo "Running locust..."
set +e
"$LOCUST_CMD" \
    --locustfile locustfile.py \
    --headless \
    --host "$TARGET_URL" \
    --users "$USERS" \
    --spawn-rate "$SPAWN_RATE" \
    --run-time "$DURATION" \
    --json \
    ${LOCUST_EXTRA_ARGS:+$LOCUST_EXTRA_ARGS} \
    > locust_raw_output.txt 2>&1

EXIT_CODE=$?
set -e

# Extract JSON stats from the output file and echo ONLY the JSON with markers
python3 << 'PYEXTRACT'
import json, sys

with open("locust_raw_output.txt") as f:
    output = f.read()

end = output.rfind("]")
if end < 0:
    print("ERROR: No JSON stats found in locust output", file=sys.stderr)
    sys.exit(0)

bracket_count = 0
start = end
while start >= 0:
    if output[start] == "]":
        bracket_count += 1
    elif output[start] == "[":
        bracket_count -= 1
    if bracket_count == 0:
        break
    start -= 1

if bracket_count != 0:
    print("ERROR: No matching JSON array in locust output", file=sys.stderr)
    sys.exit(0)

try:
    data = json.loads(output[start:end+1])
    print("=== LOCUST_JSON_START ===")
    print(json.dumps(data))
    print("=== LOCUST_JSON_END ===")
except json.JSONDecodeError as e:
    print(f"ERROR: Failed to parse locust JSON: {e}", file=sys.stderr)
PYEXTRACT

echo "=== Locust Load Test Complete (exit code: $EXIT_CODE) ==="

exit $EXIT_CODE
