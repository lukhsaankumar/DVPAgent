from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from time import monotonic
from typing import Iterator
import logging

logger = logging.getLogger("dvp_meeting_prep.salesforce")


@dataclass
class StepTimer:
    name: str
    start: float
    duration_seconds: float = 0.0


@contextmanager
def timed_step(name: str, *, echo: bool = False) -> Iterator[StepTimer]:
    """Time a named extraction step and log its duration on exit (even on error).

    `echo=True` also prints a concise milestone line, for visibility when
    logging is redirected or set to a quiet level.
    """
    timer = StepTimer(name=name, start=monotonic())
    logger.debug("[%s] starting", name)
    if echo:
        print(f"[{name}] starting")
    try:
        yield timer
    finally:
        timer.duration_seconds = monotonic() - timer.start
        logger.info("[%s] completed in %.2fs", name, timer.duration_seconds)
