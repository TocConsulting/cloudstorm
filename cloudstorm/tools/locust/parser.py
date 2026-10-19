"""
Locust output parser.

Parses the JSON output from locust --json into the cloudstorm standard format.

Locust --json produces a list of entries per endpoint, each with:
- num_requests, num_failures, total_response_time
- min_response_time, max_response_time
- response_times: histogram {ms_bucket: count} (NOT percentiles)
- num_reqs_per_sec: {timestamp: count}
"""
import json
import re
from typing import Any

from ...utils.display import print_error
from ..base import aggregate_results, create_standard_result  # noqa: F401


def parse_output(output: str) -> dict[str, Any] | None:
    """
    Parse Locust output into standardized format.

    Args:
        output: Raw output from Locust (expects JSON format from --json flag).

    Returns:
        Standardized results dictionary or None if parsing fails.
    """
    try:
        json_match = re.search(
            r'=== LOCUST_JSON_START ===\s*([\s\S]*?)\s*=== LOCUST_JSON_END ===',
            output
        )

        if json_match:
            data = json.loads(json_match.group(1))
            return parse_json(data)

        return parse_text(output)

    except json.JSONDecodeError as e:
        print_error(f"Failed to parse Locust JSON: {e}")
        return None
    except Exception as e:
        print_error(f"Failed to parse Locust output: {e}")
        return None


def parse_json(data: list[dict]) -> dict[str, Any] | None:
    """Parse Locust JSON output format."""
    if not data:
        return None

    total_requests = 0
    total_failures = 0
    total_response_time = 0.0
    min_response = float('inf')
    max_response = 0.0
    merged_histogram = {}
    rps_by_timestamp = {}  # {timestamp: total_count} sum across endpoints
    endpoint_stats = {}  # name -> {requests, failed, total_time, min, max}

    for entry in data:
        if not isinstance(entry, dict):
            continue

        if entry.get("name") == "Aggregated":
            continue

        name = entry.get("name", "")
        if name:
            if name not in endpoint_stats:
                endpoint_stats[name] = {"requests": 0, "failed": 0, "total_time": 0.0, "min": float('inf'), "max": 0.0}
            ep = endpoint_stats[name]
            ep["requests"] += entry.get("num_requests", 0)
            ep["failed"] += entry.get("num_failures", 0)
            ep["total_time"] += entry.get("total_response_time", 0.0)
            ep_entry_min = entry.get("min_response_time", 0)
            ep_entry_max = entry.get("max_response_time", 0)
            if ep_entry_min > 0:
                ep["min"] = min(ep["min"], ep_entry_min)
            if ep_entry_max > 0:
                ep["max"] = max(ep["max"], ep_entry_max)

        total_requests += entry.get("num_requests", 0)
        total_failures += entry.get("num_failures", 0)
        total_response_time += entry.get("total_response_time", 0.0)

        entry_min = entry.get("min_response_time", 0)
        entry_max = entry.get("max_response_time", 0)
        if entry_min > 0:
            min_response = min(min_response, entry_min)
        if entry_max > 0:
            max_response = max(max_response, entry_max)

        # response_times is a histogram: {ms_bucket: count}
        for ms_str, count in entry.get("response_times", {}).items():
            ms = int(float(ms_str))
            merged_histogram[ms] = merged_histogram.get(ms, 0) + count

        # num_reqs_per_sec: {timestamp: count} -- sum across endpoints per timestamp
        for ts, count in entry.get("num_reqs_per_sec", {}).items():
            rps_by_timestamp[ts] = rps_by_timestamp.get(ts, 0) + count

    if total_requests == 0:
        return None

    avg_response = total_response_time / total_requests
    rps = sum(rps_by_timestamp.values()) / len(rps_by_timestamp) if rps_by_timestamp else 0.0

    p50, p90, p95, p99 = percentiles_from_histogram(merged_histogram, [50, 90, 95, 99])

    # Convert per-endpoint stats to final format
    endpoints = {}
    for ep_name, ep in endpoint_stats.items():
        endpoints[ep_name] = {
            "requests": ep["requests"],
            "successful": ep["requests"] - ep["failed"],
            "failed": ep["failed"],
            "avg_ms": ep["total_time"] / ep["requests"] if ep["requests"] > 0 else 0,
            "min_ms": ep["min"] if ep["min"] != float('inf') else 0,
            "max_ms": ep["max"],
        }

    return create_standard_result(
        total_requests=total_requests,
        successful_requests=total_requests - total_failures,
        failed_requests=total_failures,
        min_response_ms=min_response if min_response != float('inf') else 0,
        avg_response_ms=avg_response,
        max_response_ms=max_response,
        median_response_ms=p50,
        p90_response_ms=p90,
        p95_response_ms=p95,
        p99_response_ms=p99,
        requests_per_second=rps,
        endpoints=endpoints,
    )


def parse_text(output: str) -> dict[str, Any] | None:
    """Parse Locust text output as fallback."""
    try:
        requests_match = re.search(r'Aggregated\s+(\d+)', output)
        failures_match = re.search(r'(\d+)\s*\(\d+\.?\d*%\)', output)
        rps_match = re.search(r'(\d+\.?\d*)\s*(?:req/s|RPS)', output, re.IGNORECASE)

        if requests_match:
            total = int(requests_match.group(1))
            failures = int(failures_match.group(1)) if failures_match else 0
            rps = float(rps_match.group(1)) if rps_match else 0

            return create_standard_result(
                total_requests=total,
                successful_requests=total - failures,
                failed_requests=failures,
                requests_per_second=rps,
            )

        return None

    except Exception:
        return None


def percentiles_from_histogram(histogram: dict[int, int], targets: list[int]) -> list[float]:
    """Compute percentiles from a {ms_bucket: count} histogram."""
    if not histogram:
        return [0.0] * len(targets)

    sorted_buckets = sorted(histogram.items())
    total = sum(count for _, count in sorted_buckets)
    if total == 0:
        return [0.0] * len(targets)

    results = []
    for pct in targets:
        threshold = total * pct / 100.0
        cumulative = 0
        value = 0.0
        for ms, count in sorted_buckets:
            cumulative += count
            if cumulative >= threshold:
                value = float(ms)
                break
        results.append(value)

    return results


