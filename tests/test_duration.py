"""
Tests for duration parsing utilities in cloudstorm.tools.base.
"""
import pytest

from cloudstorm.tools.base import parse_duration


class TestParseDuration:
    """Test parse_duration function."""

    def test_seconds_suffix(self):
        assert parse_duration("30s") == 30
        assert parse_duration("1s") == 1
        assert parse_duration("120s") == 120

    def test_minutes_suffix(self):
        assert parse_duration("1m") == 60
        assert parse_duration("5m") == 300
        assert parse_duration("10m") == 600

    def test_hours_suffix(self):
        assert parse_duration("1h") == 3600
        assert parse_duration("2h") == 7200

    def test_plain_integer(self):
        assert parse_duration("60") == 60
        assert parse_duration("300") == 300

    def test_whitespace_handling(self):
        assert parse_duration("  10s  ") == 10
        assert parse_duration(" 5m ") == 300

    def test_invalid_formats(self):
        with pytest.raises(ValueError):
            parse_duration("invalid")

        with pytest.raises(ValueError):
            parse_duration("")

        with pytest.raises(ValueError):
            parse_duration("5x")
