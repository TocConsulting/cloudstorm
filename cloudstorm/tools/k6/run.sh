#!/bin/bash
#
# k6 load test execution script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Required environment variables:
#   TARGET_URL    - Target URL to test (e.g., https://api.example.com)
#   USERS         - Number of virtual users (VUs)
#   DURATION      - Test duration (e.g., 60s, 5m, 1h)
#
# Optional environment variables:
#   SPAWN_RATE    - Ramp-up rate (not directly used in k6, but for consistency)
#   K6_EXTRA_ARGS - Additional k6 arguments
#
# Test manually:
#   TARGET_URL=https://example.com USERS=10 DURATION=30s bash run.sh
#
set -e

echo "=== Starting k6 Load Test ==="

# Validate required variables
if [ -z "$TARGET_URL" ]; then
    echo "ERROR: TARGET_URL is required"
    exit 1
fi

if [ -z "$USERS" ]; then
    echo "ERROR: USERS is required"
    exit 1
fi

if [ -z "$DURATION" ]; then
    echo "ERROR: DURATION is required"
    exit 1
fi

echo "Configuration:"
echo "  Target URL:  $TARGET_URL"
echo "  VUs:         $USERS"
echo "  Duration:    $DURATION"

# Create temporary directory for test files
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"

# If custom script provided, decode and use it
if [ -n "$CUSTOM_SCRIPT_B64" ]; then
    echo "$CUSTOM_SCRIPT_B64" | base64 -d > script.js
    echo "Using custom k6 script"
    CUSTOM_SCRIPT=1
fi

# Parse ENDPOINTS (comma-separated, default: "/")
IFS=',' read -ra ENDPOINT_LIST <<< "${ENDPOINTS:-/}"

# Generate k6 script -- endpoints read from env var at runtime (no shell interpolation)
if [ -z "$CUSTOM_SCRIPT" ]; then
{
    echo "import http from 'k6/http';"
    echo "import { check, sleep } from 'k6';"
    echo ""
    echo "export const options = {"
    echo "    vus: parseInt(__ENV.USERS || '10'),"
    echo "    duration: __ENV.DURATION || '30s',"
    echo "};"
    echo ""
    echo "const BASE_URL = __ENV.TARGET_URL;"
    echo "const ENDPOINTS = (__ENV.ENDPOINTS || '/').split(',').map(e => e.trim());"
    echo ""
    echo "export default function () {"
    echo "    ENDPOINTS.forEach(function(ep) {"
    echo "        const res = http.get(BASE_URL + ep);"
    echo "        check(res, {"
    echo "            [ep + ' status 2xx']: (r) => r.status >= 200 && r.status < 300,"
    echo "        });"
    echo "    });"
    echo "    sleep(Math.random() * 1.5 + 0.5);"
    echo "}"
    echo ""
    echo "export function handleSummary(data) {"
    echo "    return {"
    echo "        'summary.json': JSON.stringify(data),"
    echo "        'stdout': '',"
    echo "    };"
    echo "}"
} > script.js

echo "Created k6 script with ${#ENDPOINT_LIST[@]} endpoint(s): ${ENDPOINTS:-/}"
fi

# Start progress writer (background) -- counts k6 CSV output lines
(while true; do sleep 2; if [ -f k6_stats.csv ]; then REQS=$(grep -c "http_reqs" k6_stats.csv 2>/dev/null || echo "0"); ERRS=$(grep -c "http_req_failed" k6_stats.csv 2>/dev/null || echo "0"); echo "{\"requests\": $REQS, \"errors\": $ERRS}" > /tmp/cloudstorm-progress.json; fi; done) &
_PROGRESS_PID=$!

echo "Running k6..."
set +e
# k6 only reports p(90)/p(95) by default; request p(99) explicitly
k6 run --quiet --no-color --log-output=none \
    --summary-trend-stats="avg,min,med,max,p(90),p(95),p(99)" \
    --out csv=k6_stats.csv \
    ${K6_EXTRA_ARGS:+$K6_EXTRA_ARGS} \
    script.js \
    > /dev/null 2>&1

EXIT_CODE=$?
set -e
kill $_PROGRESS_PID 2>/dev/null || true

# Output minimal JSON summary (only fields we parse -- stays well under SSM 24KB limit)
if [ -f summary.json ]; then
    python3 << 'PYEXTRACT'
import json, sys
try:
    with open("summary.json") as f:
        data = json.load(f)
    m = data.get("metrics", {})
    compact = {"metrics": {
        "http_reqs": m.get("http_reqs", {}),
        "http_req_duration": m.get("http_req_duration", {}),
        "http_req_failed": m.get("http_req_failed", {}),
    }}
    # Extract per-endpoint stats from CSV (k6 summary doesn't have per-URL metrics)
    endpoints = {}
    try:
        import csv
        from urllib.parse import urlparse
        with open("k6_stats.csv") as csvf:
            reader = csv.DictReader(csvf)
            for row in reader:
                if row.get("metric_name") == "http_req_duration":
                    url = row.get("url", "")
                    if url:
                        path = urlparse(url).path or "/"
                        val = float(row.get("metric_value", 0))
                        status = row.get("status", "0")
                        ok = 200 <= int(status) < 400 if status.isdigit() else False
                        if path not in endpoints:
                            endpoints[path] = {"requests": 0, "successful": 0, "failed": 0, "total": 0.0, "min": float("inf"), "max": 0.0}
                        ep = endpoints[path]
                        ep["requests"] += 1
                        if ok:
                            ep["successful"] += 1
                        else:
                            ep["failed"] += 1
                        ep["total"] += val
                        if val < ep["min"]:
                            ep["min"] = val
                        if val > ep["max"]:
                            ep["max"] = val
        for path, ep in endpoints.items():
            endpoints[path] = {
                "requests": ep["requests"],
                "successful": ep["successful"],
                "failed": ep["failed"],
                "avg_ms": ep["total"] / ep["requests"] if ep["requests"] > 0 else 0,
                "min_ms": ep["min"] if ep["min"] != float("inf") else 0,
                "max_ms": ep["max"],
            }
    except Exception:
        pass
    compact["endpoints"] = endpoints
    print("=== K6_JSON_SUMMARY_START ===")
    print(json.dumps(compact))
    print("=== K6_JSON_SUMMARY_END ===")
except Exception as e:
    print(f"ERROR: Failed to parse summary.json: {e}", file=sys.stderr)
PYEXTRACT
else
    echo "ERROR: summary.json not found (process may have been killed)" >&2
fi

echo "=== k6 Load Test Complete (exit code: $EXIT_CODE) ==="

exit $EXIT_CODE
