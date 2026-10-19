#!/bin/bash
#
# JMeter load test execution script for cloudstorm
# Runs on Amazon Linux 2023 EC2 instances
#
# Required environment variables:
#   TARGET_URL    - Target URL to test (e.g., https://api.example.com)
#   USERS         - Number of threads (virtual users)
#   DURATION      - Test duration (e.g., 60s, 5m, 1h) - converted to seconds
#
# Optional environment variables:
#   SPAWN_RATE    - Users spawned per second (default: calculated for 10s ramp-up)
#   JMETER_EXTRA  - Additional JMeter arguments
#
# Test manually:
#   TARGET_URL=https://example.com USERS=10 DURATION=30s bash run.sh
#
set -e

echo "=== Starting JMeter Load Test ==="

JMETER_HOME="/opt/jmeter"

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
    # Minimum 1 second ramp-up
    if [ "$RAMP_UP" -lt 1 ]; then
        RAMP_UP=1
    fi
else
    RAMP_UP=10
fi

# Parse URL components using bash parameter expansion (portable, no grep -oP)
PROTOCOL="${TARGET_URL%%://*}"
URL_REST="${TARGET_URL#*://}"
HOST="${URL_REST%%/*}"
HOST="${HOST%%:*}"
# Extract port if present
if echo "$TARGET_URL" | grep -q '://[^/]*:[0-9]'; then
    PORT=$(echo "$URL_REST" | sed -n 's/[^:]*:\([0-9]*\).*/\1/p')
else
    PORT=""
fi

if [ -z "$PORT" ]; then
    if [ "$PROTOCOL" = "https" ]; then
        PORT="443"
    else
        PORT="80"
    fi
fi

echo "Configuration:"
echo "  Target URL:       $TARGET_URL"
echo "  Protocol:         $PROTOCOL"
echo "  Host:             $HOST"
echo "  Port:             $PORT"
echo "  Threads:          $USERS"
echo "  Duration:         $DURATION_SECONDS seconds"
echo "  Ramp-up:          $RAMP_UP seconds"

# Create temporary directory for test files
WORK_DIR=$(mktemp -d)
trap 'rm -rf "$WORK_DIR"' EXIT
cd "$WORK_DIR"

# If custom script provided, decode and use it
if [ -n "$CUSTOM_SCRIPT_B64" ]; then
    echo "$CUSTOM_SCRIPT_B64" | base64 -d > testplan.jmx
    echo "Using custom JMeter test plan"
    CUSTOM_SCRIPT=1
fi

if [ -z "$CUSTOM_SCRIPT" ]; then
# Parse ENDPOINTS (comma-separated, default: "/")
IFS=',' read -ra ENDPOINT_LIST <<< "${ENDPOINTS:-/}"

# Generate JMeter test plan (JMX format) dynamically based on endpoints
# Values are passed via -J properties to avoid shell injection
{
cat << 'JMX_HEADER'
<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="cloudstorm Load Test">
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments">
        <collectionProp name="Arguments.arguments"/>
      </elementProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="Load Test Group">
        <stringProp name="ThreadGroup.on_sample_error">continue</stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController">
          <intProp name="LoopController.loops">-1</intProp>
          <boolProp name="LoopController.continue_forever">false</boolProp>
        </elementProp>
        <stringProp name="ThreadGroup.num_threads">${__P(cs_users)}</stringProp>
        <stringProp name="ThreadGroup.ramp_time">${__P(cs_rampup)}</stringProp>
        <boolProp name="ThreadGroup.scheduler">true</boolProp>
        <stringProp name="ThreadGroup.duration">${__P(cs_duration)}</stringProp>
        <stringProp name="ThreadGroup.delay">0</stringProp>
        <boolProp name="ThreadGroup.same_user_on_next_iteration">true</boolProp>
      </ThreadGroup>
      <hashTree>
JMX_HEADER

    # Generate one HTTPSamplerProxy per endpoint
    EP_NUM=0
    for ep in "${ENDPOINT_LIST[@]}"; do
        ep=$(echo "$ep" | xargs)  # trim whitespace
        # XML-escape the endpoint
        ep_escaped=$(echo "$ep" | sed 's/&/\&amp;/g; s/</\&lt;/g; s/>/\&gt;/g; s/"/\&quot;/g')
        EP_NUM=$((EP_NUM + 1))
        cat << SAMPLER
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="GET ${ep_escaped}">
          <stringProp name="HTTPSampler.domain">\${__P(cs_host)}</stringProp>
          <stringProp name="HTTPSampler.port">\${__P(cs_port)}</stringProp>
          <stringProp name="HTTPSampler.protocol">\${__P(cs_protocol)}</stringProp>
          <stringProp name="HTTPSampler.path">${ep_escaped}</stringProp>
          <stringProp name="HTTPSampler.method">GET</stringProp>
          <boolProp name="HTTPSampler.follow_redirects">true</boolProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree/>
SAMPLER
    done

cat << 'JMX_FOOTER'
        <ConstantTimer guiclass="ConstantTimerGui" testclass="ConstantTimer" testname="Think Time">
          <stringProp name="ConstantTimer.delay">500</stringProp>
        </ConstantTimer>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
JMX_FOOTER
} > testplan.jmx

