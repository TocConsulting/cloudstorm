"""
Artillery load testing tool plugin for cloudstorm.

Artillery is a Node.js-based load testing toolkit.
https://www.artillery.io
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
    "name": "Artillery",
    "description": "Node.js-based load testing toolkit",
    "website": "https://www.artillery.io",
    "language": "Node.js",
}


def install(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Install Artillery on instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        profile: AWS profile.

    Returns:
        Installation result with success status.
    """
    print_info(f"Installing Artillery on {len(instance_ids)} instances in {region}")

    script = get_tool_script(TOOL_DIR,"install.sh")

    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=script,
        timeout=600,  # Node.js installation can take longer
        comment="cloudstorm: Install Artillery",
        profile=profile,
    )

    if result.get("success"):
        print_success(f"Artillery installed on {result.get('success_count', 0)} instances in {region}")
    else:
        print_error(f"Failed to install Artillery in {region}: {result.get('error', 'Unknown error')}")

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
    Run Artillery load test on instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        target_url: Target URL to test.
        users: Number of concurrent users.
        spawn_rate: Ramp-up duration in seconds.
        duration: Test duration (e.g., "60s", "5m").
        endpoints: Comma-separated endpoints to test.
        profile: AWS profile.
        script_content: Optional custom Artillery config content.

    Returns:
        Test result with parsed metrics.
    """
    print_info(f"Running Artillery test in {region}: {target_url}")

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
        encoded = base64.b64encode(script_content.encode()).decode()
        env_vars += f'\nexport CUSTOM_SCRIPT_B64="{encoded}"\n'
        trimmed = script_content.strip()
        script_ext = "json" if trimmed.startswith("{") or trimmed.startswith("[") else "yaml"
        env_vars += f'export CUSTOM_SCRIPT_EXT="{script_ext}"\n'

    full_script = env_vars + "\n" + script

    safe_url = urlparse(target_url)._replace(netloc=urlparse(target_url).hostname or "").geturl()
    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=full_script,
        timeout=timeout,
        comment=f"cloudstorm: Artillery test {safe_url}",
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
            print_success(f"Artillery test completed in {region}: {result['parsed']['summary']['total_requests']} requests")
        else:
            print_error(f"Failed to parse Artillery results in {region} (0/{len(result['results'])} instances produced data)")
    else:
        print_error(f"Artillery test failed in {region}: {result.get('error', 'No results returned')}")

    return result


def parse_output(output: str) -> dict[str, Any] | None:
    """
    Parse Artillery output into standardized format.

    Args:
        output: Raw output from Artillery (expects JSON report).

    Returns:
        Standardized results dictionary or None if parsing fails.
    """
    try:
        # Find JSON report in output
        json_match = re.search(
            r'=== ARTILLERY_JSON_REPORT_START ===\s*([\s\S]*?)\s*=== ARTILLERY_JSON_REPORT_END ===',
            output
        )

        if json_match:
            data = json.loads(json_match.group(1))
            return _parse_json_report(data)

        # Fallback: try to parse text output
        return _parse_text_output(output)

    except json.JSONDecodeError as e:
        print_error(f"Failed to parse Artillery JSON: {e}")
        return None
    except Exception as e:
        print_error(f"Failed to parse Artillery output: {e}")
        return None


def _is_number(value: Any) -> bool:
    """True for real numbers (not None, NaN or bool)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool) and value == value


def _num(value: Any) -> float:
    """Return value if it is a real number, else 0."""
    return value if _is_number(value) else 0


def _parse_json_report(data: dict) -> dict[str, Any] | None:
    """Parse Artillery JSON report format."""
    try:
        aggregate = data.get("aggregate", {})

        # Get counters
        counters = aggregate.get("counters", {})
        total_requests = counters.get("http.requests", 0)
        successful = 0
        for key, value in counters.items():
            if key.startswith("http.codes."):
                code = int(key.split(".")[-1])
                if 200 <= code < 400:
                    successful += value

        # Count failures (non-2xx responses + errors)
        failed = 0
        for key, value in counters.items():
            if key.startswith("http.codes."):
                code = int(key.split(".")[-1])
                if code >= 400:
                    failed += value
            elif key.startswith("errors."):
                failed += value

        if total_requests == 0:
            return None

        # Get summary stats
        summaries = aggregate.get("summaries", {})
        rt_summary = summaries.get("http.response_time", {})

        # Extract per-endpoint stats
        endpoints = data.get("endpoints", {})

        # Artillery can emit null for summary fields (e.g. "mean": null)
        avg_ms = rt_summary.get("mean")
        if not _is_number(avg_ms):
            # Rebuild the mean from per-endpoint means weighted by request count
            weighted = [
                (ep.get("avg_ms"), ep.get("requests"))
                for ep in endpoints.values()
                if _is_number(ep.get("avg_ms")) and _is_number(ep.get("requests"))
            ]
            count = sum(reqs for _, reqs in weighted)
            avg_ms = sum(avg * reqs for avg, reqs in weighted) / count if count else 0

        return create_standard_result(
            total_requests=total_requests,
            successful_requests=successful,
            failed_requests=failed,
            min_response_ms=_num(rt_summary.get("min")),
            avg_response_ms=avg_ms,
            max_response_ms=_num(rt_summary.get("max")),
            median_response_ms=_num(rt_summary.get("median")),
            p90_response_ms=_num(rt_summary.get("p90")),
            p95_response_ms=_num(rt_summary.get("p95")),
            p99_response_ms=_num(rt_summary.get("p99")),
            requests_per_second=_num(aggregate.get("rates", {}).get("http.request_rate")),
            endpoints=endpoints,
        )

    except Exception as e:
        print_error(f"Failed to parse Artillery JSON report: {e}")
        return None


def _parse_text_output(output: str) -> dict[str, Any] | None:
    """Parse Artillery text output as fallback."""
    try:
        # Artillery outputs statistics like:
        # http.requests: 1000
        # http.response_time:
        #   min: 10
        #   max: 500
        #   mean: 50

        requests_match = re.search(r'http\.requests[:\s]+(\d+)', output)
        rps_match = re.search(r'http\.request_rate[:\s]+(\d+\.?\d*)', output)

        if requests_match:
            total = int(requests_match.group(1))
            rps = float(rps_match.group(1)) if rps_match else 0

            # Try to extract response times
            min_match = re.search(r'min[:\s]+(\d+\.?\d*)', output)
            max_match = re.search(r'max[:\s]+(\d+\.?\d*)', output)
            mean_match = re.search(r'mean[:\s]+(\d+\.?\d*)', output)

            return create_standard_result(
                total_requests=total,
                successful_requests=total,  # Can't determine from text
                failed_requests=0,
                min_response_ms=float(min_match.group(1)) if min_match else 0,
                avg_response_ms=float(mean_match.group(1)) if mean_match else 0,
                max_response_ms=float(max_match.group(1)) if max_match else 0,
                requests_per_second=rps,
            )

        return None

    except Exception:
        return None
