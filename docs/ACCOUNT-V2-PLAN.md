# V2 账户增强计划与能力边界 — 0.21.2

## 回答“是不是全部增加了”

前版只有三个账户查询适配器。本版保留原生 V2 的工具、资源、回答、Footer、身份
结构，增强的仅是 Footer 内的账户组；没有第二套 Hermes 或另一条写卡通道。

**推理协议覆盖、提供商目录、余额 API、订阅权益 API、真实账号验证是五个不同层级。**
models.dev 目录收录 226 个入口（地区/代理/订阅分别计数），不是全球厂商总量。
按该官方项目的 2026-10-05 快照打包本地目录，不在每条消息重新抓取网络。
目录项默认 `not_implemented`；只有以下十个产品标为已实现，未实现的不猜管理端点。

| 产品 ID | 实现内容 | 凭据 / 地区边界 |
|---|---|---|
| opencode-go | 5h / 周 / 月剩余及重置 | Go Key；其 API 没有钱包 / 订阅到期字段 |
| deepseek | CNY/USD 账户余额 | DeepSeek 直连 Key；不是经代理使用 DeepSeek 模型的账户 |
| openrouter | 单 Key 限额及 Key 到期 | 普通推理 Key；Key 到期不是订阅到期 |
| openrouter-credits | 账户 credits 总额减已用 | 单独 `OPENROUTER_MANAGEMENT_KEY`；不拿推理 Key 去碰管理接口 |
| moonshot | 可用 / 现金 / 代金券余额 CNY | 国内 `MOONSHOT_API_KEY`；现金欠费负值如实显示 |
| moonshot-global | 同类余额 USD | 独立 `MOONSHOT_GLOBAL_API_KEY`，不轮流试国内/国际端点 |
| minimax | Token Plan 窗口或余额，按官方 Key 类型分流 | 国际 `MINIMAX_API_KEY`；固定 api.minimax.io |
| minimax-cn | 同类信息 | 独立 `MINIMAX_CN_API_KEY`；当前官方 CLI 使用 api.minimax.cn |
| zai | Coding 额度 + 个人订阅有效期 / 续费日期 | `ZAI_API_KEY`；两个固定只读 API；原样 Authorization |
| bigmodel | Coding 额度 + 个人订阅有效期 / 续费日期 | `ZHIPU_API_KEY`；不借用团队 JWT 或其他账号 token |

API 分母/字段缺失保持未知。MiniMax 官方 CLI 指出 `*_usage_count` 有“剩余/已用”
两种历史语义；本版只用明确的 remaining percent/count，不猜方向。余额未给币种
时保留“币种未标明”。Z.ai/智谱 `valid`/`nextRenewTime` 未标时区则只显示 API 日期，
不假造 UTC 时间；自动续费日期、当前有效期结束、最终订阅到期保持区别。

OpenAI、Anthropic、Gemini/Vertex、Azure、AWS Bedrock、xAI、阿里百炼、腾讯/火山等
管理平面的身份/组织/工作空间参数，以及个人 ChatGPT/Codex/Claude 订阅，不由
一个通用推理 Key 自动代表。SiliconFlow 已退休的 `/user/info` 不重新启用。
它们仍在目录或本地候选里，显示当前适配状态；不计入十个已实现产品。

## 实施顺序与验收门槛

1. 阅读维护 fork / 上游开放 issue/PR、Go 路由、OpenRouter/Kimi 文档、MiniMax
   官方 CLI、Z.ai 官方 ZCode 配额与订阅实现；保留精确源码与本地证据。
2. 本地提供商目录 + 十个固定官方产品注册，注明 credential plane 和 capabilities。
3. 可选自动发现现有环境变量；显式账户优先、同厂商同 Key 去重、不持久化 Key 或其摘要。
4. 区分额度重置、订阅有效期、自动续费、Key 到期、账户余额、Key 限额；未知值显式展示。
5. 覆盖业务错误信封、币种、欠费、超大 Decimal、时间、鉴权与地区、轮询缓存和轮换回归。
6. 在实际运行 Hermes PM Python/依赖上跑全套；原生 CardKit create/batch/close/update
   与真实 Gateway 回合分别记证据。API 合成实体不冒充用户聊天。
7. 推送维护 main，等待精确提交 Tests / CodeQL，空闲双检后在原托管插件部署并检查健康。
8. 一条新真实用户聊天；核对模型/工具/账本/投递，再截图账户与 Footer 折叠/展开。
   取得证据前不把本层写成通过。长回合压缩+插话/多厂商真实账号仍分别验收。

## 自动发现与加载

```yaml
streaming:
  layout: reference-v2
  footer:
    accounts:
      enabled: true
      auto_detect: true
      allowed_chats: [CHAT_ID]
      accounts:
        - id: go-main
          label: Go 主账户
          provider: opencode-go
          key_env: OPENCODE_GO_API_KEY
          # 已有账单记录的到期日可显式填；会标“手动记录”，不冒充 API 返回。
          # subscription_expires_at: '2026-12-01T00:00:00+08:00'
```

默认关闭；本机已有白名单维持不变。发现只检查已登记环境名称，不读浏览器 cookie、
密码库、其他项目文件或随机 IP；不安装每家 SDK。出现未知/未实现厂商时不发送其 Key。
候选不等于鉴权已验证，也不推断本轮调用账户。来源标签、失败和缺字段必须保留。

