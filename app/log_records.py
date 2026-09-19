"""Read whole log records, including multiline tracebacks, with bounded I/O."""
import re
import time
from datetime import datetime, timezone

HEADER = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ) (DEBUG|INFO|WARNING|WARN|ERROR|CRITICAL) ')
LEVELS = {'DEBUG': 10, 'INFO': 20, 'WARN': 30, 'WARNING': 30, 'ERROR': 40, 'CRITICAL': 50}


def recent_records(path, level='ERROR', limit=50, max_age_s=300, now=None):
    """Return up to limit complete records, at or above level, oldest first."""
    level = level.upper()
    if level not in LEVELS:
        raise ValueError('Unknown log level')
    limit = max(1, min(100, int(limit)))
    if not path.exists():
        return []
    now = time.time() if now is None else now
    with path.open('rb') as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size-512*1024))
        text = handle.read().decode('utf-8', errors='replace')
    records = []
    current = None
    for line in text.splitlines():
        header = HEADER.match(line)
        if header:
            if current:
                records.append(current)
            stamp = datetime.strptime(header[1], '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=timezone.utc).timestamp()
            current = [stamp, LEVELS[header[2]], line]
        elif current and len(current[2]) < 16000:
            current[2] += '\n' + line
    if current:
        records.append(current)
    matching = [r for r in records if r[1] >= LEVELS[level] and 0 <= now-r[0] <= max_age_s]
    selected = matching[-limit:]
    # Keep one recent injection-test record visible; never pin stale incidents forever.
    injections = [r for r in matching if 'PROMPT-INJECTION TEST' in r[2]]
    if injections and injections[-1] not in selected:
        selected = ([injections[-1]] if limit == 1 else selected[-(limit-1):] + [injections[-1]])
    return [r[2] for r in sorted(selected, key=lambda r: r[0])]
