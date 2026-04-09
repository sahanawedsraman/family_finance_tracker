"""Unit tests for the retry utility with exponential backoff."""

from unittest.mock import MagicMock, patch

import pytest

from src.retry import retry_api_call


class TestRetryApiCall:
    """Tests for retry_api_call()."""

    def test_success_on_first_attempt(self):
        func = MagicMock(return_value="ok")
        result = retry_api_call(func)
        assert result == "ok"
        assert func.call_count == 1

    def test_returns_value_from_func(self):
        func = MagicMock(return_value={"spreadsheetId": "abc123"})
        result = retry_api_call(func)
        assert result == {"spreadsheetId": "abc123"}

    @patch("src.retry.time.sleep")
    def test_success_on_second_attempt(self, mock_sleep):
        func = MagicMock(side_effect=[Exception("fail"), "ok"])
        result = retry_api_call(func, max_retries=3, base_delay=1.0)
        assert result == "ok"
        assert func.call_count == 2
        mock_sleep.assert_called_once_with(1.0)  # base_delay * 2^0

    @patch("src.retry.time.sleep")
    def test_success_on_third_attempt(self, mock_sleep):
        func = MagicMock(side_effect=[Exception("fail1"), Exception("fail2"), "ok"])
        result = retry_api_call(func, max_retries=3, base_delay=1.0)
        assert result == "ok"
        assert func.call_count == 3
        assert mock_sleep.call_count == 2
        mock_sleep.assert_any_call(1.0)  # 1.0 * 2^0
        mock_sleep.assert_any_call(2.0)  # 1.0 * 2^1

    @patch("src.retry.time.sleep")
    def test_raises_after_max_retries_exceeded(self, mock_sleep):
        func = MagicMock(side_effect=Exception("persistent failure"))
        with pytest.raises(Exception, match="persistent failure"):
            retry_api_call(func, max_retries=3, base_delay=1.0)
        assert func.call_count == 3
        assert mock_sleep.call_count == 2  # sleeps between attempts, not after last

    @patch("src.retry.time.sleep")
    def test_exponential_backoff_delays(self, mock_sleep):
        func = MagicMock(
            side_effect=[Exception("e1"), Exception("e2"), Exception("e3"), "ok"]
        )
        result = retry_api_call(func, max_retries=4, base_delay=0.5)
        assert result == "ok"
        assert mock_sleep.call_args_list[0][0][0] == 0.5   # 0.5 * 2^0
        assert mock_sleep.call_args_list[1][0][0] == 1.0   # 0.5 * 2^1
        assert mock_sleep.call_args_list[2][0][0] == 2.0   # 0.5 * 2^2

    @patch("src.retry.time.sleep")
    def test_single_retry_max(self, mock_sleep):
        func = MagicMock(side_effect=Exception("fail"))
        with pytest.raises(Exception, match="fail"):
            retry_api_call(func, max_retries=1, base_delay=1.0)
        assert func.call_count == 1
        mock_sleep.assert_not_called()  # no sleep when only 1 attempt

    @patch("src.retry.time.sleep")
    def test_preserves_original_exception_type(self, mock_sleep):
        func = MagicMock(side_effect=ConnectionError("network down"))
        with pytest.raises(ConnectionError, match="network down"):
            retry_api_call(func, max_retries=2, base_delay=0.1)

    @patch("src.retry.time.sleep")
    def test_logs_warnings_on_retry(self, mock_sleep):
        func = MagicMock(side_effect=[Exception("oops"), "ok"])
        with patch("src.retry.logger") as mock_logger:
            retry_api_call(func, max_retries=2, base_delay=1.0)
            mock_logger.warning.assert_called_once()
            assert "attempt 1/2" in mock_logger.warning.call_args[0][0] % mock_logger.warning.call_args[0][1:]

    @patch("src.retry.time.sleep")
    def test_logs_error_on_final_failure(self, mock_sleep):
        func = MagicMock(side_effect=Exception("fatal"))
        with patch("src.retry.logger") as mock_logger:
            with pytest.raises(Exception):
                retry_api_call(func, max_retries=2, base_delay=1.0)
            mock_logger.error.assert_called_once()
