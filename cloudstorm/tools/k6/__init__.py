"""
k6 load testing tool plugin for cloudstorm.

k6 is a modern Go-based load testing tool.
https://k6.io
"""
import base64
import json
import re
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...aws.ssm import run_command
from ...constants import TIMEOUT_TEST_BUFFER
from ...utils.display import print_error, print_info, print_success
from ..base import (
    aggregate_results,
    check_oom_kill,
    create_standard_result,
    get_tool_script,
    parse_duration,
)

TOOL_DIR = Path(__file__).parent

# Tool metadata
TOOL_INFO = {
    "name": "k6",
    "description": "Modern Go-based load testing tool",
    "website": "https://k6.io",
    "language": "Go",
}


def install(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Install k6 on instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        profile: AWS profile.

    Returns:
        Installation result with success status.
    """
    print_info(f"Installing k6 on {len(instance_ids)} instances in {region}")

    script = get_tool_script(TOOL_DIR,"install.sh")

    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=script,
        timeout=300,
        comment="cloudstorm: Install k6",
        profile=profile,
    )

    if result.get("success"):
        print_success(f"k6 installed on {result.get('success_count', 0)} instances in {region}")
    else:
        print_error(f"Failed to install k6 in {region}: {result.get('error', 'Unknown error')}")

    return result


def run_test(
    region: str,
    instance_ids: list[str],
    target_url: str,
    users: int,
    spawn_rate: int,
    duration: str,
    endpoints: str = "/",
    profile: str | None = None,
    progress_callback=None,
    script_content=None,
) -> dict[str, Any]:
    """
    Run k6 load test on instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        target_url: Target URL to test.
        users: Total number of virtual users (VUs).
        spawn_rate: Users spawned per second (used for consistency).
        duration: Test duration (e.g., "60s", "5m").
        endpoints: Comma-separated endpoints to test.
        profile: AWS profile.

    Returns:
        Test result with parsed metrics.
    """
    print_info(f"Running k6 test in {region}: {target_url}")

    # Calculate per-instance values
    users_per_instance = max(1, users // len(instance_ids))

    duration_seconds = parse_duration(duration)
    user_buffer = max(0, users_per_instance // 1000) * 30
    timeout = duration_seconds + TIMEOUT_TEST_BUFFER + user_buffer

    # Read script and prepend environment variables
    script = get_tool_script(TOOL_DIR,"run.sh")
    env_vars = f"""
export TARGET_URL={shlex.quote(target_url)}
export USERS={users_per_instance}
export SPAWN_RATE={spawn_rate}
export DURATION={shlex.quote(duration)}
export ENDPOINTS={shlex.quote(endpoints)}
"""
    if script_content:
        # Append handleSummary if not defined by user
        if 'handleSummary' not in script_content:
            script_content += """

export function handleSummary(data) {
    return {
        'summary.json': JSON.stringify(data),
        'stdout': '',
    };
}
"""
        encoded = base64.b64encode(script_content.encode()).decode()
        env_vars += f'\nexport CUSTOM_SCRIPT_B64="{encoded}"\n'

    full_script = env_vars + "\n" + script

    safe_url = urlparse(target_url)._replace(netloc=urlparse(target_url).hostname or "").geturl()
    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=full_script,
        timeout=timeout,
        comment=f"cloudstorm: k6 test {safe_url}",
        profile=profile,
        progress_callback=progress_callback,
    )

    # Parse results from each instance (try all instances, even Failed ones - tool may
    # exit non-zero but still produce valid output)
    if "results" in result:
        parsed_results = []
        for instance_id, instance_result in result["results"].items():
            status = instance_result.get("status", "")
            output = instance_result.get("output", "")
            error_output = instance_result.get("error", "")

            if output:
                parsed = parse_output(output)
                if parsed:
                    parsed["instance_id"] = instance_id
                    parsed_results.append(parsed)
                else:
                    if not check_oom_kill(output, error_output, instance_id):
                        short_id = instance_id[-8:]
                        print_error(f"  {short_id}: status={status}, no parseable output")
                        if error_output:
                            print_error(f"  {short_id} stderr: {error_output[:200]}")
                        if output:
                            print_error(f"  {short_id} stdout: {output[:200]}")
            else:
                if not check_oom_kill("", error_output, instance_id):
                    short_id = instance_id[-8:]
                    print_error(f"  {short_id}: status={status}, no output")
                    if error_output:
                        print_error(f"  {short_id} stderr: {error_output[:200]}")

        if parsed_results:
            result["parsed"] = aggregate_results(parsed_results)
            result["per_instance"] = parsed_results
            result["success"] = True
            print_success(f"k6 test completed in {region}: {result['parsed']['summary']['total_requests']} requests")
        else:
            print_error(f"Failed to parse k6 results in {region} (0/{len(result['results'])} instances produced data)")
    else:
        print_error(f"k6 test failed in {region}: {result.get('error', 'No results returned')}")

    return result


def parse_output(output: str) -> dict[str, Any] | None:
    """
    Parse k6 output into standardized format.

    Args:
        output: Raw output from k6 (expects JSON summary).

    Returns:
        Standardized results dictionary or None if parsing fails.
    """
    try:
        # Find JSON summary in output
        json_match = re.search(
            r'=== K6_JSON_SUMMARY_START ===\s*([\s\S]*?)\s*=== K6_JSON_SUMMARY_END ===',
            output
        )

        if json_match:
            data = json.loads(json_match.group(1))
            return _parse_json_summary(data)

        # Fallback: try to parse text output
        return _parse_text_output(output)

    except json.JSONDecodeError as e:
        print_error(f"Failed to parse k6 JSON: {e}")
        return None
    except Exception as e:
        print_error(f"Failed to parse k6 output: {e}")
        return None


def _parse_json_summary(data: dict) -> dict[str, Any] | None:
    """Parse k6 JSON summary format."""
    try:
        metrics = data.get("metrics", {})

        # HTTP request metrics
        http_reqs = metrics.get("http_reqs", {})
        http_req_duration = metrics.get("http_req_duration", {})
        http_req_failed = metrics.get("http_req_failed", {})

        # k6 nests metrics under "values" key
        reqs_values = http_reqs.get("values", http_reqs)
        total_requests = int(reqs_values.get("count", 0))
        if total_requests == 0:
            return None

        # Calculate failures
        failed_values = http_req_failed.get("values", http_req_failed) if http_req_failed else {}
        failure_rate = failed_values.get("rate", 0)
        failed_requests = int(total_requests * failure_rate)

        # Response times
        duration_values = http_req_duration.get("values", http_req_duration) if http_req_duration else {}

        return create_standard_result(
            total_requests=total_requests,
            successful_requests=total_requests - failed_requests,
            failed_requests=failed_requests,
            min_response_ms=duration_values.get("min", 0),
            avg_response_ms=duration_values.get("avg", 0),
            max_response_ms=duration_values.get("max", 0),
            median_response_ms=duration_values.get("med", 0),
            p90_response_ms=duration_values.get("p(90)", 0),
            p95_response_ms=duration_values.get("p(95)", 0),
            p99_response_ms=duration_values.get("p(99)", 0),
            requests_per_second=reqs_values.get("rate", 0),
            endpoints=data.get("endpoints", {}),
        )

    except Exception as e:
        print_error(f"Failed to parse k6 JSON summary: {e}")
        return None


def _parse_text_output(output: str) -> dict[str, Any] | None:
    """Parse k6 text output as fallback."""
    try:
        # k6 outputs statistics in a specific format
        # http_reqs......................: 1000   100/s
        # http_req_duration..............: avg=50ms min=10ms max=500ms p(90)=100ms p(95)=200ms

        reqs_match = re.search(r'http_reqs[.\s:]+(\d+)', output)
        rps_match = re.search(r'http_reqs[^0-9]+\d+\s+(\d+\.?\d*)/s', output)
        duration_match = re.search(
            r'http_req_duration[^a-z]+avg=(\d+\.?\d*)(\w+).*min=(\d+\.?\d*).*max=(\d+\.?\d*)',
            output
        )

        if reqs_match:
            total = int(reqs_match.group(1))
            rps = float(rps_match.group(1)) if rps_match else 0

            avg_ms = 0
            min_ms = 0
            max_ms = 0

            if duration_match:
                avg_ms = float(duration_match.group(1))
                unit = duration_match.group(2)
                min_ms = float(duration_match.group(3))
                max_ms = float(duration_match.group(4))

                # Convert to ms if needed
                if unit == 's':
                    avg_ms *= 1000
                    min_ms *= 1000
                    max_ms *= 1000
                elif unit == 'µs' or unit == 'us':
                    avg_ms /= 1000
                    min_ms /= 1000
                    max_ms /= 1000

            return create_standard_result(
                total_requests=total,
                successful_requests=total,  # Can't determine from text
                failed_requests=0,
                min_response_ms=min_ms,
                avg_response_ms=avg_ms,
                max_response_ms=max_ms,
                requests_per_second=rps,
            )

        return None

    except Exception:
        return None


