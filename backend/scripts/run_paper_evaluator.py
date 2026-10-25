"""Run one serial polling worker for approved paper agents."""
import argparse
import json
import logging
import os
import random
import signal
import time
from datetime import datetime, timezone

from app.db import SessionLocal
from app.paper_store import PaperLedgerStore
from app.signal_evaluator import PaperSignalEvaluator


logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("tradegen.paper_evaluator")
stopping = False


def retry_delay(base_seconds: int, consecutive_failures: int, jitter: float | None = None) -> float:
    """Bounded exponential retry delay with injectable jitter for deterministic checks."""
    ceiling = min(300, max(2, base_seconds) * (2 ** min(max(consecutive_failures - 1, 0), 8)))
    factor = random.uniform(0.8, 1.2) if jitter is None else jitter
    return max(1.0, min(300.0, ceiling * factor))


def stop_worker(*_):
    global stopping
    stopping = True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true", help="Evaluate each approved paper agent once and exit")
    args = parser.parse_args()
    signal.signal(signal.SIGTERM, stop_worker)
    signal.signal(signal.SIGINT, stop_worker)
    interval = min(max(int(os.getenv("EVALUATOR_INTERVAL_SECONDS", "10")), 2), 300)
    evaluator = PaperSignalEvaluator(PaperLedgerStore(SessionLocal))
    consecutive_failures = 0
    while not stopping:
        try:
            summary = evaluator.run_once()
            logger.info("paper_evaluation %s", json.dumps({"at": datetime.now(timezone.utc).isoformat(), **summary}))
            if summary.get("errors", 0):
                consecutive_failures += 1
                delay = retry_delay(interval, consecutive_failures)
            else:
                consecutive_failures = 0
                delay = interval
        except Exception:
            logger.exception("Paper signal evaluation cycle failed")
            consecutive_failures += 1
            delay = retry_delay(interval, consecutive_failures)
        if args.once:
            return
        deadline = time.monotonic() + delay
        while not stopping and time.monotonic() < deadline:
            time.sleep(min(1, deadline - time.monotonic()))


if __name__ == "__main__":
    main()
