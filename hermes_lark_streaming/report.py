"""``report``: a usage card for one chat, built from the local usage ledger and the account readers.

Run by a timer, not by the agent, so it costs no model quota::

    hermes --run-module hermes_lark_streaming report --chat oc_xxx [--period day|week] [--dry-run]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from .card.model import Metric, Phase, RenderOptions, Section, TurnView
from .card.render import details_element, grey
from .config import ConfigSource
from .details import DetailsCollector, DetailsConfig
from .details.ledger import UsageLedger
from .details.providers import short_name
from .details.sections.fmt import compact

_WEEKDAYS = "一二三四五六日"


def _period_start(now: datetime, period: str) -> datetime:
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return day - timedelta(days=day.weekday()) if period == "week" else day


def _usage_metrics(report: dict[str, Any], group: str) -> list[Metric]:
    """One cell per subscription (tokens) plus the request count, largest first."""
    rows = sorted(report.get("groups") or [], key=lambda g: -(g.get("total_tokens") or 0))
    metrics = [
        Metric(short_name(str(g["key"][0])), compact(g.get("total_tokens"), lower_bound=bool(g.get("usage_partial"))),
               group=group)
        for g in rows[:4] if g.get("total_tokens")
    ]
    totals = report.get("totals") or {}
    requests = totals.get("requests") or 0
    metrics.append(Metric("请求", f"{requests:,}" if requests < 10_000 else compact(requests), group=group))
    return metrics


def usage_section(ledger: UsageLedger, tz: str, period: str, now: datetime) -> Section:
    start = _period_start(now, period)
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    head = "本周" if period == "week" else "今日"
    first = ledger.report(start=start.timestamp(), end=now.timestamp(), group_by="provider", timezone=tz)
    whole = ledger.report(start=month.timestamp(), end=now.timestamp(), group_by="provider", timezone=tz)
    return Section("usage", "用量", (*_usage_metrics(first, head), *_usage_metrics(whole, "本月")))


async def account_sections(config: DetailsConfig, chat_id: str) -> list[Section]:
    """The quota sections of every configured subscription, read the same way the cards read them."""
    collector = DetailsCollector(config)
    providers = list(dict.fromkeys(
        str(row.get("provider")) for row in config.account_settings().get("accounts") or [] if isinstance(row, dict)
    ))
    out: list[Section] = []
    for provider in providers:
        collector.request(chat_id=chat_id, provider=provider)
        for _ in range(40):  # up to ~8s for the HTTP reads
            await asyncio.sleep(0.2)
            await collector.finish(chat_id, provider)
            found = [s for s in collector.sections({}, chat_id=chat_id, provider=provider, terminal=True)
                     if s.key == "accounts"]
            if found and not any("未就绪" in n for n in found[0].notes):
                break
        out += found
    return out


def build_card(sections: list[Section], *, period: str, now: datetime) -> dict[str, Any]:
    title = "用量周报" if period == "week" else "用量日报"
    stamp = f"{now:%m-%d}(周{_WEEKDAYS[now.weekday()]})"
    view = TurnView(phase=Phase.DONE, card_key=f"rp{now:%m%d}", sections=tuple(sections))
    panel = details_element(view, RenderOptions())
    elements: list[dict[str, Any]] = [{"tag": "markdown", "content": f"**📊 {title} · {stamp}**",
                                       "text_size": "normal_v2"}]
    if panel is not None:
        panel["expanded"] = True
        panel["header"]["title"]["content"] = grey("用量 · 额度")
        panel["header"]["title"].pop("i18n_content", None)
        elements.append(panel)
    elements.append({"tag": "markdown", "content": grey(f"自动发送 · 数据来自本地用量记录 · {now:%H:%M}"),
                     "text_size": "notation"})
    return {"schema": "2.0", "config": {"update_multi": True, "summary": {"content": f"{title} {stamp}"}},
            "body": {"elements": elements}}


async def run(chat_id: str, period: str, *, dry_run: bool = False) -> int:
    from .hooks.cli import load_hermes_environment

    load_hermes_environment()  # Feishu credentials and account keys live in the profile's .env
    source = ConfigSource()
    config = DetailsConfig.from_mapping(source.live_details())
    tz = config.zone
    now = datetime.now(ZoneInfo(tz))
    sections = [usage_section(UsageLedger(config.ledger_path()), tz, period, now)]
    sections += await account_sections(config, chat_id)
    card = build_card(sections, period=period, now=now)
    if dry_run:
        print(json.dumps(card, ensure_ascii=False, indent=1))
        return 0
    from .session.controller import Controller

    controller = Controller(source.home)
    client = await controller._client_for(chat_id)
    message_id = await client.send_card(chat_id, card)
    print(f"sent {message_id} at {time.strftime('%H:%M:%S')}")
    return 0


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(prog="hermes_lark_streaming report")
    parser.add_argument("--chat", required=True)
    parser.add_argument("--period", choices=("day", "week"), default="day")
    parser.add_argument("--dry-run", action="store_true")
    opts = parser.parse_args(list(argv))
    return asyncio.run(run(opts.chat, opts.period, dry_run=opts.dry_run))
