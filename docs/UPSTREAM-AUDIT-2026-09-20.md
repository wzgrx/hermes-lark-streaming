# 上游 Issue / PR 审计（复核于 2026-09-23）

对象：[`Cheerwhy/hermes-lark-streaming`](https://github.com/Cheerwhy/hermes-lark-streaming)。本表记录当日仍开放的项目以及本 fork 中的可验证对应实现。

## Open Issues

| 上游 | 本 fork 状态 | 实现/验证 |
|---|---|---|
| [#111 cron MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/issues/111) | 已覆盖 | 模块化 cron hook 透传 `media_files`，图片与文件按类型投递，有去重测试。 |
| [#109 流式 MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/issues/109) | 已覆盖 | 保留原始 answer delta，follow-up/失败路径由插件补投，正文移除内部指令。 |
| [#106 clarify 签名](https://github.com/Cheerwhy/hermes-lark-streaming/issues/106) | 已覆盖 | `functools.wraps` + `*args/**kwargs`，适配 2/3 参和 batch clarify。 |
| [#105 模块化 Gateway](https://github.com/Cheerwhy/hermes-lark-streaming/issues/105) | 已覆盖 | `ModularPatcher` 跨 `run_inbound/run_turn/run_turn_runner/run_busy`，预编译、原子发布和回滚。 |
| [#98 CardKit 300313](https://github.com/Cheerwhy/hermes-lark-streaming/issues/98) | 已加强，流式错误待复测 | 元素流更新 200/400/800ms 有界重试；仅含 partial 更新的 batch 遇到短暂 300313 时以原 sequence 再试 200/400ms，成功即保持 segment 已创建状态；带 add 的 batch 不重放，维持缺失 segment 的回滚/重建路径。loading anchor、答案/推理流式文本、batch 中的推理面板内文本丢失时，有界重建整卡并重放当前 segments；新卡重置恢复额度，重复失败转文本兜底。sequence 仅在成功后提交，避免连锁 300317。`api.element_not_found_recovered` 只记录真实成功恢复。回归测试覆盖这些路径。2026-09-23 的上一轮 Gateway 指标中，`cardkit_stream_element` 有 601 次尝试、506 次错误、95 次成功，但旧指标缺少错误码，尚不足以把这些错误归因于 #98；新增固定错误码桶后再按新进程数据区分 300309/300313/300317/其他。重复流式错误现在按每卡 0.5–30 秒退避，保留脏文本由后续 flush 或最终整卡更新交付，避免每次 200ms flush 形成错误风暴。 |
| [#82 其他消息类型](https://github.com/Cheerwhy/hermes-lark-streaming/issues/82) | 已覆盖核心场景 | 后台复盘合并进最终卡片；审批保留 Hermes 原生按钮/回调，插件负责暂停旧流并在工具结束后换卡继续。 |

## 最新上游变化

- [#113](https://github.com/Cheerwhy/hermes-lark-streaming/pull/113) 已于 2026-09-22 合并并发布上游 v0.13.0，要求 Hermes 0.21.1 split layout。
- 本 fork 保留原子多文件补丁、doctor/metrics、投递台账、多 bot、CardKit 限额紧缩和供应链门禁，因此按行为吸收上游 anchor/签名变化，而非用上游简化版覆盖维护功能。
- 当前发布门禁以 Hermes 0.21.3 固定版本和最新 main 为准。

## Open PRs

| 上游 | 本 fork 对应 |
|---|---|
| [#102 主文本回调](https://github.com/Cheerwhy/hermes-lark-streaming/pull/102) | AST 按 `_stream_consumer.on_delta` 语义选主回调，排除 TTS-only fallback，双回调 fixture 回归测试。 |
| [#103 CLI env](https://github.com/Cheerwhy/hermes-lark-streaming/pull/103) | CLI 调用 Hermes 官方 dotenv 加载路径，`status` 在子进程也看到 profile 凭据。 |
| [#107 clarify](https://github.com/Cheerwhy/hermes-lark-streaming/pull/107) | 签名透传、完整异常日志、modular hook 已覆盖。 |
| [#108 split gateway](https://github.com/Cheerwhy/hermes-lark-streaming/pull/108) | 重新实现为原子多文件 patch plan，并对 `v2026.9.11` / `v2026.9.14` / `main` 跑兼容矩阵。 |
| [#110 流式 MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/pull/110) | 已吸收并补上 inline image / duplicate suppression。 |
| [#112 cron MEDIA](https://github.com/Cheerwhy/hermes-lark-streaming/pull/112) | 已吸收 cron 透传和 image message 路由。 |
| [#114 CardKit anchor/sequence](https://github.com/Cheerwhy/hermes-lark-streaming/pull/114) | 已吸收成功后提交 sequence 与 loading anchor 恢复，并继续补齐流式/嵌套文本丢失路径。 |

## 审计原则

2026-09-24 补充 [#98](https://github.com/Cheerwhy/hermes-lark-streaming/issues/98)
诊断链：本地测试会调用卡片完成/失败路径并写入默认
`~/.hermes/state/hermes-lark-streaming-metrics.json`，污染真实网关的错误码
统计；同时网关与可选 sidecar 原先共用固定 `.tmp` 文件名，两个进程同时
持久化时其中一个可能在 `os.replace` 遇到 `FileNotFoundError`。现在测试
为每例隔离指标路径，运行时使用每次写入独有的临时文件并在失败时清理。
并发回归在旧实现上复现了该异常。这只提高 #98 的诊断可信度，尚需真实
飞书流式负载确认其余 300313/300309 分布。
指标 CLI 在文件缺失或损坏时现在明确报告 `metrics_unavailable`，避免将
新启动的 CLI 进程空计数误报为 Gateway 的健康状态。

1. 不以 commit SHA 相同作为“已修复”证据，以行为、fixture 和兼容测试为准。
2. 可操作审批继续使用 Hermes 官方 adapter；插件不复制第二套按钮鉴权、过期和 resolver。
3. 所有重试都有上限，所有注入都先 compile，不以静默跳过掩盖上游结构漂移。
