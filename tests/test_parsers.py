"""
Tests for load test engine output parsers (Locust, k6, JMeter, Artillery).
"""
import json

import pytest

from cloudstorm.tools.artillery import (
    parse_output as parse_artillery_output,
)
from cloudstorm.tools.base import aggregate_results
from cloudstorm.tools.jmeter import (
    _parse_text_output as parse_jmeter_text,
)
from cloudstorm.tools.jmeter import (
    parse_output as parse_jmeter_output,
)
from cloudstorm.tools.k6 import (
    _parse_text_output as parse_k6_text,
)
from cloudstorm.tools.k6 import (
    parse_output as parse_k6_output,
)
from cloudstorm.tools.locust.parser import (
    parse_output as parse_locust_output,
)
from cloudstorm.tools.locust.parser import (
    parse_text as parse_locust_text,
)
from cloudstorm.tools.locust.parser import (
    percentiles_from_histogram,
)


class TestLocustParser:
    """Test Locust output parsing."""

    def test_json_delimiters_parsing(self):
        stats_data = [
            {
                "name": "Aggregated",
                "method": None,
                "num_requests": 1500,
                "num_failures": 15,
                "current_rps": 50.0,
                "min_response_time": 12.0,
                "avg_response_time": 45.0,
                "max_response_time": 320.0,
                "median_response_time": 40.0,
                "ninetieth_response_time": 85.0,
            },
            {
                "name": "/api/users",
                "method": "GET",
                "num_requests": 1500,
                "num_failures": 15,
                "total_response_time": 67500.0,
                "avg_response_time": 45.0,
                "min_response_time": 12.0,
                "max_response_time": 320.0,
                "response_times": {20: 500, 40: 600, 80: 300, 200: 100},
                "num_reqs_per_sec": {"1700000000": 50},
            },
        ]
        raw_output = f"Noise before\n=== LOCUST_JSON_START ===\n{json.dumps(stats_data)}\n=== LOCUST_JSON_END ===\nNoise after"
        result = parse_locust_output(raw_output)

        assert result is not None
        assert result["summary"]["total_requests"] == 1500
        assert result["summary"]["successful_requests"] == 1485
        assert result["summary"]["failed_requests"] == 15
        assert result["summary"]["error_rate"] == pytest.approx(0.01)
        assert result["response_times"]["min_ms"] == 12.0
        assert result["response_times"]["avg_ms"] == 45.0
        assert result["response_times"]["max_ms"] == 320.0
        assert "/api/users" in result["endpoints"]

    def test_histogram_percentiles(self):
        # 1000 items: 500 at 10ms, 400 at 50ms, 100 at 200ms
        hist = {10: 500, 50: 400, 200: 100}
        p50, p90, p95, p99 = percentiles_from_histogram(hist, [50, 90, 95, 99])
        assert p50 == 10.0
        assert p90 == 50.0
        assert p95 == 200.0
        assert p99 == 200.0

    def test_text_fallback(self):
        text = "Aggregated 500 5(1.0%) 25.5 req/s"
        result = parse_locust_text(text)
        assert result is not None
        assert result["summary"]["total_requests"] == 500
        assert result["summary"]["failed_requests"] == 5
        assert result["summary"]["successful_requests"] == 495
        assert result["summary"]["requests_per_second"] == 25.5


class TestK6Parser:
    """Test k6 output parsing."""

    def test_json_delimiters_parsing(self):
        k6_data = {
            "metrics": {
                "http_reqs": {"values": {"count": 2500, "rate": 83.3}},
                "http_req_failed": {"values": {"passes": 25, "fails": 2475, "rate": 0.01}},
                "http_req_duration": {
                    "values": {
                        "min": 5.2,
                        "avg": 32.1,
                        "max": 450.0,
                        "med": 28.0,
                        "p(90)": 65.0,
                        "p(95)": 95.0,
                        "p(99)": 210.0,
                    }
                },
            }
        }
        raw_output = f"header\n=== K6_JSON_SUMMARY_START ===\n{json.dumps(k6_data)}\n=== K6_JSON_SUMMARY_END ===\nfooter"
        result = parse_k6_output(raw_output)

        assert result is not None
        assert result["summary"]["total_requests"] == 2500
        assert result["summary"]["failed_requests"] == 25
        assert result["summary"]["successful_requests"] == 2475
        assert result["summary"]["error_rate"] == 0.01
        assert result["response_times"]["avg_ms"] == 32.1
        assert result["response_times"]["p95_ms"] == 95.0

    def test_text_fallback(self):
        text = """
          http_reqs..................: 1000   50/s
          http_req_duration..........: avg=25ms min=5ms med=20ms max=250ms p(90)=45ms p(95)=60ms
          http_req_failed............: 2.00%  ✓ 20 ✗ 980
        """
        result = parse_k6_text(text)
        assert result is not None
        assert result["summary"]["total_requests"] == 1000
        assert result["response_times"]["avg_ms"] == 25.0
        assert result["response_times"]["min_ms"] == 5.0
        assert result["response_times"]["max_ms"] == 250.0


