# 0.20.24 — Footer 历史统计表达

## 问题与范围

原 V1 今日/月/累计和模型行把不完整统计显示为普通四舍五入缩写加 `*`，例如 1,999 个已知 token 显示 `2k*`。星号说明不完整，却没有明确表达已知子集是下限。现在统一显示 `≥1.9k*`，复用既有整数向下缩写逻辑，不把下限放大。只有缺失 usage 时仍显示 `—*`；完整计数、已报告零和真正空时段不改。

模型分组明确标为“累计 · 前 3 项”，区分相邻的今日/月统计和完整 CLI 报表；`*`、`≥` 的解释放在现有范围行。不增加面板或行数，保留原生折叠 ID、双列指标、三列模型分组与右对齐计数。账本 schema、SQL 聚合、采集、费用未知策略、投递钩子和依赖保持原样；不回填旧卡片或重写历史。

## 验证与部署门槛

- 使用正在运行的 `/home/wzgrx/.hermes/hermes-agent`（`0764e9165721fdec30a11cf5fb0ac255cb09bb6f`）及 PM 选中的 Python 3.14.7/依赖；不创建另一个 Hermes 快照或安装测试依赖。激活 PM 后隔离测试数据，借用已有纯 Python pytest 工具。
- 20 项新增回归覆盖部分计数、未知/无效计数、完整计数、SQLite 缺失请求聚合、双语说明、布局不增行、未就绪状态。确认原布局为 13 个直属元素后记录修复前结果：9 失败、11 基线通过；修复后 20 通过。SQLite 为临时合成数据，不改写生产用量。
- 部署门槛为全量当前运行环境测试、Ruff/mypy、精确提交 GitHub Tests/CodeQL，以及真实但未附着聊天的 CardKit Footer 状态探针；结果写入本机版本化记录。未完成验证不记录为通过。
- Card-only 空闲检查、平稳停止/启动、PM 同步、源码哈希、doctor、日志及只读数据库检查。保持 Hermes 核心、LCM、provider、凭据和会话配置不变。
- 接口探针只使用合成 Footer 数据，不发聊天消息、不执行工具、不调用模型、不写真实账本。它证明服务端接受卡片 JSON，不证明新 Gateway 用户回合或 Windows 客户端逐像素外观。

## 网络与代码复核

- 认证 GitHub API 核对 fork、上游和相关项目 main 与 open issue/PR，未盲目合并其它维护路径。
- [配置可发现性讨论 #370](https://github.com/baileyh8/hermes-feishu-streaming-card/issues/370)提示把 UI 能力和边界写清楚；同步 README、历史说明和 roadmap，不增加 Node/侧车环境。
- [Hermes 辅助模型污染 Footer 讨论 #43228](https://github.com/NousResearch/hermes-agent/issues/43228)已关闭且标记当前 main 未复现；本插件仍按绑定的主请求事件取模型，不从辅助模型或全局最后会话猜测。该旧报告不是本轮新故障。
- [上游 #116](https://github.com/Cheerwhy/hermes-lark-streaming/issues/116)仍是独立的压缩/插话投递问题，本轮历史标签修改不是它的修复证明。

旧未知投递保留证据，不清零、不自动重发；缺少当前 Gateway 指标时不伪造新快照消除告警。