Gateway 展示和查询最多四项，显式账户先行。发现每 60 秒或环境名/配置变化时重算，
不是每个 token 重扫；现有 Key 的值变化仍即时使旧身份快照失效。读取每 300 秒最多
刷新一次，一个后台任务，每个 HTTP 请求 4 秒 / 64KiB，禁止携凭据重定向。
同身份读取失败时保留上次成功快照并标过期，不覆盖为假零；换 Key 后不继承旧账户数据。

```bash
hermes --run-module hermes_lark_streaming accounts --catalog --json
hermes --run-module hermes_lark_streaming accounts --discover --json
hermes --run-module hermes_lark_streaming accounts --discover --refresh --json
```

前两者零外部查询；发现清单最多 16 条，实际刷新仍最多四条。查询 / 卡片展示不自动
改模型路由、轮换账号、续费、充值、创建 Key 或改历史账本。无新数据库、守护进程或定时器。

## 当前官方来源

- [models.dev 官方项目](https://github.com/anomalyco/models.dev)、[目录 API](https://models.dev/api.json)：只作目录，不提供统一余额协议。
- [Go usage 路由](https://github.com/anomalyco/opencode/blob/dev/packages/console/app/src/routes/zen/go/v1/usage.ts)：只返回 usage 三窗口。
- [OpenRouter 当前 Key](https://openrouter.ai/docs/api/api-reference/api-keys/get-current-api-key)、[账户 credits](https://openrouter.ai/docs/api/api-reference/credits/get-remaining-credits)：普通 Key 与 management key 分开。
- [Kimi 国内余额](https://platform.kimi.com/docs/api/balance)、[国际余额](https://platform.kimi.ai/docs/api/balance)：币种、地域 Key 和欠费字段分别核实。
- [MiniMax 官方 CLI 端点](https://github.com/MiniMax-AI/cli/blob/06e47c70b76f419196678367dae62acca4c94076/src/client/endpoints.ts)、[计数歧义](https://github.com/MiniMax-AI/cli/blob/06e47c70b76f419196678367dae62acca4c94076/src/utils/quota.ts)、[地区](https://github.com/MiniMax-AI/cli/blob/06e47c70b76f419196678367dae62acca4c94076/src/config/schema.ts)。旧网页可能转到新 M Plan，以当前 CLI 源码契约为依据。
- [Z.ai 配额映射](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/services/src/usage-stats/providers/bigmodelUsageQuotaMapper.ts)、[个人订阅契约](https://github.com/zai-org/ZCode/blob/29628c9acdb81b703bbd4080c207a0e7ce5e276e/packages/services/src/bigmodel/codingPlanEntitlement.ts)：保留额度/权益/续费边界，不移植充值或 reset 写接口。

P0–P7：账户信息只收在既有次级折叠组；回答仍是唯一主焦点，未知/手动/过期为辅助语义；
无新大按钮、第四个一级面板或固定宽度。主布局、历史账本和现有单写卡生命周期不变。

## 0.21.3 可靠性补充

- 自动发现关闭时，显式配置的同提供商/同 Key 别名也去重；首个标签和记录优先。容量已满时不再检查无关凭据。
- 正在查询时由用户修改 Key，只在原有后台任务顺序跟进最终身份；中间身份不查询、旧结果不发表。没有自动选账号或自动换号。
- 待查询、待接入、缺凭据、查询失败分别标注。401/403、429 和其他 HTTP 失败保留有界原因；旧成功快照的时间不改，最近失败时间单独显示。
- 未核实的余额形状保留未知；钱包允许的负余额如实呈现。一级布局、默认折叠、4 项上限、300 秒缓存和聊天白名单不变。
- [SiliconFlow 最新官方公告](https://docs.siliconflow.cn/docs/release-notes/overview)明确 `/user/info` 在 **2026-08-14** 退役。旧 GitHub/OpenAPI 文件不代表当前可用；本版将此候选标“官方账户接口已退役”，不向退役接口发送 Key。

本轮 native fixtures / 当前 Hermes PM 测试与真实用户聊天分别记录；没有新入站与客户端截图时，不把前者当作后者，也不覆盖历史验收记录。

## 0.21.4 当前订阅商优先

主卡片以本轮请求 telemetry 的 `provider` 为依据，只展示该订阅商的配置账户；不是按模型名称猜供应商，也不是把默认模型配置当作实际回合证据。未开始请求或缺少提供商身份时，暂不查询/展示任意默认账户。OpenRouter 的普通 Key 与同商 management wallet 仍分开标注。

- 本机全提供商目录、发现清单和未适配候选仅保留在 `accounts --catalog / --discover`；不再塞入主消息。
- 展示期只解析当前商的凭据引用并查询已审核端点。每商一个后台读任务，最多四个分商缓存；并发/切换回合不混用其他商的余额。
- 终态只校验当前身份、不新起查询；已有读任务最多额外等 0.6 秒，不取消 HTTP worker。冻结时尚未就绪的卡片明确说“后续消息刷新”，不承诺旧卡片随后自动更新。
- 展示：三列额度/重置、双列到期/余额、简短 API 时间与来源。空值保留 `—`，API 缺字段不是金额 0；额度重置不是到期，当前商也不证明具体被调用 Key 的归属。
- Go 官方 usage 路由与本机只读结果仅有 5h/周/月窗口；未返回钱包或订阅到期，持续明确显示“API 未返回”。没有猜内部 billing 地址或将月窗口重置伪装成到期。
- 历史真回复保留原版本；当前代码、原生合成实体、客户端预览与新真实用户对话分别验证。
