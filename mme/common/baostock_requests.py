"""Project-wide BaoStock query accounting."""

from __future__ import annotations

import csv
import os
from datetime import date
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None


DEFAULT_BAOSTOCK_REQUEST_LOG = Path('data/state/baostock/requests.csv')
REQUEST_COLUMNS = ('request_date', 'endpoint', 'code', 'adjustflag')


def reserve_baostock_request(
    endpoint: str,
    max_requests_per_day: int,
    code: str = '',
    adjustflag: str = '',
    request_log: Path = DEFAULT_BAOSTOCK_REQUEST_LOG,
    request_date: date | None = None,
) -> int:
    """Record one BaoStock query and return its daily sequence number.

    The shared log contains only the current calendar day's rows. The number of
    rows is the authoritative request count, so a stale log is cleared before a
    new query is reserved.
    """
    if max_requests_per_day <= 0:
        raise ValueError('max_requests_per_day must be positive')
    today = (request_date or date.today()).isoformat()
    request_log.parent.mkdir(parents=True, exist_ok=True)
    with request_log.open('a+', newline='', encoding='utf-8') as handle:
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            handle.seek(0)
            reader = csv.DictReader(handle)
            rows = list(reader)
            if reader.fieldnames and tuple(reader.fieldnames) != REQUEST_COLUMNS:
                raise ValueError(f'invalid BaoStock request log: {request_log}')
            write_header = reader.fieldnames is None
            if any(row['request_date'] != today for row in rows):
                rows = []
                write_header = True
                handle.seek(0)
                handle.truncate()
            if len(rows) >= max_requests_per_day:
                raise RuntimeError(
                    f'BaoStock daily request limit reached: {len(rows)}/{max_requests_per_day}'
                )
            handle.seek(0, os.SEEK_END)
            writer = csv.DictWriter(handle, fieldnames=REQUEST_COLUMNS)
            if write_header:
                writer.writeheader()
            writer.writerow(
                {'request_date': today, 'endpoint': endpoint, 'code': code, 'adjustflag': adjustflag}
            )
            handle.flush()
            os.fsync(handle.fileno())
            return len(rows) + 1
        finally:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
