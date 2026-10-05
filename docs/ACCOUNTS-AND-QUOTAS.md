# 缓存命中率与订阅账户（0.20.25）

## 为什么以前是 `≥99k / —`

这是两种数据完整性的组合，不是缓存失效：输入总量完整，缓存桶部分缺失。
Hermes canonical usage 会把可选字段补成零；该值失去字段是否存在的证据。
插件保留这种歧义，既不补造零命中，也不把已观测缓存总量当完整计数。

0.20.25 修正展示规则：

- 输入与缓存计数均完整：显示精确命中率。
- 输入总量完整、缓存计数部分完整：显示 **命中率下限**。
- 输入缺失、部分完整、为零或缓存大于输入：命中率仍为 `—`。
- 下限向下截断，不四舍五入向上。例如合成输入 149,120、已观测缓存
  99,072：`≥99k / ≥66.4%`，标签“缓存读取（部分） / 命中率下限”。
- 旧卡片不自动重写，历史缺失字段不回填。这个修复是正确展示，不改变
  服务商缓存策略，也不保证未来请求达到相同命中率。

参见 [DeepSeek final usage 字段](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)
和 [缓存机制](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)。这是协议依据，
不代表 Go / SiliconFlow 使用者拥有 DeepSeek 直连账户。

## 四类数据严格分开

| 数据 | 含义 | 来源 |
|---|---|---|
| 本轮 / 本机历史用量 | 此 Hermes 记录到的请求；部分完整时标下限 | 原有 SQLite 账本 |
| 订阅窗口额度 | 5h、周、月的已用/剩余百分比与重置时间 | 服务商只读 API |
| 账户现金余额 | 对应服务商账户的钱；不是订阅 Token 余额 | 有公开余额 API 的直连服务商 |
| Key 限额剩余 | 单个 API Key 可花的剩余额度；不是账户钱包 | OpenRouter 当前 Key API |

重置时间不是订阅到期时间。没有返回的余额 / 到期时间保持未知。
同一账户的多个 Key 可能共享同一额度，**不汇总多个 Key 的剩余百分比**。
本版本不推断“本轮用了哪个账户”，不做账户自动切换。

## 已实现与后续接入边界（2026-10-05 核实）

| 提供商 / 产品 | 可查询内容与认证 | 本版证据 |
|---|---|---|
| OpenCode Go | `GET /zen/go/v1/usage`；现有 Go Key；5h/周/月 percent 和 resetsAt | **现有账户 HTTP 200 实测**，无现金余额 / 到期字段 |
| DeepSeek 直连 | `GET /user/balance`；DeepSeek API Key；CNY/USD total_balance | 适配实现 + 合成协议测试，未查询真实账户 |
| OpenRouter | `GET /api/v1/key`；普通 API Key；limit_remaining | 适配实现 + 合成协议测试，标为 Key 限额而非账户余额 |
| SiliconFlow | `/user/info` 于 2026-08-14 下线 | 不调用旧接口；等待新的官方余额 API |
| MiniMax Token Plan | 官方 `/v1/token_plan/remains`；Token Plan Key；5h/周窗口 | 已找到官方文档，本版尚未接入 |
| 阿里百炼 | 官方 CLI `bl usage coding-plan` / `bl usage token-plan` / `bl token-plan harness-quota` | 已核实 CLI 能力，涉及对应管理身份 / 工作空间，本版尚未接入 |
| OpenRouter 账户钱包 | `/api/v1/credits`，management key | 与推理 Key 分开；本版不收集管理凭据 |
| Anthropic API | 组织 Usage/Cost 与 Rate Limits Admin API | 与 Claude Pro/Max 订阅分开；本版不收集 Admin key |
| ChatGPT / Codex / Claude 个人订阅及其他厂商 | 各自官方客户端或控制台，按实际凭据平面接入 | 不把本机 Token 计数兑换成“订阅余额” |

来源：

