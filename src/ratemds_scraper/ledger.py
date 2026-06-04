"""
Resume ledger, append-only CSV sinks, and the anomaly log.

These are the small, thread-safe persistence primitives the scraper builds on:

* :class:`Ledger`  -- a JSONL set of completed unit keys, so an interrupted run
  resumes exactly where it stopped.
* :class:`CsvSink` -- an append-only, header-once CSV writer shared by all
  worker threads.
* :func:`anomaly`  -- record a data-quality note (e.g. a review-count mismatch)
  to ``state/anomalies.log`` without aborting the crawl.
"""

from __future__ import annotations

import os
import csv
import json
import time
import logging
import threading

from . import config

log = logging.getLogger("ratemds.ledger")


class Ledger:
    """Append-only set of completed unit keys, persisted to JSONL. Thread-safe."""

    def __init__(self, path: str):
        self.path = path
        self.done: set[str] = set()
        self._lock = threading.Lock()
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        try:
                            self.done.add(json.loads(line)["key"])
                        except Exception:
                            pass
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8")

    def has(self, key: str) -> bool:
        with self._lock:
            return key in self.done

    def mark(self, key: str):
        with self._lock:
            if key not in self.done:
                self.done.add(key)
                self._fh.write(json.dumps({"key": key, "t": time.time()}) + "\n")
                self._fh.flush()

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


class CsvSink:
    """Append-only CSV writer. Thread-safe so workers can share one sink."""

    def __init__(self, path: str, columns: list[str]):
        self.columns = columns
        self._lock = threading.Lock()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        new = not os.path.exists(path) or os.path.getsize(path) == 0
        self._fh = open(path, "a", newline="", encoding="utf-8")
        self._w = csv.DictWriter(self._fh, fieldnames=columns, extrasaction="ignore")
        if new:
            self._w.writeheader()
            self._fh.flush()

    def write(self, row: dict):
        with self._lock:
            self._w.writerow(row)

    def write_many(self, rows: list[dict]):
        with self._lock:
            for r in rows:
                self._w.writerow(r)

    def flush(self):
        with self._lock:
            self._fh.flush()

    def close(self):
        try:
            self._fh.close()
        except Exception:
            pass


_anomaly_lock = threading.Lock()


def anomaly(msg: str):
    """Append a data-quality note to the anomaly log and warn. Never raises."""
    with _anomaly_lock:
        os.makedirs(config.STATE_DIR, exist_ok=True)
        with open(config.ANOMALY_LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    log.warning("ANOMALY: %s", msg)
