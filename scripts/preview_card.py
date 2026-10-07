"""Send a sample finished card to a chat, with the details panel open, to check layout at a given width.

    .venv/bin/python scripts/preview_card.py <chat_id> [--width compact|default|fill]

The card is built from fixed sample data through the real renderer; it does not touch the gateway.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from live_acceptance import load_env

from hermes_lark_streaming.card.model import (
    Block,
    BlockKind,
    Footer,
    Metric,
    Phase,
    RenderOptions,
    Section,
    Step,
    StepStatus,
    TurnView,
)
from hermes_lark_streaming.card.render import render_final

STEPS = (
    Step("Terminal", "printf 'OK\\n'", StepStatus.OK, 192, icon="setting_outlined"),
    Step("Terminal", "sh -c 'exit 7'", StepStatus.FAILED, 37, "Exit code 7\nV1_EXPECTED_FAILURE", "setting_outlined"),
)
SECTIONS = (
    Section("usage", "用量", (
        Metric("输入", "403.7k", group="本轮"), Metric("输出", "1.2k", group="本轮"),
        Metric("缓存", "99.9%", 0.999, group="本轮"), Metric("首响应", "6.2s", group="本轮"),
        Metric("费用", "¥0.13", group="本轮"),
        Metric("今日", "3.63M", group="累计"), Metric("本月", "228.4M", group="累计"),
        Metric("总计", "228.4M", group="累计"),
    )),
    Section("resources", "资源 · WSL", (
        Metric("CPU", "36.8%"), Metric("GPU", "2%·73°C"), Metric("显存", "2.8/24G"), Metric("内存", "35/126G"),
        Metric("磁盘", "1.1/1.5T"),
    )),
    Section("accounts", "OpenCode Go · 订阅与额度", (
        Metric("5小时", "1%", 0.01, "3h02m 后重置"), Metric("每周", "8%", 0.08, "10-12 08:00 重置"),
        Metric("每月", "51%", 0.51, "10-20 14:26 重置"),
    ), notes=("Go 主账户", "快照 10-08 00:38"), layout="bars"),
)


def sample() -> TurnView:
    return TurnView(
        phase=Phase.DONE, elapsed_s=141.0, card_key="preview", steps=STEPS,
        blocks=(
            Block(BlockKind.THOUGHT, "b0", "先执行成功命令,再执行预期失败的命令。", elapsed_s=1.5),
            Block(BlockKind.TOOLS, "b1", steps=STEPS, elapsed_s=8.5),
            Block(BlockKind.ANSWER, "b2", "验收完成。第二个命令按预期以退出码 7 结束。"),
        ),
        answers=("验收完成。",),
        footer=Footer(model="DeepSeek V4.1 Flash", context_used=403_700, context_max=1_000_000, cache_hit=1.0,
                      reasoning="max"),
        sections=SECTIONS,
    )


async def main(chat_id: str, width: str) -> None:
    load_env(Path.home() / ".hermes")
    from hermes_lark_streaming.transport import CardKitClient, ClientConfig

    card = render_final(sample(), RenderOptions(width_mode=width))
    for element in card["body"]["elements"]:
        if str(element.get("element_id", "")).startswith("details"):
            element["expanded"] = True  # preview only: show the panel's layout
    client = CardKitClient(ClientConfig(os.environ["FEISHU_APP_ID"], os.environ["FEISHU_APP_SECRET"],
                                        "https://open.feishu.cn"))
    await client.send_card(chat_id, card)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("chat_id")
    parser.add_argument("--width", default="compact", choices=("compact", "default", "fill"))
    args = parser.parse_args()
    asyncio.run(main(args.chat_id, args.width))