class TestJMeterParser:
    """Test JMeter output parsing."""

    def test_json_delimiters_parsing(self):
        jmeter_data = {
            "total_requests": 5000,
            "success_count": 4990,
            "failed_count": 10,
            "requests_per_second": 83.3,
            "min_ms": 8.0,
            "avg_ms": 38.5,
            "max_ms": 512.0,
            "p50_ms": 30.0,
            "p90_ms": 75.0,
            "p95_ms": 110.0,
            "p99_ms": 230.0,
            "endpoints": {
                "GET /items": {
                    "total_requests": 5000,
                    "failed_requests": 10,
                    "avg_ms": 38.5,
                    "min_ms": 8.0,
                    "max_ms": 512.0,
                }
            },
        }
        raw_output = f"=== JMETER_RESULTS_START ===\n{json.dumps(jmeter_data)}\n=== JMETER_RESULTS_END ==="
        result = parse_jmeter_output(raw_output)

        assert result is not None
        assert result["summary"]["total_requests"] == 5000
        assert result["summary"]["failed_requests"] == 10
        assert result["summary"]["successful_requests"] == 4990
        assert result["response_times"]["p90_ms"] == 75.0
        assert result["response_times"]["p99_ms"] == 230.0

    def test_text_fallback(self):
        text = "summary = 1000 in 00:00:30 = 33.3/s Avg: 50 Min: 10 Max: 500 Err: 10 (1.00%)"
        result = parse_jmeter_text(text)
        assert result is not None
        assert result["summary"]["total_requests"] == 1000
        assert result["summary"]["failed_requests"] == 10
        assert result["summary"]["successful_requests"] == 990
        assert result["response_times"]["avg_ms"] == 50.0


class TestArtilleryParser:
    """Test Artillery output parsing."""

    def test_json_delimiters_parsing(self):
        artillery_data = {
            "aggregate": {
                "counters": {
                    "http.requests": 3000,
                    "http.codes.200": 2970,
                    "http.codes.500": 30,
                    "errors.ETIMEDOUT": 5,
                },
                "rates": {
                    "http.request_rate": 50.0,
                },
                "summaries": {
                    "http.response_time": {
                        "min": 15.0,
                        "max": 620.0,
                        "mean": 42.0,
                        "median": 35.0,
                        "p95": 98.0,
                        "p99": 240.0,
                    }
                },
            }
        }
        raw_output = f"=== ARTILLERY_JSON_REPORT_START ===\n{json.dumps(artillery_data)}\n=== ARTILLERY_JSON_REPORT_END ==="
        result = parse_artillery_output(raw_output)

        assert result is not None
        assert result["summary"]["total_requests"] == 3000
        assert result["summary"]["failed_requests"] == 35  # 30 (500 code) + 5 (timeouts)
        assert result["summary"]["successful_requests"] == 2970
        assert result["response_times"]["min_ms"] == 15.0
        assert result["response_times"]["avg_ms"] == 42.0
        assert result["response_times"]["p95_ms"] == 98.0
        assert result["response_times"]["p99_ms"] == 240.0

    def test_null_summary_mean(self):
        """Artillery can emit "mean": null; rebuild it from per-endpoint means."""
        artillery_data = {
            "aggregate": {
                "counters": {"http.requests": 400, "http.codes.200": 400},
                "rates": {"http.request_rate": 21},
                "summaries": {
                    "http.response_time": {
                        "min": 0, "max": 164, "count": 400, "mean": None,
                        "median": 40, "p90": 76, "p95": 79.1, "p99": 98.5,
                    }
                },
            },
            "endpoints": {
                "/api/products": {"requests": 300, "successful": 300, "failed": 0, "avg_ms": 50.0},
                "/api/cart": {"requests": 100, "successful": 100, "failed": 0, "avg_ms": 10.0},
            },
        }
        raw_output = f"=== ARTILLERY_JSON_REPORT_START ===\n{json.dumps(artillery_data)}\n=== ARTILLERY_JSON_REPORT_END ==="
        result = parse_artillery_output(raw_output)

        assert result is not None
        assert result["response_times"]["avg_ms"] == 40.0  # (300*50 + 100*10) / 400
        assert result["response_times"]["p99_ms"] == 98.5
        assert all(v is not None for v in result["response_times"].values())

        # Must aggregate without crashing on None values
        aggregated = aggregate_results([result])
        assert aggregated["response_times"]["avg_ms"] == 40.0
