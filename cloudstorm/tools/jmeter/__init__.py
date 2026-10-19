"""
JMeter load testing tool plugin for cloudstorm.

JMeter is a Java-based load testing tool from Apache.
https://jmeter.apache.org
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
    "name": "JMeter",
    "description": "Java-based load testing tool from Apache",
    "website": "https://jmeter.apache.org",
    "language": "Java",
}


def install(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> dict[str, Any]:
    """
    Install JMeter on instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        profile: AWS profile.

    Returns:
        Installation result with success status.
    """
    print_info(f"Installing JMeter on {len(instance_ids)} instances in {region}")

    script = get_tool_script(TOOL_DIR,"install.sh")

    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=script,
        timeout=600,  # Java + JMeter download can take a while
        comment="cloudstorm: Install JMeter",
        profile=profile,
    )

    if result.get("success"):
        print_success(f"JMeter installed on {result.get('success_count', 0)} instances in {region}")
    else:
        print_error(f"Failed to install JMeter in {region}: {result.get('error', 'Unknown error')}")

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
    Run JMeter load test on instances.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        target_url: Target URL to test.
        users: Number of threads (virtual users).
        spawn_rate: Users spawned per second.
        duration: Test duration (e.g., "60s", "5m").
        endpoints: Comma-separated endpoints to test.
        profile: AWS profile.
        script_content: Optional custom JMeter test plan content.

    Returns:
        Test result with parsed metrics.
    """
    print_info(f"Running JMeter test in {region}: {target_url}")

    # Calculate per-instance values
    users_per_instance = max(1, users // len(instance_ids))

    duration_seconds = parse_duration(duration)
    # JMeter needs extra buffer: gawk processes the entire JTL file after the test,
    # which scales with request count (users * duration), not just user count
    user_buffer = max(0, users_per_instance // 1000) * 30
    jtl_buffer = min(300, duration_seconds)  # extra time for JTL processing
    timeout = duration_seconds + TIMEOUT_TEST_BUFFER + user_buffer + jtl_buffer

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

    full_script = env_vars + "\n" + script

    safe_url = urlparse(target_url)._replace(netloc=urlparse(target_url).hostname or "").geturl()
    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=full_script,
        timeout=timeout,
        comment=f"cloudstorm: JMeter test {safe_url}",
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
            print_success(f"JMeter test completed in {region}: {result['parsed']['summary']['total_requests']} requests")
        else:
            print_error(f"Failed to parse JMeter results in {region} (0/{len(result['results'])} instances produced data)")
    else:
        print_error(f"JMeter test failed in {region}: {result.get('error', 'No results returned')}")

    return result


def parse_output(output: str) -> dict[str, Any] | None:
    """
    Parse JMeter output into standardized format.

    Args:
        output: Raw output from JMeter run script.

    Returns:
        Standardized results dictionary or None if parsing fails.
    """
    try:
        # Find our custom results section
        results_match = re.search(
            r'=== JMETER_RESULTS_START ===\s*([\s\S]*?)\s*=== JMETER_RESULTS_END ===',
            output
        )

        if results_match:
            results_text = results_match.group(1)
            return _parse_results_text(results_text)

        # Fallback: try to parse generic JMeter output
        return _parse_text_output(output)

    except Exception as e:
        print_error(f"Failed to parse JMeter output: {e}")
        return None


def _parse_results_text(text: str) -> dict[str, Any] | None:
    """Parse our custom JMeter results format (single JSON object)."""
    try:
        # Find the outermost JSON object (may contain nested objects like endpoints)
        start = text.find('{')
        if start < 0:
            return None
        depth = 0
        end = None
        for i in range(start, len(text)):
            if text[i] == '{':
                depth += 1
            elif text[i] == '}':
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end is None:
            return None

        stats = json.loads(text[start:end])

        total = stats.get("total_requests", 0)
        if total == 0:
            return None

        success = stats.get("success_count", total)
        failed = stats.get("failed_count", 0)
        rps = stats.get("requests_per_second", 0)
        endpoints = stats.get("endpoints", {})

        return create_standard_result(
            total_requests=total,
            successful_requests=success,
            failed_requests=failed,
            min_response_ms=stats.get("min_ms", 0),
            avg_response_ms=stats.get("avg_ms", 0),
            max_response_ms=stats.get("max_ms", 0),
            median_response_ms=stats.get("p50_ms", 0),
            p90_response_ms=stats.get("p90_ms", 0),
            p95_response_ms=stats.get("p95_ms", 0),
            p99_response_ms=stats.get("p99_ms", 0),
            requests_per_second=rps,
            endpoints=endpoints,
        )

    except json.JSONDecodeError:
        return None
    except Exception:
        return None


def _parse_text_output(output: str) -> dict[str, Any] | None:
    """Parse JMeter text output as fallback."""
    try:
        # Look for common JMeter output patterns
        # summary = 1000 in 00:00:30 = 33.3/s Avg: 50 Min: 10 Max: 500 Err: 10 (1.00%)

        summary_match = re.search(
            r'summary\s*=\s*(\d+)\s+in\s+[\d:]+\s*=\s*(\d+\.?\d*)/s\s+Avg:\s*(\d+)\s+Min:\s*(\d+)\s+Max:\s*(\d+)',
            output
        )
        error_match = re.search(r'Err:\s*(\d+)', output)

        if summary_match:
            total = int(summary_match.group(1))
            rps = float(summary_match.group(2))
            avg_ms = float(summary_match.group(3))
            min_ms = float(summary_match.group(4))
            max_ms = float(summary_match.group(5))
            errors = int(error_match.group(1)) if error_match else 0

            return create_standard_result(
                total_requests=total,
                successful_requests=total - errors,
                failed_requests=errors,
                min_response_ms=min_ms,
                avg_response_ms=avg_ms,
                max_response_ms=max_ms,
                requests_per_second=rps,
            )

        return None

    except Exception:
        return None
