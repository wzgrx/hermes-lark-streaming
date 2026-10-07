"""``history`` command: read the local ledger, no provider API calls."""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .config import default_history_path
from .ledger import GROUPS, UsageLedger


def run(args: list[str], path: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read local Hermes usage history (no provider API calls)")
    parser.add_argument("--month", help="YYYY-MM in the selected timezone")
    parser.add_argument("--from", dest="start", help="Inclusive YYYY-MM-DD")
    parser.add_argument("--to", dest="end", help="Exclusive YYYY-MM-DD")
    parser.add_argument("--timezone", default="UTC")
    parser.add_argument("--scope", choices=["all", "main", "auxiliary"], default="all")
    parser.add_argument("--group-by", choices=list(GROUPS), default="provider-model")
    parser.add_argument("--json", action="store_true")
    opts = parser.parse_args(args)
    try:
        tz = ZoneInfo(opts.timezone)
        start = datetime.strptime(opts.start, "%Y-%m-%d").replace(tzinfo=tz).timestamp() if opts.start else None
        end = datetime.strptime(opts.end, "%Y-%m-%d").replace(tzinfo=tz).timestamp() if opts.end else None
        if opts.month:
            if opts.start or opts.end:
                raise ValueError("Use --month or --from/--to, not both")
            dt = datetime.strptime(opts.month, "%Y-%m").replace(tzinfo=tz)
            start = dt.timestamp()
            end = dt.replace(year=dt.year + (dt.month == 12), month=dt.month % 12 + 1).timestamp()
        report = UsageLedger(path or default_history_path()).report(
            start=start, end=end, group_by=opts.group_by, timezone=opts.timezone, scope=opts.scope
        )
    except (ValueError, ZoneInfoNotFoundError, OSError, sqlite3.Error):
        print("History query failed: check dates, timezone and ledger health.")
        return 2
    if opts.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    print(f"History: {report['status']} | {opts.timezone} | {opts.scope} | {opts.group_by}")
    print("Group | attempts | measured | input | output | total (known complete requests)")
    for row in report["groups"] + [{"key": ["TOTAL"], **report["totals"]}]:
        cells = [
            str(row[k]) if row[k] is not None else "unknown"
            for k in ("requests", "measured_requests", "input_tokens", "output_tokens", "total_tokens")
        ]
        print(" | ".join([" / ".join(row["key"]), *cells]))
    print(report["note"])
    return 0
