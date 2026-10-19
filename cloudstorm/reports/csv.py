"""
CSV report generation for cloudstorm.
"""
from typing import Any


def generate_csv_report(results: dict[str, Any]) -> str:
    """Generate CSV report from results."""
    lines = []

    # Header
    lines.append("metric,value")

    # Summary
    summary = results.get("summary", {})
    lines.append(f"total_requests,{summary.get('total_requests', 0)}")
    lines.append(f"successful_requests,{summary.get('successful_requests', 0)}")
    lines.append(f"failed_requests,{summary.get('failed_requests', 0)}")
    lines.append(f"error_rate,{summary.get('error_rate', 0)}")
    lines.append(f"requests_per_second,{summary.get('requests_per_second', 0)}")

    # Response times
    times = results.get("response_times", {})
    lines.append(f"min_ms,{times.get('min_ms', 0)}")
    lines.append(f"avg_ms,{times.get('avg_ms', 0)}")
    lines.append(f"max_ms,{times.get('max_ms', 0)}")
    lines.append(f"median_ms,{times.get('median_ms', 0)}")
    lines.append(f"p90_ms,{times.get('p90_ms', 0)}")
    lines.append(f"p95_ms,{times.get('p95_ms', 0)}")
    lines.append(f"p99_ms,{times.get('p99_ms', 0)}")

    return "\n".join(lines)
