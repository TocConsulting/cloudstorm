"""
Locust load testing tool plugin for cloudstorm.

Locust is a Python-based load testing tool.
https://locust.io
"""
import base64
import shlex
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ...aws.ssm import run_command
from ...constants import TIMEOUT_TEST_BUFFER
from ...utils.display import print_error, print_info, print_success
from ..base import check_oom_kill, get_tool_script, parse_duration
from .parser import aggregate_results, parse_output

TOOL_DIR = Path(__file__).parent

TOOL_INFO = {
    "name": "Locust",
    "description": "Python-based load testing tool",
    "website": "https://locust.io",
    "language": "Python",
}


def install(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> dict[str, Any]:
    """Install Locust on instances."""
    print_info(f"Installing Locust on {len(instance_ids)} instances in {region}")

    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=get_tool_script(TOOL_DIR,"install.sh"),
        timeout=300,
        comment="cloudstorm: Install Locust",
        profile=profile,
    )

    if result.get("success"):
        print_success(f"Locust installed on {result.get('success_count', 0)} instances in {region}")
    else:
        print_error(f"Failed to install Locust in {region}: {result.get('error', 'Unknown error')}")

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
    """Run Locust load test on instances."""
    print_info(f"Running Locust test in {region}: {target_url}")

    users_per_instance = max(1, users // len(instance_ids))
    spawn_rate_per_instance = max(1, spawn_rate // len(instance_ids))
    duration_seconds = parse_duration(duration)
    # Buffer scales with user count: more users = longer teardown/output generation
    user_buffer = max(0, users_per_instance // 1000) * 30  # ~30s per 1K users
    timeout = duration_seconds + TIMEOUT_TEST_BUFFER + user_buffer

    script = get_tool_script(TOOL_DIR,"run.sh")
    env_vars = f"""
export TARGET_URL={shlex.quote(target_url)}
export USERS={users_per_instance}
export SPAWN_RATE={spawn_rate_per_instance}
export DURATION={shlex.quote(duration)}
export ENDPOINTS={shlex.quote(endpoints)}
"""
    if script_content:
        encoded = base64.b64encode(script_content.encode()).decode()
        env_vars += f'\nexport CUSTOM_SCRIPT_B64="{encoded}"\n'
    safe_url = urlparse(target_url)._replace(netloc=urlparse(target_url).hostname or "").geturl()
    result = run_command(
        region=region,
        instance_ids=instance_ids,
        command=env_vars + "\n" + script,
        timeout=timeout,
        comment=f"cloudstorm: Locust test {safe_url}",
        profile=profile,
        progress_callback=progress_callback,
    )

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
            print_success(f"Locust test completed in {region}: {result['parsed']['summary']['total_requests']} requests")
        else:
            print_error(f"Failed to parse Locust results in {region} (0/{len(result['results'])} instances produced data)")
    else:
        print_error(f"Locust test failed in {region}: {result.get('error', 'No results returned')}")

    return result
