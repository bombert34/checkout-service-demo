"""Small durable request ledger; the rolling window survives process restarts."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


class Telemetry:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS requests (ts REAL NOT NULL, error INTEGER NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS request_time ON requests(ts)')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    def record(self, error, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            db.execute('INSERT INTO requests VALUES (?, ?)', (now, int(error)))
            db.execute('DELETE FROM requests WHERE ts < ?', (now - 3600,))

    @staticmethod
    def summarize(rows, now, window_s):
        events = [(ts, error) for ts, error in rows if now-window_s < ts <= now]
        count = len(events)
        errors = sum(error for _, error in events)
        age = round(now - events[-1][0], 2) if events else None
        span = round(events[-1][0] - events[0][0], 2) if events else 0
        rate = errors / count if count else 0.0
        active = age is not None and age <= 10
        return dict(window_s=window_s, requests=count, errors=errors,
                    error_rate=round(rate, 4), traffic_active=active,
                    observed_span_s=span, last_request_age_s=age,
                    recovery_ready=active and count >= 30 and span >= 30 and rate < .01)

    def metrics(self, window_s=60, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            rows = db.execute('SELECT ts,error FROM requests WHERE ts > ? AND ts <= ? ORDER BY ts',
                              (now-window_s, now)).fetchall()
        return self.summarize(rows, now, window_s)

    def history(self, now=None):
        now = time.time() if now is None else now
        with self.connect() as db:
            rows = db.execute('SELECT ts,error FROM requests WHERE ts > ? AND ts <= ? ORDER BY ts',
                              (now-180, now)).fetchall()
        points = []
        for offset in range(120, -1, -2):
            stamp = now-offset
            metric = self.summarize(rows, stamp, 60)
            points.append(dict(time=stamp*1000, value=metric['error_rate']*100
                               if metric['traffic_active'] else None))
        return points
