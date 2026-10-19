"""
Tests for result aggregation (per-instance in tools.base and per-region in cli).
"""
import pytest

from cloudstorm.cli import _aggregate_all_results
from cloudstorm.tools.base import aggregate_results, create_standard_result


class TestInstanceAggregation:
    """Test aggregation across multiple EC2 instances within a region."""

    def test_aggregate_empty(self):
        result = aggregate_results([])
        assert result["summary"]["total_requests"] == 0
        assert result["summary"]["successful_requests"] == 0
        assert result["summary"]["failed_requests"] == 0
        assert result["response_times"]["avg_ms"] == 0

    def test_aggregate_single_instance(self):
        inst1 = create_standard_result(
            total_requests=1000,
            successful_requests=980,
            failed_requests=20,
            min_response_ms=10.0,
            avg_response_ms=50.0,
            max_response_ms=200.0,
            median_response_ms=45.0,
            p90_response_ms=90.0,
            p95_response_ms=120.0,
            p99_response_ms=180.0,
            requests_per_second=33.3,
            endpoints={
                "/api/items": {
                    "requests": 1000,
                    "successful": 980,
                    "failed": 20,
                    "avg_ms": 50.0,
                    "min_ms": 10.0,
                    "max_ms": 200.0,
                }
            },
        )
        agg = aggregate_results([inst1])
        assert agg["summary"]["total_requests"] == 1000
        assert agg["summary"]["successful_requests"] == 980
        assert agg["summary"]["failed_requests"] == 20
        assert agg["summary"]["requests_per_second"] == pytest.approx(33.3)
        assert agg["response_times"]["min_ms"] == 10.0
        assert agg["response_times"]["max_ms"] == 200.0
        assert agg["response_times"]["avg_ms"] == 50.0
        assert agg["endpoints"]["/api/items"]["requests"] == 1000

    def test_aggregate_multiple_instances(self):
        # Instance 1: 1000 requests, avg 40ms, min 10ms, max 100ms
        inst1 = create_standard_result(
            total_requests=1000,
            successful_requests=990,
            failed_requests=10,
            min_response_ms=10.0,
            avg_response_ms=40.0,
            max_response_ms=100.0,
            median_response_ms=35.0,
            p90_response_ms=70.0,
            p95_response_ms=85.0,
            p99_response_ms=95.0,
            requests_per_second=50.0,
            endpoints={
                "/users": {
                    "requests": 1000,
                    "successful": 990,
                    "failed": 10,
                    "avg_ms": 40.0,
                    "min_ms": 10.0,
                    "max_ms": 100.0,
                }
            },
        )
        # Instance 2: 3000 requests, avg 60ms, min 5ms, max 300ms
        inst2 = create_standard_result(
            total_requests=3000,
            successful_requests=2950,
            failed_requests=50,
            min_response_ms=5.0,
            avg_response_ms=60.0,
            max_response_ms=300.0,
            median_response_ms=55.0,
            p90_response_ms=110.0,
            p95_response_ms=140.0,
            p99_response_ms=250.0,
            requests_per_second=150.0,
            endpoints={
                "/users": {
                    "requests": 3000,
                    "successful": 2950,
                    "failed": 50,
                    "avg_ms": 60.0,
                    "min_ms": 5.0,
                    "max_ms": 300.0,
                }
            },
        )

        agg = aggregate_results([inst1, inst2])

        assert agg["summary"]["total_requests"] == 4000
        assert agg["summary"]["successful_requests"] == 3940
        assert agg["summary"]["failed_requests"] == 60
        assert agg["summary"]["requests_per_second"] == 200.0
        assert agg["response_times"]["min_ms"] == 5.0
        assert agg["response_times"]["max_ms"] == 300.0
        # Weighted avg: (1000*40 + 3000*60) / 4000 = (40000 + 180000) / 4000 = 220000 / 4000 = 55.0
        assert agg["response_times"]["avg_ms"] == pytest.approx(55.0)
        # Endpoint merging:
        ep = agg["endpoints"]["/users"]
        assert ep["requests"] == 4000
        assert ep["successful"] == 3940
        assert ep["failed"] == 60
        assert ep["avg_ms"] == pytest.approx(55.0)
        assert ep["min_ms"] == 5.0
        assert ep["max_ms"] == 300.0


class TestRegionalAggregation:
    """Test aggregation across multiple AWS regions."""

    def test_aggregate_all_empty(self):
        res = _aggregate_all_results({})
        assert res["summary"]["total_requests"] == 0
        assert res["summary"]["successful_requests"] == 0

        res_none = _aggregate_all_results({"us-east-1": None})
        assert res_none["summary"]["total_requests"] == 0

    def test_aggregate_multi_region(self):
        region_data = {
            "us-east-1": {
                "parsed": create_standard_result(
                    total_requests=2000,
                    successful_requests=1980,
                    failed_requests=20,
                    min_response_ms=8.0,
                    avg_response_ms=45.0,
                    max_response_ms=180.0,
                    median_response_ms=40.0,
                    p90_response_ms=80.0,
                    p95_response_ms=100.0,
                    p99_response_ms=150.0,
                    requests_per_second=100.0,
                    endpoints={
                        "/api/v1": {
                            "requests": 2000,
                            "successful": 1980,
                            "failed": 20,
                            "avg_ms": 45.0,
                            "min_ms": 8.0,
                            "max_ms": 180.0,
                        }
                    },
                )
            },
            "eu-west-1": {
                "parsed": create_standard_result(
                    total_requests=1000,
                    successful_requests=970,
                    failed_requests=30,
                    min_response_ms=12.0,
                    avg_response_ms=75.0,
                    max_response_ms=250.0,
                    median_response_ms=70.0,
                    p90_response_ms=120.0,
                    p95_response_ms=160.0,
                    p99_response_ms=220.0,
                    requests_per_second=50.0,
                    endpoints={
                        "/api/v1": {
                            "requests": 1000,
                            "successful": 970,
                            "failed": 30,
                            "avg_ms": 75.0,
                            "min_ms": 12.0,
                            "max_ms": 250.0,
                        }
                    },
                )
            },
            "ap-southeast-1": None,  # Test handling of failed/None region
        }

        agg = _aggregate_all_results(region_data)

        assert agg["summary"]["total_requests"] == 3000
        assert agg["summary"]["successful_requests"] == 2950
        assert agg["summary"]["failed_requests"] == 50
        assert agg["summary"]["requests_per_second"] == 150.0
        assert agg["response_times"]["min_ms"] == 8.0
        assert agg["response_times"]["max_ms"] == 250.0
        # Weighted avg: (2000*45 + 1000*75) / 3000 = (90000 + 75000) / 3000 = 165000 / 3000 = 55.0
        assert agg["response_times"]["avg_ms"] == pytest.approx(55.0)
        assert agg["endpoints"]["/api/v1"]["requests"] == 3000
        assert agg["endpoints"]["/api/v1"]["failed"] == 50
