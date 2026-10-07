"""Generate public, synthetic V1 JSON from the actual renderer. No provider calls."""

# ruff: noqa: RUF001

from __future__ import annotations

import json
from pathlib import Path

from hermes_lark_streaming.card_limits import inspect_card
from hermes_lark_streaming.cardkit.builder import build_complete_card, build_streaming_card_v2
from hermes_lark_streaming.streaming.segments import SegmentState

steps = []
groups = [(3, "检查版本与依赖", "version", 3400), (8, "等待安装进程", "install", 240000),
          (1, "安装依赖", "failed", 2300), (8, "修复后重试与验证", "retry", 45000),
          (4, "检查服务与版本", "service", 10300)]
for copies, title, detail, elapsed in groups:
    for _ in range(copies):
        failed = detail == "failed"
        steps.append(dict(name="command" if failed else "process_poll", title=title,
                          status="error" if failed else "success", detail=f"EXAMPLE_{detail}",
                          elapsed_ms=elapsed / copies, error="Exit code 1" if failed else "", output="",
                          icon="setting_outlined", result_block=None, error_block=None))
data = dict(presentation="reference", model="deepseek-v4.1-flash", requested_model="deepseek-v4.1-flash",
            response_model="deepseek-v4.1-flash", provider="opencode-go", api_mode="chat_completions",
            reasoning="max", input_tokens=143800, output_tokens=3900, cache_read_tokens=69600,
            context_used=70300, context_max=1000000, first_response=2.1, duration=301,
            api_calls=3, retries=0, tool_calls=24, reference={
                "steps": steps, "show_tools": True, "failed_total": 1, "succeeded_total": 23,
                "resources_enabled": True, "agent_name": "龙虾3号", "host": {
                    "scope": "WSL", "gpu_percent": 1, "gpu_temperature": 66, "gpu_used_gib": 10.5,
                    "gpu_total_gib": 24, "cpu_percent": 17, "ram_used_gib": 15.5, "ram_total_gib": 126,
                    "processes": 75, "uptime": 316800, "sampled_at": "2026-10-03 16:07:18 +0800",
                }, "history": {"status": "ok", "timezone": "Asia/Shanghai", "since": "2026-07-01",
                    "today": {"tokens": 1200000}, "month": {"tokens": 36800000}, "total": {"tokens": 416900000},
                    "show_models": False, "models": [{"subscription": "OpenCode Go", "model": "DeepSeek V4.1 Flash",
                                                       "tokens": 350600000},
                                                      {"subscription": "本地 llama.cpp", "model": "Qwen Next",
                                                       "tokens": 48100000},
                                                      {"subscription": "SiliconFlow", "model": "DeepSeek V4.1 Flash",
                                                       "tokens": 18200000}],
                },
            })
state = SegmentState()
state.on_tool_event(24)
state.on_answer_delta("**处理完成。**\n\n依赖与服务检查完成，回答正文保持独立。\n\n"
                      "1 次工具失败已列在上方，不混同为最终回答失败。\n\n"
                      "*这是 V1 布局验收的合成示例，不是实际执行结果。*")
state.finalize_segments(24)
root = Path(__file__).resolve().parents[1] / "docs/assets"
cards = {
    "reference-v1-completed": build_complete_card(segments=state.segments, all_tool_steps=steps,
                                                  footer_data=data, footer_mode="enhanced"),
    "reference-v1-running": build_streaming_card_v2(footer_data={**data, "runtime_phase": "tool"},
                                                   show_tool_use=False, show_streaming_element=False),
}
data["reference"]["history"]["show_models"] = True
cards["reference-v1-history"] = build_complete_card(segments=state.segments, all_tool_steps=steps,
                                                   footer_data=data, footer_mode="enhanced")
inspection = {}
for name, card in cards.items():
    check = inspect_card(card)
    assert check.safe, check
    (root / f"{name}.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    inspection[name] = {"elements": check.elements, "json_bytes": check.json_bytes,
                        "synthetic": True, "validation_scope": "offline_structure_only"}
(root / "reference-v1-inspection.json").write_text(json.dumps(inspection, indent=2) + "\n", encoding="utf-8")
print(json.dumps(inspection))
