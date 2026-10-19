"""
Base functions for load testing tools.
"""
from pathlib import Path
from typing import Any

from ..utils.display import print_error, print_warning


def get_tool_script(tool_dir: Path, name: str) -> str:
    """Read a script from a tool's directory."""
    script_path = tool_dir / name
    return script_path.read_text()


def create_standard_result(
    total_requests: int = 0,
    successful_requests: int = 0,
    failed_requests: int = 0,
    min_response_ms: float = 0,
    avg_response_ms: float = 0,
    max_response_ms: float = 0,
    median_response_ms: float = 0,
    p90_response_ms: float = 0,
    p95_response_ms: float = 0,
    p99_response_ms: float = 0,
    requests_per_second: float = 0,
    errors: dict[str, int] = None,
    endpoints: dict[str, dict] = None,
) -> dict[str, Any]:
    """
    Create a standardized result dictionary.

    All tools should convert their output to this format.
    """
    return {
        "summary": {
            "total_requests": total_requests,
            "successful_requests": successful_requests,
            "failed_requests": failed_requests,
            "requests_per_second": requests_per_second,
            "error_rate": failed_requests / total_requests if total_requests > 0 else 0,
        },
        "response_times": {
            "min_ms": min_response_ms,
            "avg_ms": avg_response_ms,
            "max_ms": max_response_ms,
            "median_ms": median_response_ms,
            "p90_ms": p90_response_ms,
            "p95_ms": p95_response_ms,
            "p99_ms": p99_response_ms,
        },
        "errors": errors or {},
        "endpoints": endpoints or {},
    }


def parse_duration(duration: str) -> int:
    """
    Parse duration string to seconds.

    Args:
        duration: Duration string (e.g., "5m", "30s", "1h").

    Returns:
        Duration in seconds.
    """
    duration = str(duration).strip().lower()

    if duration.endswith("h"):
        return int(duration[:-1]) * 3600
    elif duration.endswith("m"):
        return int(duration[:-1]) * 60
    elif duration.endswith("s"):
        return int(duration[:-1])
    else:
        return int(duration)


def check_oom_kill(output: str, error_output: str, instance_id: str) -> bool:
    """Check if a process was OOM-killed and print a clear message."""
    combined = (output + " " + error_output).lower()
    if "killed" in combined or "exit code: 137" in combined or "signal 9" in combined:
        short_id = instance_id[-8:]
        print_error(f"  {short_id}: Process was KILLED (likely out of memory)")
        print_warning(f"  {short_id}: Try using fewer users per instance or a larger instance type")
        return True
    return False


def aggregate_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    """
    Aggregate results from multiple instances.

    Percentiles are weighted averages -- an approximation without raw histograms.

    Args:
        results: List of parsed results from each instance.

    Returns:
        Aggregated results.
    """
    if not results:
        return create_standard_result()

    total_requests = sum(r["summary"]["total_requests"] for r in results)
    successful = sum(r["summary"]["successful_requests"] for r in results)
    failed = sum(r["summary"]["failed_requests"] for r in results)
    rps = sum(r["summary"]["requests_per_second"] for r in results)

    all_mins = [r["response_times"]["min_ms"] for r in results if r["response_times"]["min_ms"] > 0]
    all_maxes = [r["response_times"]["max_ms"] for r in results if r["response_times"]["max_ms"] > 0]

    min_response = min(all_mins) if all_mins else 0
    max_response = max(all_maxes) if all_maxes else 0

    total_weight = sum(r["summary"]["total_requests"] for r in results)

    def weighted_avg(key: str) -> float:
        if total_weight == 0:
            return 0
        return sum(
            r["response_times"][key] * r["summary"]["total_requests"]
            for r in results
        ) / total_weight

    # Merge per-endpoint stats from all instances
    merged_endpoints: dict[str, dict] = {}
    for r in results:
        for name, ep in r.get("endpoints", {}).items():
            if name not in merged_endpoints:
                merged_endpoints[name] = {"requests": 0, "successful": 0, "failed": 0, "total_time": 0.0, "min": float('inf'), "max": 0.0}
            m = merged_endpoints[name]
            reqs = ep.get("requests", 0)
            m["requests"] += reqs
            m["successful"] += ep.get("successful", 0)
            m["failed"] += ep.get("failed", 0)
            m["total_time"] += ep.get("avg_ms", 0) * reqs
            ep_min = ep.get("min_ms", 0)
            ep_max = ep.get("max_ms", 0)
            if ep_min > 0:
                m["min"] = min(m["min"], ep_min)
            if ep_max > 0:
                m["max"] = max(m["max"], ep_max)

    endpoints_final: dict[str, dict] = {}
    for name, m in merged_endpoints.items():
        endpoints_final[name] = {
            "requests": m["requests"],
            "successful": m["successful"],
            "failed": m["failed"],
            "avg_ms": m["total_time"] / m["requests"] if m["requests"] > 0 else 0,
            "min_ms": m["min"] if m["min"] != float('inf') else 0,
            "max_ms": m["max"],
        }

    return create_standard_result(
        total_requests=total_requests,
        successful_requests=successful,
        failed_requests=failed,
        min_response_ms=min_response,
        avg_response_ms=weighted_avg("avg_ms"),
        max_response_ms=max_response,
        median_response_ms=weighted_avg("median_ms"),
        p90_response_ms=weighted_avg("p90_ms"),
        p95_response_ms=weighted_avg("p95_ms"),
        p99_response_ms=weighted_avg("p99_ms"),
        requests_per_second=rps,
        endpoints=endpoints_final,
    )
