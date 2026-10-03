# 历史用量账本（0.17.0）

> 功能首次加入 0.17.0；维护者当前托管版为 0.19.2。2026-10-03 已核实生产配置启用 history，并使用托管启动器完成以下只读 CLI 验收。下文默认关闭指新安装默认值，不代表维护者当前设置。

## 解决什么问题

启用后持续记录当前 Hermes profile 的请求用量，数月后仍可按服务商、订阅标签、请求模型、返回模型、日/月查询，并显示总量。SQLite 位于 `$HERMES_HOME/state/card-usage.sqlite3`（默认 `~/.hermes/state/card-usage.sqlite3`）。不随卡片生命周期或会话清理而清空，无自动过期删除。

默认关闭，独立于经典/增强 footer 展示。只收集当前进程实际触发的主请求和辅助请求 hook；CLI、其他网关、定时任务只有在各自进程加载本插件、启用相同 profile 配置并发出 hook 时才覆盖。不是账号全局账单，也不包含其他软件发出的请求。

## 配置

在现有 `config.yaml` 中合并，不覆盖其他配置：

```yaml
streaming:
  footer:
    mode: enhanced
    details: true
    history:
      enabled: true
      provider_labels:
        opencode-go: "OpenCode Go 订阅"
        siliconflow: "SiliconFlow 按量服务"
```

这里只设置展示标签，模型、接口、API key 仍归 Hermes 配置管理。订阅标签按事件发生时保存；改标签不重写历史。相同服务商的轮换账号没有可靠账号标识时汇总到该服务商，不通过 API key 猜账号。不要把密钥、邮箱或带 token 的 URL 填进标签。

## 查询（Hermes/DeepSeek 可直接调用 CLI）

已安装插件的 Hermes 托管解释器：

```bash
# 所有已记录用量，按服务商/订阅/请求模型/返回模型分组
hermes --run-module hermes_lark_streaming history --timezone Asia/Shanghai

# 指定月；方便 AI 读取 JSON
hermes --run-module hermes_lark_streaming history --month 2026-10 --timezone Asia/Shanghai --json

# 最近几个月：起点包含、终点不包含，以下覆盖 10/1 至 12/31
hermes --run-module hermes_lark_streaming history --from 2026-10-01 --to 2027-01-01 --group-by month --timezone Asia/Shanghai

# 按模型、服务商、订阅、日期汇总
hermes --run-module hermes_lark_streaming history --group-by model --json
hermes --run-module hermes_lark_streaming history --group-by provider --json
hermes --run-module hermes_lark_streaming history --group-by subscription --json
hermes --run-module hermes_lark_streaming history --group-by day --json

# 区分主对话和压缩/标题等辅助任务
hermes --run-module hermes_lark_streaming history --scope main --json
hermes --run-module hermes_lark_streaming history --scope auxiliary --json
```

开发环境也可用 `python -m hermes_lark_streaming history ...`。查询不访问提供商网络、不启动模型、不产生 token 费用。暂未注册飞书 `/usage` 交互按钮/新命令，避免冒充已实现的 UI；本版交付持久化、汇总和可供 AI 使用的 CLI/JSON。

## 指标口径

| 字段 | 意义 |
| --- | --- |
| requests | 已观测到终态的物理请求尝试；重试单独计数，相同回调去重 |
| measured_requests | 同时有输入、输出 token 的请求数 |
| completed_requests | 收到成功终态的请求数，不代表已向飞书投递 |
| error_attempts | 观测到错误的尝试数；后续同尝试成功仍保留错误证据 |
| input_tokens | 已知输入合计，**含缓存** |
| output_tokens | 已知输出合计，按 Hermes 规范化口径 |
| total_tokens | 输入输出均已知的请求之输入+输出合计；不是上下文占用 |
| cache_read_tokens / cache_write_tokens | 已确认缓存子集，不再次加进总 token |
| reasoning_tokens | 上游明确报告的推理 token；通常已属于输出，不再加总 |
| known_field_requests | 每个字段有证据的请求数量；便于识别部分统计 |
| usage_partial | 有请求缺失完整用量时为 true |
| coverage.first_event / last_event | 当前筛选范围实际记录的第一/最后时间 |

缺失字段为 `null`，不是零。Hermes 规范化层当前可能将缺失缓存/推理字段补零，因此这些可选字段仅保留正数证据，不展示虚假的 0% 缓存命中。流式辅助调用可能没有最终 usage，保留其请求数并标记部分统计。

跨午夜/月界按**请求开始时间**归属；默认 UTC，可选 IANA 时区。进程崩溃且未发出终态 hook 的请求暂不记录；停用期间不补算。多个月历史从启用之日起积累，旧数据只有另行核对可信 usage 记录后才应导入；本版不自动读取聊天内容做估算。

费用、订阅剩余额度、账号轮换分摊属于后续功能：需要提供商真实账单/额度 API 或明确维护的计价证据。token 合计不等于订阅消耗比例，不用模型名推算金额。

## 持久化与备份

- 新数据库创建权限 0600，标准库 SQLite/WAL，无额外服务或依赖。
- 唯一事件摘要用于去重；只保存白名单元数据与计数，不保存原始会话 ID、提示词、回复、错误正文、base URL 或凭据。
- SQLite 锁最多等待约 0.2 秒；写入失败记录通用日志且不打断推理。此类丢失事件没有后台自动补发，检查日志中的 `Usage history event was not recorded`。
- 查询采用只读连接，空查询不创建数据库。
- 在线备份使用 SQLite backup API；不要只复制运行中的主文件遗漏 WAL。维护者后续部署已将账本纳入现有备份并验证快照，见 [部署记录](FOOTER-V2-VALIDATION.md)。本次只读 CLI 验收没有修改备份任务。
- 开发阶段与托管部署分离。当前生产账本已启用，0.19.0 的托管部署及健康状态见 [运行态记录](FOOTER-RUNTIME.md)；不要把最初开发阶段的“未部署”当作当前状态。

## 2026-10-03 托管 CLI 验收

通过实际 `hermes --run-module hermes_lark_streaming history ... --json` 查询当前账本，以下七种入口均退出 0，返回可解析 JSON、分组与合计：指定 2026-10 月及 Asia/Shanghai 时区、按服务商、按模型、按订阅标签、按月份、仅主请求、仅辅助请求。已有主请求查询为 `ok`；辅助范围为 `empty_range`，空范围不是零费用证明。未公开实际用量、聊天身份或订阅标签。

这是部署后的只读查询证据，不替代新轮次写入/去重验收，也不证明已经积累数月真实数据。旧月份不自动补造；查询不调用模型或提供商 API。

## 架构增量

```text
Hermes post_api_request / api_request_error / post_auxiliary_call
  ├─ whitelist + usage normalization + hashed physical-attempt identity
  │    └─ opt-in profile SQLite (persistent)
  │         └─ read-only history CLI → month/provider/model/subscription reports
  └─ precise session ContextVar → per-turn footer state (ephemeral)
       └─ two-line footer + collapsible details (existing delivery owner)
```
