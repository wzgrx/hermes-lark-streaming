# 上游 Issue / PR 审计（2026-09-20）

对象：[`Cheerwhy/hermes-lark-streaming`](https://github.com/Cheerwhy/hermes-lark-streaming)。本表记录当日仍开放的项目以及本 fork 中的可验证对应实现。

## Open Issues

| 上游 | 本 fork 状态 | 实现/验证 |
|---|---|---|
| [#111 cron MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/issues/111) | 已覆盖 | 模块化 cron hook 透传 `media_files`，图片与文件按类型投递，有去重测试。 |
| [#109 流式 MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/issues/109) | 已覆盖 | 保留原始 answer delta，follow-up/失败路径由插件补投，正文移除内部指令。 |
| [#106 clarify 签名](https://github.com/Cheerwhy/hermes-lark-streaming/issues/106) | 已覆盖 | `functools.wraps` + `*args/**kwargs`，适配 2/3 参和 batch clarify。 |
| [#105 模块化 Gateway](https://github.com/Cheerwhy/hermes-lark-streaming/issues/105) | 已覆盖 | `ModularPatcher` 跨 `run_inbound/run_turn/run_turn_runner/run_busy`，预编译、原子发布和回滚。 |
| [#98 CardKit 300313](https://github.com/Cheerwhy/hermes-lark-streaming/issues/98) | 已加强 | 元素流更新 200/400/800ms 有界重试；batch 缺失元素回滚计数与 created 快照，通过 FlushController 互斥队列立即重刷。 |
| [#82 其他消息类型](https://github.com/Cheerwhy/hermes-lark-streaming/issues/82) | 已覆盖核心场景 | 后台复盘合并进最终卡片；审批保留 Hermes 原生按钮/回调，插件负责暂停旧流并在工具结束后换卡继续。 |

## Open PRs

| 上游 | 本 fork 对应 |
|---|---|
| [#102 主文本回调](https://github.com/Cheerwhy/hermes-lark-streaming/pull/102) | AST 按 `_stream_consumer.on_delta` 语义选主回调，排除 TTS-only fallback，双回调 fixture 回归测试。 |
| [#103 CLI env](https://github.com/Cheerwhy/hermes-lark-streaming/pull/103) | CLI 调用 Hermes 官方 dotenv 加载路径，`status` 在子进程也看到 profile 凭据。 |
| [#107 clarify](https://github.com/Cheerwhy/hermes-lark-streaming/pull/107) | 签名透传、完整异常日志、modular hook 已覆盖。 |
| [#108 split gateway](https://github.com/Cheerwhy/hermes-lark-streaming/pull/108) | 重新实现为原子多文件 patch plan，并对 `v2026.9.11` / `v2026.9.14` / `main` 跑兼容矩阵。 |
| [#110 流式 MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/pull/110) | 已吸收并补上 inline image / duplicate suppression。 |
| [#112 cron MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/pull/112) | 已吸收 cron 透传和 image message 路由。 |

## 审计原则

1. 不以 commit SHA 相同作为“已修复”证据，以行为、fixture 和兼容测试为准。
2. 可操作审批继续使用 Hermes 官方 adapter；插件不复制第二套按钮鉴权、过期和 resolver。
3. 所有重试都有上限，所有注入都先 compile，不以静默跳过掩盖上游结构漂移。
