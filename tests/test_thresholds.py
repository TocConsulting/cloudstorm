"""
Tests for CI/CD threshold parsing and evaluation in cloudstorm.cli.
"""
import pytest

from cloudstorm.cli import _evaluate_thresholds, _parse_threshold


class TestThresholdParsing:
    """Test _parse_threshold function."""

    def test_parse_with_spaces(self):
        metric, op, val = _parse_threshold("p95 < 500")
        assert metric == "p95"
        assert op == "<"
        assert val == 500.0

    def test_parse_without_spaces(self):
        metric, op, val = _parse_threshold("error_rate<1.5")
        assert metric == "error_rate"
        assert op == "<"
        assert val == 1.5

    def test_parse_operators(self):
        for op_str in ["<", ">", "<=", ">="]:
            metric, op, val = _parse_threshold(f"rps {op_str} 1000")
            assert op == op_str
            assert val == 1000.0

    def test_valid_metrics(self):
        valid = ["p90", "p95", "p99", "avg", "median", "min", "max", "error_rate", "rps", "total_requests"]
        for m in valid:
            metric, op, val = _parse_threshold(f"{m} < 100")
            assert metric == m

    def test_invalid_metric_raises(self):
        with pytest.raises(ValueError, match="Unknown metric"):
            _parse_threshold("latency < 500")

    def test_invalid_operator_raises(self):
        with pytest.raises(ValueError, match="Invalid threshold"):
            _parse_threshold("p95 == 500")

    def test_non_numeric_value_raises(self):
        with pytest.raises(ValueError, match="Invalid threshold"):
            _parse_threshold("p95 < fast")


class TestThresholdEvaluation:
    """Test _evaluate_thresholds against mock results."""

    @pytest.fixture
    def mock_results(self):
        return {
            "summary": {
                "total_requests": 10000,
                "successful_requests": 9950,
                "failed_requests": 50,
                "error_rate": 0.005,  # 0.5%
                "requests_per_second": 500.0,
            },
            "response_times": {
                "min_ms": 10.0,
                "avg_ms": 45.0,
                "max_ms": 650.0,
                "median_ms": 35.0,
                "p90_ms": 80.0,
                "p95_ms": 120.0,
                "p99_ms": 250.0,
            },
        }

    def test_all_passing(self, mock_results):
        thresholds = ("p95 < 200", "p99 < 300", "error_rate < 1", "rps >= 400")
        evaluations = _evaluate_thresholds(thresholds, mock_results)
        assert len(evaluations) == 4
        assert all(e["passed"] for e in evaluations)

    def test_one_failing(self, mock_results):
        thresholds = ("p95 < 100", "error_rate < 1")  # p95 is 120, so 120 < 100 will fail
        evaluations = _evaluate_thresholds(thresholds, mock_results)
        assert evaluations[0]["passed"] is False
        assert evaluations[0]["metric"] == "p95"
        assert evaluations[1]["passed"] is True

    def test_error_rate_percentage_conversion(self, mock_results):
        # error_rate in summary is 0.005 (0.5%), threshold compares against percentage
        thresholds = ("error_rate <= 0.5",)
        evaluations = _evaluate_thresholds(thresholds, mock_results)
        assert evaluations[0]["passed"] is True
        assert evaluations[0]["actual"] == 0.5
