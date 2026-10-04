# Hermes Lark Streaming

**让 Hermes 的回复成为持续更新、可追溯的飞书卡片。**

[![Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/workflows/test.yml/badge.svg)](https://github.com/wzgrx/hermes-lark-streaming/actions/workflows/test.yml)
[![CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/workflows/codeql.yml/badge.svg)](https://github.com/wzgrx/hermes-lark-streaming/actions/workflows/codeql.yml)
![Code version](https://img.shields.io/badge/code-0.20.12-blue)
![Python](https://img.shields.io/badge/Python-%E2%89%A53.11-blue)
[![License MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

[English](README.en.md) · [安装](INSTALL.md) · [V1 整卡设计与实测](docs/REFERENCE-V1.md) · [历史用量](docs/USAGE-HISTORY.md) · [计划状态](docs/FOOTER-V2-PLAN.md)

> **当前维护版 0.20.12**：工具摘要优先保留仍运行/结果未确认的步骤，再保留失败及最近完成项；新增运行中总数，保持最多 8 行及原顺序。缺失耗时显示 `—`，不冒充 `0ms`。V1 三面板与冻结设计保留。见[修复与验证记录](docs/MAINTENANCE-0212.md)。旧截图保留原验收版本。

> **0.20.4 桌面增强**：修复工具面板动态开关、缺失面板恢复、真实配置文件热更新及重试后的首响应统计；历史状态更紧凑，保留 V1 三面板与单写卡通道。代码与部署证据分别记录在 [本轮修复与验证](docs/DESKTOP-HARDENING.md)。

> **验收范围**：按用户最新选择，只处理桌面飞书，手机不再是本轮门槛。0.20.2 真实轮次、桌面双主题展开证据保留原版本范围，不当作新版逐像素证明。见 [V1 设计和实测](docs/REFERENCE-V1.md)及 [变更记录](CHANGELOG.md)。

### 0.20.0：V1 整卡布局

**工具 → 资源快照 → 回答正文 → 模型/本轮/历史 → 身份标签**。三个一级面板默认折叠，与后台复盘复用原生边框和箭头。工具四列对齐、失败浅红底、重复进度轮询有界合并；资源与用量使用标签在上、数值在下的双列网格。配置 `streaming.layout: reference` 开启；旧布局默认保留。

下面是 **0.20.6 真实 Windows 飞书客户端**的合成预览截图，仅裁出卡片内容，不是生产任务数据：

![V1 工具执行：原生四列、失败突出和有界步骤摘要](docs/assets/reference-v1-0206-tools.png)

![V1 资源快照：双列数值、GiB 和 WSL 范围](docs/assets/reference-v1-0206-resources.png)

![V1 Footer：模型上下文标题、紧凑本轮统计和历史分组](docs/assets/reference-v1-0206-footer.png)

[冻结设计图、配置、代码契约及剩余验收](docs/REFERENCE-V1.md) · [真实 builder 合成 JSON](docs/assets/reference-v1-completed.json)

### 0.20.1：真实工具结果与失败状态

实测发现 Hermes 完成回调的 `preview` 为空，结果和错误标志位于 `result` / `is_error`。已修复旧钩子的遗漏，并补齐非零退出判定、输出预算与脱敏回归。以下是**真实 Gateway 测试命令**的桌面局部截图，不含生产用量、聊天列表或消息 ID；不是手写假回执。回答完成和工具失败保持独立。

![真实成功命令：原始记录保留 stdout 测试标记](docs/assets/reference-v1-real-success-tool.png)

![真实预期失败：退出码 7、失败数和浅红底](docs/assets/reference-v1-real-failed-tool.png)

升级本版需在 Gateway 空闲/停止时重新安装生成钩子，见 [安装与更新](INSTALL.md)。旧卡片保留原历史，不自动重发或补造旧输出。

### 历史阶段 0.19.2：与后台复盘统一样式

“本轮详情”直接复用**后台复盘同款原生面板**：灰色小标题、右侧箭头、圆角细边框和相同内边距；默认折叠，内部保留紧凑统计。代码/测试及桌面折叠预览通过；托管 0.19.2 已部署，Gateway 与飞书连接检查正常。本版展开/手机外观仍单独验收。

![Windows 飞书真实截图：合成数据，仅裁出卡片，两个面板均折叠](docs/assets/footer-panel-client.png)

![运行架构：Hermes、Card 插件、飞书客户端及独立用量账本](docs/assets/runtime-overview.svg)

## 这是什么

这是 [Hermes Agent](https://github.com/NousResearch/hermes-agent) 的飞书/Lark CardKit 2.0 插件，维护分支来自社区项目。**只做 Hermes 集成，不是 OpenClaw 插件，也不是独立模型客户端。**

Hermes 负责提供商认证、模型调用、工具执行和会话；本插件负责卡片流式显示、可靠投递与只读用量采集。现有 Gateway 飞书连接保持唯一，额外安装 Node SDK 不会自动改变卡片外观。

## 功能一览

| 能力 | 当前实现 | 边界 |
|---|---|---|
| 流式卡片 | 回答、思考与工具按事件顺序显示 | 思考内容以模型实际返回及 Hermes 展示设置为准 |
| 可靠投递 | 成功后提交 sequence、稳定 UUID、投递三态台账 | `unknown` 不冒充成功，不自动重复发送答案 |
| 长任务续卡 | 时间/元素预算续卡，旧片封存 | 卡片历史收缩不等于 LCM 上下文压缩 |
| 打断与审批 | `/stop`、排队、后台/Cron、审批边界适配 | 原生审批 resolver 仍由 Hermes 持有 |
| V1 整卡 / Footer V2 | 工具、资源、正文、模型和历史；旧紧凑布局保留 | 0.20.4 修复记录单列；历史真实轮次及桌面双主题展开已验，手机不在本轮范围 |
| 历史用量 | SQLite 持久化，按月/日/模型/服务商/订阅标签查询 | 从启用后开始收集；不是账户全局账单 |
| 多提供商口径 | Hermes canonical + 7 类协议字段解析测试（Cohere V2 自 0.20.9 纳入） | 226 个目录入口不等于 226 家真实账号验收 |
| 运维 | doctor、metrics、只读检查、显式 API smoke | 测试通过、服务端通过、客户端验收分别记录 |

## 兼容保留的旧 Footer 布局

以下图用于保留的旧整卡布局（`layout: classic`），不是新的 V1；从**实际 `build_footer` 生成的字段与分组**绘制，使用合成数据。
**它是结构示意，不是飞书客户端截图，也不承诺像素一致。**

![0.18.0 紧凑 Footer 结构；示例数据，非客户端截图](docs/assets/footer-current-structure.svg)

运行期间显示回答、工具、确认、上下文摘要和实际服务商切换；单次请求错误与整轮失败分开。只有真实事件触发状态，详见[运行态契约与验证](docs/FOOTER-RUNTIME.md)。

![0.19.0 原生运行态结构；示例数据，非客户端截图](docs/assets/footer-runtime-states.svg)

**约八行常规详情**：服务/接口/思考、模型、四行双列指标、末次上下文、统计说明。长字段自动换行；模型差异或服务商切换时增加对应信息，不删掉真实差异。

- 默认折叠；展开后查看 provider、请求/返回模型、时间、token、缓存、末次上下文和路由。
- 输入包含缓存，缓存不重复加总。多次请求累计 token 与末次上下文是不同指标。
- 缺失值明确显示“未提供”，不编造费用、账户额度或压缩完成状态。
- 已用真实 Hermes sanitizer 复现长请求思考字段丢失；0.18.1 通过官方执行中间件读取白名单标量，精确匹配当前请求。缺少接口或身份有歧义时仍显示缺失原因，不解析对话预览或猜测配置值；“请求”不代表服务端确认。
- 原始设计图及与现状的差异，见[视觉审查与下一阶段验收](docs/FOOTER-DESIGN-AUDIT.md)。不要通过更换 SDK 掩盖尚未完成的布局工作。

[查看合成 Card JSON](docs/assets/footer-example.json) · [重建示意资产](scripts/build_readme_assets.py)

## 快速开始

### 运行要求

- 已配置飞书平台的 Hermes；持续兼容矩阵为 `v2026.9.11`、`v2026.9.14`、`v2026.9.21` 和测试时的 `main`，详见[兼容表](docs/COMPATIBILITY.md)。
- Python ≥ 3.11；项目 CI 为 3.11 / 3.12 / 3.13。
- Hermes PM 管理 `lark-oapi >= 1.7.3`、`PyYAML >= 6.0.3` 等声明依赖。
- 飞书应用具备当前功能所需的 CardKit、消息收发/回复及图片权限；凭据保留在 Hermes 现有配置中。
- Node SDK、独立 Channel 服务和 `lark-cli` 均不是运行时必装项。

### 托管安装

先阅读 [INSTALL.md](INSTALL.md) 的来源扫描与依赖同意说明。维护版安装示例：

```bash
hermes plugins install wzgrx/hermes-lark-streaming --enable --force
hermes pm install
hermes plugins doctor hermes-lark-streaming --ci
hermes --run-module hermes_lark_streaming verify
hermes --run-module hermes_lark_streaming install
```

`--force` 用于明确审核过的来源提示，不是关闭扫描。使用 Hermes 托管 launcher；只往旧 venv 安装包，不代表运行中的 Gateway 已加载。
启用/更新代码后，在空闲窗口按安装指南平稳重启 Gateway。不要中断正在执行的用户任务。

### 配置示例

将下列字段**合并**到现有 `config.yaml`，不要覆盖模型、凭据或其他插件配置：

```yaml
streaming:
  enabled: true
  width_mode: default
  layout: reference
  agent_name: "龙虾3号"
  resources:
    enabled: true
  footer:
    enabled: true
    mode: enhanced
    details: true
    text_size: notation # 灰色小标题；V1 指标数值保持 normal_v2
    history:
      enabled: true
      timezone: Asia/Shanghai
      show_models: false
      provider_labels:
        opencode-go: "OpenCode Go 订阅"
        siliconflow: "SiliconFlow 按量服务"
```

仓库默认是旧布局、`classic` Footer、资源和历史关闭。上面的配置显式开启 V1 / enhanced / 资源快照 / 历史统计。`layout: classic` 返回旧整卡布局；`mode: classic` 恢复经典 Footer，`enabled: false` 隐藏 Footer；账本采集独立。`show_models: true` 在展开区增加前三组订阅商/模型复盘。旧 `fields` / `show_label` 仅作用于经典模式。

## 历史用量：几个月以后仍然可查

账本位于 `$HERMES_HOME/state/card-usage.sqlite3`。按请求尝试去重，主请求与辅助调用分开；不保存提示词、回复正文、API key 或原始会话 ID。

```bash
# 已记录的全部用量：服务商、订阅标签、请求模型、返回模型
hermes --run-module hermes_lark_streaming history --timezone Asia/Shanghai

# 月报与 AI 可读取的 JSON
hermes --run-module hermes_lark_streaming history --month 2026-10 --timezone Asia/Shanghai --json

# 自定义范围；起点包含、终点不包含
hermes --run-module hermes_lark_streaming history \
  --from 2026-10-01 --to 2027-01-01 --group-by month --timezone Asia/Shanghai

# 按模型聚合，或区分主/辅助调用
hermes --run-module hermes_lark_streaming history --group-by model --json
hermes --run-module hermes_lark_streaming history --scope auxiliary --json
```

查询只读、不请求模型。账户轮换若缺少可靠账号 ID，按服务商/标签汇总；不通过 API key 猜账户。
历史数据从启用后积累；旧月份没有可信用量来源就不补算。在线备份请使用 SQLite backup API，应避免只复制正在写入的主文件。详见[历史用量文档](docs/USAGE-HISTORY.md)。

## 检查与更新

```bash
hermes --run-module hermes_lark_streaming doctor --json
hermes --run-module hermes_lark_streaming status
hermes --run-module hermes_lark_streaming metrics --json
hermes --run-module hermes_lark_streaming smoke
```

默认 smoke 是离线检查。显式的 `smoke --execute --closed-stream-probe` 会调用飞书 API 创建未发送到聊天的实体并测试终态更新，仍不等于客户端截图验收。
指标须检查是否属于当前 Gateway 进程；重启后的旧快照不等于实时错误率。

更新顺序：**确认空闲 → 保存回滚信息 → 平稳停止 → 托管更新/PM 同步 → doctor/verify/install/status → 启动 → 检查连接、日志、投递与客户端**。来源 pin、精确 SHA、审核提示与回滚命令统一以 [INSTALL.md](INSTALL.md) 为准。

```bash
# 在上述空闲维护流程内执行；更新源码后同步 PM，再安装钩子。
hermes plugins update hermes-lark-streaming
hermes pm install
hermes --run-module hermes_lark_streaming verify
hermes --run-module hermes_lark_streaming install

# 仅当明确要卸载时：先撤回钩子，再移除托管插件。
hermes --run-module hermes_lark_streaming uninstall
hermes plugins remove hermes-lark-streaming
hermes pm install
```

## 已完成与未完成

| 里程碑 | 状态 |
|---|---|
| 协议归一化、按轮采集、缺失/部分统计 | 已实现、自动测试通过 |
| SQLite 历史账本和 CLI/JSON 报表 | 已实现；线上开始积累，并已纳入维护者本机备份 |
| V1 整卡 0.20.1 托管部署 | 完成；空闲重启、doctor/新钩子/配置不变/只读账本健康通过；真实成功与预期失败两轮通过 |
| 0.17.1 托管部署、服务端创建/关闭/终态更新 | 已验证 |
| 桌面展开检查 | 0.17.1 旧设计失败；0.18.0 紧凑合成预览已直接检查 |
| 用户新要求：高密度紧凑详情 | 已实现，合成卡片桌面检查通过；0.19.2 已托管部署 |
| 思考档位在真实请求中的稳定展示 | 离线契约及真实 Gateway max（请求）显示通过；不是服务端采纳证明 |
| 回答/工具/确认/摘要/切换/错误运行态 | 回答及成功/失败工具真实轮次通过；审批/摘要/切换保留契约与合成验证，不冒充本次真实覆盖 |
| 客户端视觉矩阵 | 桌面不同宽度、深浅色及真实时钟 8 分钟续卡场景通过；手机不在本轮范围 |
| LCM 压缩提交前后值、账户额度、真实费用 | 待可靠事件/API；当前不推算 |
| 飞书内历史报表按钮或网页 Dashboard | 尚未实现，CLI 已可查询 |
| 226 家提供商真实账号逐家端到端 | 未做；目录和协议测试不等于账号认证 |

**整个计划尚未全部完成。** 详细证据见[验证记录](docs/FOOTER-V2-VALIDATION.md)，逐阶段见[任务计划](docs/FOOTER-V2-PLAN.md)。

## 为什么不直接安装 Node SDK 实现设计图

[官方 Node SDK](https://github.com/larksuite/node-sdk) 是接口/事件/发送层；[官方 CLI](https://github.com/larksuite/cli) 是操作与规范工具。
现有 Python SDK 同样向 CardKit 提交 JSON。**外观修复应发生在 JSON 布局和真实客户端验收，不是给 Gateway 多装一个发送端。**

推荐使用[飞书官方卡片搭建工具](https://open.feishu.cn/tool/cardbuilder)核对原生组件布局。
固定图片可保留画布构图，但图片内没有原生折叠/可复制字段；网页面板可自由排版，但需要独立托管与访问控制。完整比较及相关 Issue/PR 见[专项审查](docs/FOOTER-DESIGN-AUDIT.md)。

## 开发与文档

在独立开发环境运行，不把测试依赖装入 Gateway 的 PM 环境：

```bash
python -m pip install -e ".[dev]"
python -m ruff check hermes_lark_streaming tests
python -m mypy --explicit-package-bases hermes_lark_streaming
python -m pytest tests -q
python scripts/build_readme_assets.py
```

| 文档 | 内容 |
|---|---|
| [Footer V2](docs/FOOTER-V2.md) | 开关、字段口径、缺失语义 |
| [历史用量](docs/USAGE-HISTORY.md) | 数据持久化、时间范围、报表与备份 |
| [覆盖清单](docs/PROVIDER-COVERAGE.md) | 226 个目录入口及协议证据分级 |
| [运维](docs/OPERATIONS.md) | 指标、投递三态、续卡、可选 sidecar |
| [紧凑设计](docs/FOOTER-COMPACT.md) | 用户新要求、字段密度、实际验证与剩余工作 |
| [视觉审查](docs/FOOTER-DESIGN-AUDIT.md) | 设计差异、SDK/CLI 能力与下一阶段 gate |
| [Roadmap](docs/ROADMAP.md) | 已实现能力与未验证目标分开列示 |
| [更新记录](CHANGELOG.md) | 版本变更 |

灵感与上游参考：[Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)、[openclaw-lark](https://github.com/larksuite/openclaw-lark)、[hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card)。

## 贡献者

感谢以下贡献者的 Issue 和 PR：

<a href="https://github.com/Mxin-9527"><img src="https://avatars.githubusercontent.com/u/178271393?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/gitteeee"><img src="https://avatars.githubusercontent.com/u/128769493?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/Bandersnatch0x"><img src="https://avatars.githubusercontent.com/u/13325067?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/runfali"><img src="https://avatars.githubusercontent.com/u/39327978?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/thunderfight127-svg"><img src="https://avatars.githubusercontent.com/u/275854191?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/willggy"><img src="https://avatars.githubusercontent.com/u/74762604?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/atomperson"><img src="https://avatars.githubusercontent.com/u/14934637?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/linjunxin01"><img src="https://avatars.githubusercontent.com/u/63715504?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/mouxangithub"><img src="https://avatars.githubusercontent.com/u/48978046?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/numuly"><img src="https://avatars.githubusercontent.com/u/137970054?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/wzgrx"><img src="https://avatars.githubusercontent.com/u/39661556?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>
<a href="https://github.com/zhaomingcheng01"><img src="https://avatars.githubusercontent.com/u/46734892?v=4&s=64" width="48" height="48" style="border-radius:50%" /></a>

---

## 许可证

[MIT](LICENSE)
