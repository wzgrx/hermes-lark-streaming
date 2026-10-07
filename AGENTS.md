# AGENTS.md

Hermes Gateway 插件:把飞书回复渲染为持续更新的 CardKit 2.0 卡片。架构见 docs/ARCHITECTURE.md,设计稿见 docs/design/card-redesign.html。

## 命令

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check hermes_lark_streaming tests
.venv/bin/mypy --explicit-package-bases hermes_lark_streaming
```

## 约定

- 依赖方向:`hooks → session → card | details | transport`,下层不得 import 上层;`card` 无 I/O,不碰 Hermes 类型。
- 只有 `hooks/` 接触 Hermes 源码;注入片段语义以 `hooks/snippets.py` 为准,锚点变更后先跑 `tests/hooks/test_real_hermes.py`。
- 一张卡片同一时间一个写入者:写入都经过 `CardChannel.write`,序号成功后才提交。
- 进入卡片的外部文本先 `redact` 再 `esc`;缺失数据不显示、不补造(渲染层过滤值为“未知”的指标与备注)。
- 面向用户的字符串用中文,双语文案用 `Bi(zh, en)`。
- 提交信息正文用无序列表。
- 真实验收:`scripts/live_acceptance.py <chat_id>` 走一轮模拟对话(`--ok` 为全部成功),再用 `scripts/windows/feishu_shot.ps1` 从 WSL 截取飞书桌面端,看卡片的真实渲染。
