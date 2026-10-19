"""
SSM (Systems Manager) operations for cloudstorm.
"""
import json
import time
from collections.abc import Callable
from typing import Any

from ..constants import (
    POLL_INTERVAL_FAST,
    POLL_INTERVAL_NORMAL,
)
from ..utils.display import print_error, print_info, print_success, print_warning
from .credentials import get_client


def wait_for_ssm_ready(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
    timeout: int = 300,
) -> list[str]:
    """
    Wait for instances to be SSM-ready.

    Polls SSM describe_instance_information until all instances have
    PingStatus == "Online". Instances with PingStatus "ConnectionLost"
    or "Inactive" (3 consecutive checks) are terminated and removed.
    Times out after `timeout` seconds.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        profile: AWS profile name.
        timeout: Maximum seconds to wait (default 300).

    Returns:
        List of SSM-ready instance IDs.
    """
    if not instance_ids:
        return []

    from .ec2 import terminate_instances

    ssm = get_client("ssm", region, profile)
    start_time = time.time()
    pending = set(instance_ids)
    ready = []
    connection_lost_counts = {}

    while pending:
        elapsed = time.time() - start_time
        if elapsed > timeout:
            print_warning(f"{region}: SSM readiness timeout after {int(elapsed)}s. "
                         f"{len(pending)} instances still not ready: {', '.join(pending)}")
            terminate_instances(region, list(pending), profile)
            break

        try:
            response = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": list(pending)}]
            )

            elapsed_int = int(elapsed)

            for info in response.get("InstanceInformationList", []):
                instance_id = info["InstanceId"]
                ping_status = info.get("PingStatus", "N/A")

                print_info(f"[ssm] {region} ({elapsed_int}s) {instance_id}: "
                           f"PingStatus={ping_status}")

                if ping_status == "Online":
                    ready.append(instance_id)
                    pending.discard(instance_id)
                    connection_lost_counts.pop(instance_id, None)
                elif ping_status in ("ConnectionLost", "Inactive"):
                    connection_lost_counts[instance_id] = connection_lost_counts.get(instance_id, 0) + 1
                    if connection_lost_counts[instance_id] >= 3:
                        print_error(f"{region}: {instance_id} PingStatus={ping_status} for 3 checks - terminating")
                        terminate_instances(region, [instance_id], profile)
                        pending.discard(instance_id)
                    else:
                        print_warning(f"{region}: {instance_id} PingStatus={ping_status} "
                                     f"(attempt {connection_lost_counts[instance_id]}/3)")

            # Show instances not yet registered in SSM
            registered = {info["InstanceId"] for info in response.get("InstanceInformationList", [])}
            missing = pending - registered
            for m in missing:
                print_info(f"[ssm] {region} ({elapsed_int}s) {m}: not yet registered in SSM")

            if not pending:
                break

            time.sleep(POLL_INTERVAL_FAST)

        except Exception as e:
            print_warning(f"Error checking SSM status in {region}: {e}")
            time.sleep(POLL_INTERVAL_FAST)

    if ready:
        print_success(f"All {len(ready)} instances SSM-ready in {region}")

    return ready


def get_ssm_ready_instances(
    region: str,
    instance_ids: list[str],
    profile: str | None = None,
) -> list[str]:
    """
    Get instances that are SSM-ready.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs to check.
        profile: AWS profile name.

    Returns:
        List of SSM-ready instance IDs.
    """
    if not instance_ids:
        return []

    try:
        ssm = get_client("ssm", region, profile)

        response = ssm.describe_instance_information(
            Filters=[{"Key": "InstanceIds", "Values": instance_ids}]
        )

        ready = [
            info["InstanceId"]
            for info in response.get("InstanceInformationList", [])
            if info.get("PingStatus") == "Online"
        ]

        return ready

    except Exception as e:
        print_error(f"Failed to check SSM status in {region}: {e}")
        return []


def run_command(
    region: str,
    instance_ids: list[str],
    command: str,
    timeout: int = 600,
    comment: str = "cloudstorm command",
    profile: str | None = None,
    progress_callback: Callable | None = None,
) -> dict[str, Any]:
    """
    Run a command on instances via SSM.

    Args:
        region: AWS region.
        instance_ids: List of instance IDs.
        command: Shell command to run.
        timeout: Timeout in seconds.
        comment: Command description.
        profile: AWS profile name.

    Returns:
        Dictionary with results per instance.
    """
    if not instance_ids:
        return {"success": False, "error": "No instances provided"}

    try:
        ssm = get_client("ssm", region, profile)

        # Send command
        response = ssm.send_command(
            InstanceIds=instance_ids,
            DocumentName="AWS-RunShellScript",
            Comment=comment,
            Parameters={"commands": [command]},
            TimeoutSeconds=timeout,
        )

        command_id = response["Command"]["CommandId"]
        print_info(f"Command sent to {len(instance_ids)} instances in {region}")

        # Wait for completion (poll longer than SSM timeout to let SSM handle it first)
        return _wait_for_command(ssm, command_id, instance_ids, timeout + 60,
                                progress_callback=progress_callback, region=region)

    except Exception as e:
        print_error(f"Failed to run command in {region}: {e}")
        return {"success": False, "error": str(e)}


