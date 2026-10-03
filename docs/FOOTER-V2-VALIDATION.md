# Footer V2 / 历史用量：交付证据

日期：2026-10-03。版本：0.17.0。此报告区分离线测试、真实源码契约与线上验收。

## 已交付

- Hermes 专用 enhanced footer：两行摘要、默认折叠的本轮详情；classic 保持默认。
- Canonical usage 与 6 类外部协议字段解析（Chat、Responses、Anthropic、Gemini、Bedrock、Ollama）；提供商 ID 通用通路。生产读取 Hermes 规范化事件，不直接发起第三方推理请求。
- 去重、有界轮次状态、精确 ContextVar 身份、主请求/辅助隔离；不把会话累计 token 冒充本轮用量。
- 新请求参数信封 `request.body` 的思考参数读取；max 只是实际请求档位，不冒充服务端确认。
- 增强详情额外预留 6 个嵌套 CardKit 元素，经典模式维持原 2 个预留与分卡边界。
- 独立可选的 SQLite 历史账本，覆盖本进程实际 hook 的主/辅助请求；按月/日/范围、服务商/订阅标签/请求与返回模型查询，CLI/JSON。缺失、部分统计、已测数量明确。
- 226 条目录快照、45 个静态 Hermes provider profile 的来源文档、计划及架构设计图。

## 本地检查

| 检查 | 结果 |
| --- | --- |
| `ruff check hermes_lark_streaming tests` | PASS |
| `mypy --explicit-package-bases hermes_lark_streaming` | PASS，41 个源文件 |
| `pytest tests -q` | **1055 passed**，2 条 lark-oapi 弃用警告 |
| Footer 定向回归 | 288 passed，包含目录 ID/profile、字段解析、隔离/去重/渲染/容量 |
| 历史账本测试 | 14 项，含持久化、重复/重试、辅助缺失、零用量、时区/月界、只读查询、配置/脱敏、数据库锁 |
| `git diff --check` | PASS |
| 修改文件凭据模式扫描 | PASS；不读取或发布本机密钥文件 |

测试环境：独立 Python 3.12.14 环境，pytest 9.1.1、ruff 0.16.10、mypy 2.4.0、lark-oapi 1.7.3。生产 PM 环境没有安装开发依赖。

## 真实 Hermes 源码契约（离线，不是服务端 E2E）

官方源码快照 [`0ff4c748658ff5b92661fa2b453fcf8ed813414d`](https://github.com/NousResearch/hermes-agent/commit/0ff4c748658ff5b92661fa2b453fcf8ed813414d)：

- 独立 checkout 导入真实 `ApiRequestHooksMixin`，调用实际请求/响应 payload 与 canonical usage 构造函数。
- 合成输入 100、输出 7、缓存 70 的响应，验证只统计 100 输入而不是重复相加 170。
- 真实 `set_session_vars` / session-key ContextVar 接口契约通过；`request.body.extra_body.reasoning_effort=max` 被正确读取。
- 没有启动 Gateway、访问付费模型 API 或给飞书发测试消息。
- 上述全量回归包括 Hermes 补丁兼容/往返、已有卡片重试/附件/打断/排队路径。

GitHub push 后的 Python 3.11/3.12/3.13 与 Hermes 版本矩阵以对应提交 Actions 结果为准；本地通过不替代远端 CI。

## 待部署后验收

- 飞书真实客户端显示、折叠详情、实际长任务换卡、真实主/辅助模型 usage 对账。
- 多个账号轮换的独立账单归属：当前 hook 缺少可靠账号 ID，按服务商/标签统计。
- LCM 压缩提交成功事件、账号额度/计费、实时统计控制面板：未纳入本版实现，不猜测数值。
- 旧月份回填需审核已有可靠 usage 来源；本版从启用后开始记录，不解析旧聊天内容估算。
- 新账本在线备份应使用 SQLite backup API；本轮未变更用户定时备份任务。

此次只维护源码和 GitHub，未替换 Hermes 托管插件，未修改生产配置，未重启 Gateway。开关与查询见 [USAGE-HISTORY.md](USAGE-HISTORY.md)；部署按 [INSTALL.md](../INSTALL.md) 独立执行。
