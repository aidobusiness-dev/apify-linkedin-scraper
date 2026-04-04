"""Shared utilities."""

from __future__ import annotations

import random
import time


def sleep_random(lo: float, hi: float) -> None:
    """Sleep for a random duration between lo and hi seconds."""
    time.sleep(random.uniform(lo, hi))