echo "Created JMeter test plan with ${#ENDPOINT_LIST[@]} endpoint(s): ${ENDPOINTS:-/}"
fi

# Run JMeter in non-GUI mode
# ALL output goes to file to avoid SSM's 24KB stdout limit
# Start progress writer (background) -- counts JTL result lines and errors
(while true; do sleep 2; if [ -f results.jtl ]; then REQS=$(($(wc -l < results.jtl) - 1)); ERRS=$(awk -F',' '$8=="false"' results.jtl 2>/dev/null | wc -l); [ "$REQS" -gt 0 ] 2>/dev/null && echo "{\"requests\": $REQS, \"errors\": $ERRS}" > /tmp/cloudstorm-progress.json; fi; done) &
_PROGRESS_PID=$!

echo "Running JMeter..."
set +e
"$JMETER_HOME/bin/jmeter" \
    -n \
    -t testplan.jmx \
    -l results.jtl \
    -j jmeter.log \
    -Jcs_users="$USERS" \
    -Jcs_rampup="$RAMP_UP" \
    -Jcs_duration="$DURATION_SECONDS" \
    -Jcs_host="$HOST" \
    -Jcs_port="$PORT" \
    -Jcs_protocol="$PROTOCOL" \
    ${JMETER_EXTRA:+$JMETER_EXTRA} \
    > jmeter_raw_output.txt 2>&1

EXIT_CODE=$?
set -e
kill $_PROGRESS_PID 2>/dev/null || true

# Parse and output results
if [ -f results.jtl ]; then
    echo "=== JMETER_RESULTS_START ==="

    # JTL is CSV format, output statistics
    TOTAL=$(tail -n +2 results.jtl | wc -l)
    SUCCESS=$(tail -n +2 results.jtl | awk -F',' '$8=="true"' | wc -l)
    FAILED=$((TOTAL - SUCCESS))

    # Calculate RPS (use awk instead of bc -- bc is not installed on AL2023)
    RPS="0"
    if [ "$DURATION_SECONDS" -gt 0 ]; then
        RPS=$(awk "BEGIN {printf \"%.2f\", $TOTAL / $DURATION_SECONDS}")
    fi

    # Output a single valid JSON object with all metrics including per-endpoint stats
    tail -n +2 results.jtl | gawk -F',' -v success="$SUCCESS" -v failed="$FAILED" -v rps="$RPS" '
        BEGIN { min=999999; max=0; sum=0; count=0 }
        {
            elapsed=$2;
            label=$3;
            ok=$8;
            if (elapsed < min) min=elapsed;
            if (elapsed > max) max=elapsed;
            sum+=elapsed;
            count++;
            times[count]=elapsed;
            # Per-endpoint tracking
            if (!(label in ep_reqs)) { ep_reqs[label]=0; ep_ok[label]=0; ep_fail[label]=0; ep_sum[label]=0; ep_mn[label]=999999; ep_mx[label]=0; }
            ep_reqs[label]++;
            ep_sum[label]+=elapsed;
            if (ok=="true") ep_ok[label]++; else ep_fail[label]++;
            if (elapsed < ep_mn[label]) ep_mn[label]=elapsed;
            if (elapsed > ep_mx[label]) ep_mx[label]=elapsed;
        }
        END {
            if (count > 0) {
                avg=sum/count;
                asort(times);
                p50=times[int(count*0.5)];
                p90=times[int(count*0.9)];
                p95=times[int(count*0.95)];
                p99=times[int(count*0.99)];
                print "{"
                print "  \"total_requests\": " count ","
                print "  \"min_ms\": " min ","
                print "  \"max_ms\": " max ","
                print "  \"avg_ms\": " avg ","
                print "  \"p50_ms\": " p50 ","
                print "  \"p90_ms\": " p90 ","
                print "  \"p95_ms\": " p95 ","
                print "  \"p99_ms\": " p99 ","
                print "  \"success_count\": " success ","
                print "  \"failed_count\": " failed ","
                print "  \"requests_per_second\": " rps ","
                printf "  \"endpoints\": {"
                sep=""
                for (label in ep_reqs) {
                    ep_avg=ep_sum[label]/ep_reqs[label];
                    printf "%s\"%s\":{\"requests\":%d,\"successful\":%d,\"failed\":%d,\"avg_ms\":%.1f,\"min_ms\":%d,\"max_ms\":%d}", sep, label, ep_reqs[label], ep_ok[label], ep_fail[label], ep_avg, ep_mn[label], ep_mx[label];
                    sep=","
                }
                print "}"
                print "}"
            }
        }
    '

    echo "=== JMETER_RESULTS_END ==="
else
    echo "ERROR: results.jtl not found" >&2
fi

echo "=== JMeter Load Test Complete (exit code: $EXIT_CODE) ==="

exit $EXIT_CODE