- [OpenCode 当前路由源码（精确快照）](https://github.com/anomalyco/opencode/blob/907b3bc518fa48e90e8ec24dd327d13eee71c36c/packages/console/app/src/routes/zen/go/v1/usage.ts)、[合并 PR #16513](https://github.com/anomalyco/opencode/pull/16513)、[Go 官方说明](https://opencode.ai/docs/zh-cn/go/)。
- [DeepSeek 余额](https://api-docs.deepseek.com/api/get-user-balance/)、[OpenRouter 当前 Key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)、[账户 credits](https://openrouter.ai/docs/api/api-reference/credits/get-credits)。
- [SiliconFlow 发布记录](https://docs.siliconflow.cn/docs/release-notes/overview)、[MiniMax 官方 Token Plan](https://platform.minimax.io/subscribe/token-plan)、[百炼 CLI 用量与配额](https://help.aliyun.com/zh/model-studio/cli/usage-quota)、[Anthropic Admin 用量](https://platform.claude.com/docs/en/manage-claude/usage-cost-api)。
- [CodexBar OpenCode 方法](https://github.com/steipete/CodexBar/blob/main/docs/opencode.md)提供账户展示思路，但旧版 cookie / 本机 SQLite 估算不优于新 Go 官方 API；本插件不新增浏览器 cookie 采集或 macOS GUI 依赖。

## 开启：明确账户与聊天白名单

配置放在现有 `config.yaml` 的 `streaming.footer` 下。默认关闭；只在
`layout: reference`、Footer enabled/details 为 true 且聊天在白名单时轮询/展示。
账户额度可能是私密信息，不建议把群聊加入白名单。

```yaml
streaming:
  layout: reference
  footer:
    enabled: true
    details: true
    accounts:
      enabled: true
      allowed_chats: [CHAT_ID]   # 使用你确认的私聊，不是通配符
      accounts:
        - id: go-main
          label: Go 主账户
          provider: opencode-go
          key_env: OPENCODE_GO_API_KEY
        # 可选第二账户；需你已有的独立凭据，不复制主账户冒充多账户
        # - id: go-second
        #   label: Go 第二账户
        #   provider: opencode-go
        #   key_env: OPENCODE_GO_API_KEY_2
```

最多读取列表前四项，别名去重，账户标签脱敏。凭据只保留环境变量名称；
支持 `OPENCODE_GO_API_KEY` / `DEEPSEEK_API_KEY` / `OPENROUTER_API_KEY`
及同前缀大写数字后缀。跨提供商环境变量引用会被忽略。
设置是启动范围配置；修改账户/白名单后按 INSTALL.md 平稳重启 Gateway。

## 查看与刷新

```bash
HERMES_LAUNCHER="$HOME/.local/bin/hermes"
# 默认仅查看显式配置，不发外部请求
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming accounts --json
# 显式只读查询官方接口；不改模型路由 / Key / 历史账本
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming accounts --refresh --json
```

卡片的“订阅账户 · API 快照”嵌套面板默认折叠，不改变原 V1 三个一级面板。
最多一个后台查询任务，每 300 秒最多刷新一次；最多四个账户，每个请求
超时 4 秒、响应上限 64KiB。固定官方 HTTPS 端点，禁止重定向携带凭据。
错误仅保留类型 / HTTP 状态，不显示 Key、响应原文、请求头或完整错误 URL。

每行显示服务商、显式账户标签、额度窗口 / 余额种类、时区转换后的重置时间、
快照时间。过期快照标“上次快照 · 待刷新”，部分窗口缺失单独标记。
网络读取不阻塞流式回答、不另建 CardKit 写通道。完成的卡片保留完成时快照，
不主动改写；第一轮非常短时可能在快照返回前结束，此时保留“待返回”，
下次消息复用结果。此处的 CLI 刷新独立于 Gateway 内存快照。

## 验收范围

缓存数学与适配器用合成回归测试；Go 官方读取另用现有账户实测。
未挂到聊天的真实 CardKit API 测试只证明原生结构可被服务端接受，不冒充
Gateway 回合 / Windows 截图。旧 0.20.24 真聊天截图保留原版本标记。
账户快照不写入历史 Token 账本；不创建数据库 / 后台守护进程 / 定时任务。
