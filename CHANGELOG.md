# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.1.1] - 2026-05-08

### 新增

- 新增 `AGENTS.md`，包含架构概览与开发指南。

### 变更

- 精简 `optimize_markdown_style`，移除不必要的 `<br>` 间距逻辑（连续标题、表格、代码块前后填充）。空行压缩已足够适配 CardKit 渲染。
- 移除 5 个模块中的冗余代码。

### Added

- Add `AGENTS.md` with architecture overview and development guide.

### Changed

- Simplify `optimize_markdown_style` by removing unnecessary `<br>` spacing logic (consecutive headers, tables, code-block padding). Blank-line compression is sufficient for CardKit rendering.
- Remove redundant code across 5 modules.

---

## [0.1.0] - 2026-05-08

### 新增

- `hermes-lark-streaming` 初始版本 — 基于飞书 CardKit v2.0 的 Hermes Gateway 实时流式卡片插件。
- 通过 CardKit `streaming_mode` 实现打字机效果的流式输出。
- 可折叠面板展示推理/思考过程。
- 实时工具调用状态追踪，含图标、结果块和错误块。
- CardKit 流式失败或频控时自动降级到 IM PATCH。
- 完成态卡片，页脚展示元数据（耗时、模型、token 用量、上下文窗口）。
- `UnavailableGuard` — 源消息被删除或撤回时自动终止后续更新。
- `ImageResolver` — 异步识别 markdown 图片 URL，下载并上传为飞书 `img_key`。
- AST 注入 6 个 hook 到 `gateway/run.py`（`on_message_started`、`on_answer_delta`、`on_thinking_delta`、`on_tool_updated`、`on_message_completed`、`on_message_aborted`）。
- CLI 命令：`install`、`uninstall`、`verify`、`status`、`restore`。

### 变更

- 在 README 中明确说明插件必须安装到 Hermes 自身的 Python 虚拟环境中（`~/.hermes/hermes-agent/venv/bin/python3`），而非系统 Python。避免 gateway 启动后因找不到插件而失败。

### 修复

- 移除 `strip_reasoning_tags()` 末尾的 `.strip()`，保留换行符以支持 CardKit 流式渲染。Markdown 格式（加粗、代码块、表格、列表）现在在流式阶段即可正确渲染，不再仅在全量更新后正常显示。

### Added

- Initial release of `hermes-lark-streaming` — a real-time streaming card plugin for Hermes Gateway via Feishu/Lark CardKit v2.0.
- Streaming output with typewriter effect via CardKit `streaming_mode`.
- Reasoning/thinking display in collapsible panels.
- Live tool-use status tracking with icons, result blocks, and error blocks.
- Auto fallback from CardKit streaming to IM PATCH on creation failure or rate limiting.
- Completion card with footer metadata (duration, model, tokens, context usage).
- `UnavailableGuard` — auto-terminates updates when the source message is deleted or recalled.
- `ImageResolver` — asynchronously detects markdown image URLs, downloads, uploads to Feishu, and replaces with `img_key`.
- AST injection of 6 hooks into `gateway/run.py` (`on_message_started`, `on_answer_delta`, `on_thinking_delta`, `on_tool_updated`, `on_message_completed`, `on_message_aborted`).
- CLI commands: `install`, `uninstall`, `verify`, `status`, `restore`.

### Changed

- Clarify in README that the plugin must be installed into Hermes's own Python venv (`~/.hermes/hermes-agent/venv/bin/python3`), not the system Python. This prevents the gateway from failing to load the plugin at runtime.

### Fixed

- Remove trailing `.strip()` in `strip_reasoning_tags()` to preserve newlines for CardKit streaming. Markdown formatting (bold, code blocks, tables, lists) now renders correctly during the streaming phase, not just after completion.
