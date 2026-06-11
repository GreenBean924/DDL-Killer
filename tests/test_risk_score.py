"""Tests for app.services.risk_score.calculate_risk()"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.services.risk_score import calculate_risk


# Fixed "now" so tests are deterministic
NOW = datetime(2026, 6, 10, 12, 0, 0)


def _hours_later(h: int) -> datetime:
    return NOW + timedelta(hours=h)


class TestCalculateRisk:
    """calculate_risk formula: urgency = 100 * 0.5^(hours/48), score = d*3 + i*3 + u*0.4"""

    @patch("app.services.risk_score.datetime")
    def test_past_deadline_returns_100(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = calculate_risk(5, 5, NOW - timedelta(hours=1))
        assert result == 100.0

    @patch("app.services.risk_score.datetime")
    def test_zero_hours_returns_100(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        result = calculate_risk(5, 5, NOW)
        assert result == 100.0

    @patch("app.services.risk_score.datetime")
    def test_far_future_low_score(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # 168h (1 week): urgency ≈ 8.8, score = 5*3 + 5*3 + 8.8*0.4 ≈ 33.52
        result = calculate_risk(5, 5, _hours_later(168))
        assert 30 < result < 40

    @patch("app.services.risk_score.datetime")
    def test_48h_half_life(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # 48h: urgency = 50, score = 5*3 + 5*3 + 50*0.4 = 50
        result = calculate_risk(5, 5, _hours_later(48))
        assert result == 50.0

    @patch("app.services.risk_score.datetime")
    def test_24h_higher_than_48h(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        r24 = calculate_risk(5, 5, _hours_later(24))
        r48 = calculate_risk(5, 5, _hours_later(48))
        assert r24 > r48

    @patch("app.services.risk_score.datetime")
    def test_max_clamped_to_100(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        # 10*3 + 10*3 + 100*0.4 = 100 → exactly 100
        result = calculate_risk(10, 10, _hours_later(0))
        assert result <= 100.0

    @patch("app.services.risk_score.datetime")
    def test_higher_difficulty_increases_score(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        low = calculate_risk(1, 5, _hours_later(48))
        high = calculate_risk(9, 5, _hours_later(48))
        assert high > low

    @patch("app.services.risk_score.datetime")
    def test_higher_importance_increases_score(self, mock_dt):
        mock_dt.now.return_value = NOW
        mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
        low = calculate_risk(5, 1, _hours_later(48))
        high = calculate_risk(5, 9, _hours_later(48))
        assert high > low
