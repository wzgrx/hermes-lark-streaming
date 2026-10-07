# 更新记录

## [1.0.0] - 2026-10-07

完全重写,0.21.x 历史见标签 `legacy-0.21.5`。

- 单一卡片布局:状态行、过程面板、回答、底栏、详情面板;三套旧布局合并。
- 详情面板收拢用量、资源监控、订阅账户三块,各自独立开关。
- 新架构:`card`(纯渲染)、`details`(数据)、`transport`(CardKit 与可靠投递)、`session`(状态机与控制器)、`hooks`(声明式注入表与引擎)。
- 注入引擎全量编译后才写入,失败整体回滚,可清理 0.x 标记块。
- 配置精简,旧键不兼容,见 docs/MIGRATION.md。
- 原先以本地提交方式携带在 Hermes 里的补丁(日志脱敏、OpenCode Go 403 轮换、`skills.index_mode: names_only`)移入插件的 `compat` 包,Hermes 可保持纯上游。
- lark-oapi 下限降为 1.6.8,与 Hermes 自带的飞书依赖固定不再冲突。
- 新增 `scripts/live_acceptance.py`:用真实飞书接口走一轮模拟对话做验收。
