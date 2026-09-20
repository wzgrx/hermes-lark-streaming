# Roadmap

以下项目来自对 Hermes 官方、Aowen 分支、Bailey sidecar 和 Lark 官方工具链的对比。优先级依据用户影响、升级稳定性和实现成本排序。

## P0：近期

1. ✅ **原生插件钩子优先与 AST 兼容层**：已实现原生协议能力探测；跟进 [Hermes CardKit 整合讨论](https://github.com/NousResearch/hermes-agent/issues/33854)，当官方暴露 answer/reasoning/tool/approval 生命周期后删除对 `gateway/run_*` 的源码注入。
2. ✅ **Doctor / monitor / metrics**：参考 [Aowen-Nowor/hermes-lark-streaming](https://github.com/Aowen-Nowor/hermes-lark-streaming) 的 `doctor`、卡片 TTL、metrics 与配置热加载，增加可观测的卡片成功率、fallback 率、API 延迟和 300313 自愈计数。
3. ✅ **真实 Feishu E2E**：用独立测试群验证创建→流式→工具→审批→MEDIA→完成，日志仅保留飞书 `log_id` 与脱敏元数据。
4. ✅ **自适应背压**：按 429/230020 和延迟动态调整 flush interval，并将连续 partial update 合并为最新快照。

## P1：功能

5. ✅ **多 profile / 多 bot 路由**：参考 [baileyh8/hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card) 的 profile/bot 隔离，为每个 app_id + chat/thread 保持独立 controller、限流器和诊断统计。
6. ✅ **可交互卡片加固**：为 clarify/approval 加回调签名验证、精确 request identity、过期冻结与重放防护；保持 Hermes 作为唯一 resolver。
7. ✅ **长任务压缩**：将较早的 reasoning/tool rounds 收缩为统计摘要，保留错误和最近 N 轮全文，避免卡片元素上限导致过早拆卡。
8. ✅ **表格/超长 Markdown 细化**：表格溢出降级、DeepSeek/Qwen 思考标签清洗、代码块和大图占位的可预测分片。

## P2：工程化

9. ✅ **`lark-cli` smoke chain**：使用 [larksuite/cli](https://github.com/larksuite/cli) 做权限状态、接口 schema 与 dry-run 预检；它是运维/验收工具，不成为插件运行时依赖。
10. ✅ **Sidecar 可选模式**：对高并发多 bot 部署提供进程隔离模式，但保留当前零额外服务的进程内默认模式。
11. ✅ **发布供应链**：PyPI 签名包、SBOM、对应 Hermes 兼容表和自动回滚指南。
12. ✅ **国际化/可访问性**：中英之外的 locale，颜色不作为唯一状态信号，以及移动端紧凑布局的截图回归。


## P3：Hermes 最新接口与可靠投递

13. ✅ **投递三态与稳定 UUID**：初始卡片 UUID 跨重启复用；仅 `not_sent` 允许新 UUID
    重试，`unknown` 抑制重复答案并发一个幂等通用提示。
14. ✅ **话题锚点与恢复测试**：保持 Hermes 最新 `_reply_anchor_for_event` 语义，引用消息
    优先、普通消息回落 event message id；拆卡和恢复均复用同一 anchor。
15. ✅ **SDK 能力检查/显式修复**：按实际构造器探测 `lark-oapi`，doctor 同时报告官方
    `lark-channel-sdk` 就绪度；修复严格落到当前 Hermes Python。
16. ✅ **原生 Hermes observer bridge**：注册 0.21.3/main 的流、工具与审批 observer；
    因返回值被忽略且缺少飞书路由 id，它们只做脱敏指标，AST 仍是唯一 delivery owner。
17. ✅ **供应链强化**：Actions 完整 SHA 固定、CodeQL、统一 `actions/attest`、自动化契约测试。

## P4：等待上游 owner API 后执行

- Hermes 发布能 claim/suppress 平台回复并携带 `chat_id/message_id` 的稳定 renderer API 后，
  将 CardKit owner 从 AST 一次性迁移到原生接口；禁止 observer 与 AST 双投递。
- 在独立 profile 做 `lark-channel-sdk` shadow transport，对比去重、CardKit 流式、媒体、
  callback 和多 bot 行为；通过真实 E2E 后再替换当前 `lark-oapi` transport。

## 0.16.0 落地说明

实现与运维入口见 [Operations](OPERATIONS.md)，兼容边界见
[Compatibility](COMPATIBILITY.md)，Bailey sidecar 的逐项取舍见
[Bailey audit](BAILEY-AUDIT.md)。原生 observer 已在 Hermes 0.21.3/main 上启用，可靠投递台账与 SDK doctor 已落地。
真正的 CardKit owner 切换仍取决于 Hermes 上游发布稳定的
`register_streaming_renderer` 生命周期协议。
