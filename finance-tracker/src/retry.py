"""Retry utility with exponential backoff for Google API calls."""

import logging
import re
import time

logger = logging.getLogger(__name__)


def _extract_retry_delay(error_msg: str) -> float | None:
    """Extract retry delay from a 429 rate limit error message."""
    match = re.search(r"retry in ([\d.]+)s", str(error_msg), re.IGNORECASE)
    if match:
        return float(match.group(1))
    return None


def retry_api_call(func, max_retries=3, base_delay=1.0):
    """Execute a function with exponential backoff retry logic.

    Handles 429 rate limit errors by respecting the server's suggested retry delay.

    Args:
        func: Callable to execute (typically a Google API call).
        max_retries: Maximum number of attempts (default 3).
        base_delay: Base delay in seconds between retries (default 1.0).
            Actual delay doubles each attempt: base_delay * 2^attempt.

    Returns:
        The return value of func() on success.

    Raises:
        The last exception if all retries are exhausted.
    """
    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            if attempt == max_retries - 1:
                logger.error(
                    "API call failed after %d attempts: %s", max_retries, e
                )
                raise

            # Check for rate limit with suggested retry delay
            error_str = str(e)
            retry_delay = _extract_retry_delay(error_str)
            is_rate_limit = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str

            if is_rate_limit and retry_delay:
                delay = retry_delay + 5  # add buffer
            else:
                delay = base_delay * (2 ** attempt)

            logger.warning(
                "API call failed (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                max_retries,
                delay,
                e,
            )
            time.sleep(delay)