def _wait_for_command(
    ssm,
    command_id: str,
    instance_ids: list[str],
    timeout: int,
    progress_callback: Callable | None = None,
    region: str | None = None,
) -> dict[str, Any]:
    """
    Wait for SSM command to complete.

    Args:
        ssm: SSM client.
        command_id: Command ID.
        instance_ids: List of instance IDs.
        timeout: Timeout in seconds.
        progress_callback: Optional callback(region, elapsed, progress_data) for live updates.
        region: AWS region name for progress reporting.

    Returns:
        Dictionary with results.
    """
    start_time = time.time()
    poll_interval = POLL_INTERVAL_FAST
    last_progress_poll = 0

    while time.time() - start_time < timeout:
        try:
            response = ssm.list_commands(CommandId=command_id)

            if not response["Commands"]:
                return {"success": False, "error": "Command not found"}

            cmd = response["Commands"][0]
            status = cmd["Status"]
            elapsed = int(time.time() - start_time)

            if status in ["Success", "Failed", "Cancelled", "TimedOut", "DeliveryTimedOut"]:
                # Final progress collection before returning
                if progress_callback and region:
                    progress = _collect_instance_progress(ssm, instance_ids)
                    progress_callback(region, elapsed, progress)
                print_info(f"Command {status} after {elapsed}s (completed: {cmd.get('CompletedCount', 0)}/{cmd.get('TargetCount', 0)}, errors: {cmd.get('ErrorCount', 0)})")
                return _collect_results(ssm, command_id, instance_ids)

            # Collect progress from instances every 5 seconds
            if progress_callback and region and elapsed - last_progress_poll >= 5:
                last_progress_poll = elapsed
                progress = _collect_instance_progress(ssm, instance_ids)
                progress_callback(region, elapsed, progress)
            elif not progress_callback:
                # Fallback to old-style polling output
                completed = cmd.get("CompletedCount", 0)
                target = cmd.get("TargetCount", 0)
                errors = cmd.get("ErrorCount", 0)
                delivery_timeout = cmd.get("DeliveryTimedOutCount", 0)
                print_info(f"Waiting: status={status}, {completed}/{target} done, {errors} errors, {delivery_timeout} delivery timeouts ({elapsed}s)")

            # Adjust polling
            if elapsed > 120:
                poll_interval = POLL_INTERVAL_NORMAL

            time.sleep(poll_interval)

        except Exception as e:
            print_warning(f"Error checking command status: {e}")
            time.sleep(poll_interval)

    # Timeout: cancel the command so it doesn't keep running and starve the instance
    elapsed = int(time.time() - start_time)
    try:
        ssm.cancel_command(CommandId=command_id)
        print_warning(f"Command timed out after {elapsed}s - cancelled to free instance resources")
    except Exception:
        print_warning(f"Command timed out after {elapsed}s - failed to cancel")

    # Wait for cancellation to propagate before collecting results
    time.sleep(5)

    # Still try to collect any partial results
    return _collect_results(ssm, command_id, instance_ids)


class _ProgressPoller:
    """Async progress poller -- fires SSM commands and collects results on next cycle."""

    def __init__(self):
        self._pending_cmd = None
        self._pending_instances = None
        self._last_data = {"requests": 0, "errors": 0}

    def poll(self, ssm, instance_ids):
        """Called every 10s. Collects previous results and fires new poll."""
        # Collect results from previous poll
        if self._pending_cmd and self._pending_instances:
            total_requests = 0
            total_errors = 0
            for instance_id in self._pending_instances:
                try:
                    inv = ssm.get_command_invocation(
                        CommandId=self._pending_cmd, InstanceId=instance_id
                    )
                    if inv["Status"] == "Success" and inv.get("StandardOutputContent"):
                        data = json.loads(inv["StandardOutputContent"].strip())
                        total_requests += data.get("requests", 0)
                        total_errors += data.get("errors", 0)
                except Exception:
                    pass
            if total_requests > 0:
                self._last_data = {"requests": total_requests, "errors": total_errors}

        # Fire new poll (non-blocking)
        try:
            r = ssm.send_command(
                InstanceIds=instance_ids,
                DocumentName="AWS-RunShellScript",
                Parameters={"commands": ["cat /tmp/cloudstorm-progress.json 2>/dev/null || echo '{}'"]},
                TimeoutSeconds=30,
                Comment="cloudstorm: progress",
            )
            self._pending_cmd = r["Command"]["CommandId"]
            self._pending_instances = instance_ids
        except Exception:
            self._pending_cmd = None

        return self._last_data


# One poller per region (created on demand)
_progress_pollers = {}


def _collect_instance_progress(ssm, instance_ids):
    """Collect progress from instances (non-blocking async pattern)."""
    key = id(ssm)
    if key not in _progress_pollers:
        _progress_pollers[key] = _ProgressPoller()
    return _progress_pollers[key].poll(ssm, instance_ids)


def _collect_results(
    ssm,
    command_id: str,
    instance_ids: list[str],
) -> dict[str, Any]:
    """
    Collect command results from all instances.

    Args:
        ssm: SSM client.
        command_id: Command ID.
        instance_ids: List of instance IDs.

    Returns:
        Dictionary with results.
    """
    results = {}
    success_count = 0

    for instance_id in instance_ids:
        try:
            response = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )

            status = response["Status"]
            results[instance_id] = {
                "status": status,
                "output": response.get("StandardOutputContent", ""),
                "error": response.get("StandardErrorContent", ""),
            }

            if status == "Success":
                success_count += 1

        except ssm.exceptions.InvocationDoesNotExist:
            results[instance_id] = {
                "status": "NotDelivered",
                "output": "",
                "error": "Command was not delivered to this instance",
            }
        except Exception as e:
            results[instance_id] = {
                "status": "Error",
                "output": "",
                "error": str(e),
            }

    return {
        "success": success_count > 0,
        "success_count": success_count,
        "total_count": len(instance_ids),
        "results": results,
    }
