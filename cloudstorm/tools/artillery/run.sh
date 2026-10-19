#!/bin/bash
#
# Artillery load test execution script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Required environment variables:
#   TARGET_URL    - Target URL to test (e.g., https://api.example.com)
#   USERS         - Number of concurrent virtual users
#   DURATION      - Test duration (e.g., 60s, 5m, 1h) - converted to seconds
#
# Optional environment variables:
#   SPAWN_RATE        - Ramp-up duration in seconds (default: 10)
#   ARTILLERY_EXTRA   - Additional artillery arguments
#
# Test manually:
#   TARGET_URL=https://example.com USERS=10 DURATION=30s bash run.sh
#
set -e

echo "=== Starting Artillery Load Test ==="

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

# Convert duration to seconds
DURATION_SECONDS=$DURATION
if [[ "$DURATION" == *"m" ]]; then
    DURATION_SECONDS=$((${DURATION%m} * 60))
elif [[ "$DURATION" == *"h" ]]; then
    DURATION_SECONDS=$((${DURATION%h} * 3600))
elif [[ "$DURATION" == *"s" ]]; then
    DURATION_SECONDS=${DURATION%s}
fi

# Calculate ramp-up time: users / spawn_rate = seconds to reach full load
if [ -n "$SPAWN_RATE" ] && [ "$SPAWN_RATE" -gt 0 ]; then
    RAMP_UP=$(( USERS / SPAWN_RATE ))
    if [ "$RAMP_UP" -lt 1 ]; then
        RAMP_UP=1
    fi
else
    RAMP_UP=10
fi

export DURATION_SECONDS RAMP_UP

echo "Configuration:"
echo "  Target URL:       $TARGET_URL"
echo "  Concurrent Users: $USERS"
echo "  Duration:         $DURATION_SECONDS seconds"
echo "  Ramp-up:          $RAMP_UP seconds"

# Create temporary directory for test files
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"

# Config file defaults to config.json
CONFIG_FILE="config.json"

# If custom script provided, decode and use it
if [ -n "$CUSTOM_SCRIPT_B64" ]; then
    if [ "$CUSTOM_SCRIPT_EXT" = "yaml" ] || [ "$CUSTOM_SCRIPT_EXT" = "yml" ]; then
        CONFIG_FILE="config.yaml"
    elif [ -z "$CUSTOM_SCRIPT_EXT" ]; then
        FIRST_CHAR=$(echo "$CUSTOM_SCRIPT_B64" | base64 -d | grep -v '^[[:space:]]*$' | head -n 1 | sed 's/^[[:space:]]*//' | cut -c1)
        if [ "$FIRST_CHAR" != "{" ] && [ "$FIRST_CHAR" != "[" ]; then
            CONFIG_FILE="config.yaml"
        fi
    fi
    echo "$CUSTOM_SCRIPT_B64" | base64 -d > "$CONFIG_FILE"
    echo "Using custom Artillery config ($CONFIG_FILE)"
    CUSTOM_SCRIPT=1
fi

if [ -z "$CUSTOM_SCRIPT" ]; then
# Parse ENDPOINTS (comma-separated, default: "/")
ENDPOINTS="${ENDPOINTS:-/}"
export ENDPOINTS

# Generate Artillery config via Node.js to avoid shell/YAML injection
node -e "
const fs = require('fs');
const endpoints = (process.env.ENDPOINTS || '/').split(',').map(e => e.trim());
const users = parseInt(process.env.USERS);
const rampUp = parseInt(process.env.RAMP_UP || '10');
const durationSec = parseInt(process.env.DURATION_SECONDS);
// One Artillery process is single threaded and a single source IP can only open
// so many new connections per second, so both rates are capped per instance.
const arrivalCap = parseInt(process.env.ARTILLERY_MAX_ARRIVAL || '400');
const rpsCap = parseInt(process.env.ARTILLERY_MAX_RPS || '300');
// New users arrive no faster than arrivalCap per second
const rampSec = Math.max(rampUp, Math.ceil(users / arrivalCap));
const arrivalRate = Math.max(1, Math.ceil(users / rampSec));
// Each user keeps its connection open and repeats the requests with a think time
// long enough that all users together send about rpsCap requests per second
const think = Math.max(1, Math.ceil(users * endpoints.length / rpsCap));
const loops = Math.max(1, Math.floor(durationSec / think));
const cycle = [];
endpoints.forEach(ep => {
  cycle.push({ get: { url: ep } });
});
cycle.push({ think: think });
const flow = [{ loop: cycle, count: loops }];
console.log('Artillery plan: users=' + users + ' ramp=' + rampSec + 's arrivalRate=' + arrivalRate + ' think=' + think + 's loops=' + loops);
const config = {
  config: {
    target: process.env.TARGET_URL,
    phases: [
      { duration: rampSec, arrivalRate: arrivalRate, maxVusers: users, name: 'Ramp up' }
    ]
  },
  scenarios: [
    { name: 'Default scenario', flow: flow }
  ]
};
fs.writeFileSync('config.json', JSON.stringify(config, null, 2));
"

echo "Created Artillery config with endpoints: $ENDPOINTS"
fi

# Run Artillery with JSON report
# ALL output goes to file to avoid SSM's 24KB stdout limit
# Start progress writer (background) -- counts lines in Artillery output as proxy
(while true; do sleep 2; if [ -f artillery_raw_output.txt ]; then REQS=$(grep -c "statusCode" artillery_raw_output.txt 2>/dev/null || echo "0"); ERRS=$(grep -c "\"errors\"" artillery_raw_output.txt 2>/dev/null || echo "0"); echo "{\"requests\": $REQS, \"errors\": $ERRS}" > /tmp/cloudstorm-progress.json; fi; done) &
_PROGRESS_PID=$!

echo "Running Artillery..."
set +e
artillery run \
    --output report.json \
    ${ARTILLERY_EXTRA:+$ARTILLERY_EXTRA} \
    "$CONFIG_FILE" \
    > artillery_raw_output.txt 2>&1

EXIT_CODE=$?
set -e
kill $_PROGRESS_PID 2>/dev/null || true

# Output compact JSON report (extract only fields we parse to stay under SSM 24KB limit)
if [ -f report.json ]; then
    node -e "
const d = JSON.parse(require('fs').readFileSync('report.json','utf8'));
const a = d.aggregate || {};
// Extract per-endpoint stats from summaries + counters
const endpoints = {};
const summaries = a.summaries || {};
const counters = a.counters || {};
for (const [key, val] of Object.entries(summaries)) {
    const match = key.match(/metrics-by-endpoint\.response_time\.(\/.*)$/);
    if (match) {
        const path = match[1];
        const reqs = val.count || 0;
        // Count successful/failed from counters: plugins.metrics-by-endpoint.{path}.codes.{code}
        let ok = 0, fail = 0;
        const prefix = 'plugins.metrics-by-endpoint.' + path + '.codes.';
        for (const [ck, cv] of Object.entries(counters)) {
            if (ck.startsWith(prefix)) {
                const code = parseInt(ck.substring(prefix.length));
                if (code >= 200 && code < 400) ok += cv;
                else if (code >= 400) fail += cv;
            }
        }
        if (ok === 0 && fail === 0) ok = reqs;
        endpoints[path] = {
            requests: reqs,
            successful: ok,
            failed: fail,
            avg_ms: val.mean || 0,
            min_ms: val.min || 0,
            max_ms: val.max || 0,
        };
    }
}
const compact = {aggregate: {
  counters: a.counters || {},
  summaries: a.summaries || {},
  rates: a.rates || {},
}, endpoints: endpoints};
console.log('=== ARTILLERY_JSON_REPORT_START ===');
console.log(JSON.stringify(compact));
console.log('=== ARTILLERY_JSON_REPORT_END ===');
" 2>/dev/null || echo "ERROR: Failed to parse report.json" >&2
else
    echo "ERROR: report.json not found (process may have been killed)" >&2
fi

echo "=== Artillery Load Test Complete (exit code: $EXIT_CODE) ==="

exit $EXIT_CODE
