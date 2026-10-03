# Footer V2 / 历史用量：交付与未完成项

> **当前入口（0.20.4）**：代码、托管部署、服务端与 Windows 原生合成卡片已分别验证，见[当前交付审计](DESKTOP-0204-ACCEPTANCE.md)。1482 项完整测试通过；真实 Gateway 模型轮次证据最近为 0.20.2，0.20.4 新轮次尚未验证。手机已由用户排除；逐像素一致性没有认证。下文保留各历史版本的实际结果，不代表当前故障清单。

> **历史 V1 验收（0.20.1）**：1435 全量测试、Ruff/mypy、Tests / CodeQL / Hermes Compat Check 通过；托管部署和刷新钩子后空闲重启。真实 Gateway 成功 stdout 与预期退出 7 两轮、Windows 失败高亮与完成状态分离、max（请求）、账本统计核对和独立话题 interactive 读取通过。doctor 健康，当前进程 API 错误/traceback 为 0；历史 unknown 回执保留。当时手机登录验证过期，跨端/像素级未验。下面为历史版本证据，参见 [REFERENCE-V1.md](REFERENCE-V1.md) 和 [0.20.1 真实新轮次记录](assets/reference-v1-real-gateway-checks.json)。

> **后续进展：0.19.0 已部署** 紧凑详情、结构化思考标量与运行态。运行态的七阶段局部更新、正文插入、关流及失败终态通过真实 CardKit API；托管更新与空闲重启后 Gateway/飞书/版本/数据库健康检查通过。下面保留的是 0.17.1 部署基线记录，不代表最新源码或运行版。真实新轮次与跨端验收继续单列，当前证据见 [紧凑布局](FOOTER-COMPACT.md) 与 [运行态](FOOTER-RUNTIME.md)。

> 后续变更：用户将详情要求更新为“好看、紧凑、高信息密度、减少纵向占用”。0.18.0 已实现紧凑布局并检查真实桌面合成预览；旧三列目标和五组长表不再作为当前详情验收标准。部署及其他原计划目标保持独立，详见 [紧凑设计与当前证据](FOOTER-COMPACT.md)。
日期：2026-10-03。运行代码：0.17.1 / [`1d3cb3c`](https://github.com/wzgrx/hermes-lark-streaming/commit/1d3cb3c6edab10fae7daabf3fccc5366011d0f98)。

> 本报告替换先前 0.17.0“仅源码、尚未部署”的旧结论。**已经部署，服务端通过，视觉验收未通过。** 文档更新不等于新运行功能上线。

## 代码与自动检查

| 检查 | 已记录结果 |
|---|---|
| 全量 pytest | **1058 passed**；2 条 SDK 弃用警告 |
| Footer 定向测试 | 290 passed |
| Ruff / mypy | PASS；mypy 检查 41 个源文件 |
| Python 3.11 / 3.12 / 3.13 与 Hermes 兼容矩阵 | [Tests 成功](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37096099715) |
| CodeQL | [成功](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37096099722) |
| 部署版本的 ContextVar → observer → CardSession | 隔离合成数据契约通过，不调用模型 |

实现包含 canonical usage 与 6 类外部协议 parser、按轮身份/去重/主辅隔离、缺失/部分统计、按需历史账本、A–E 分组。增强详情预留 **40** 个元素，无详情增强模式 **5** 个，经典模式 **2** 个；先前“增强预留 6 个”已过时。

## 托管部署与服务端验证

- PM 创建新依赖快照；实际加载托管版本，不只是开发 checkout。
- 0.17.1 plugin doctor 通过，注册 12 hooks；verify/install/status 通过。
- 空闲后平稳停止、更新、启动；模型、凭据、会话和 LCM 保留。
- enhanced / details / normal 字号 / history 已在维护者环境启用；Gateway running、飞书 connected。
- 显式探针：创建实体、关闭流式、终态全量更新获得成功回执。
- 自动测试卡已发送；没有把演示 fixture 写入生产历史。
- 新账本只读 quick_check 通过；维护者本机自动备份加入该数据库，并实际验证 SQLite backup API 快照。

这些证明部署和 API 链路，不证明样式与设计一致。本机路径、聊天身份和私人窗口截图不公开到仓库。

## 客户端检查：已执行，但未通过设计验收

用户再次报告不一致。本轮直接查看 Windows 飞书并展开最新卡片，确认：

- 实际是两列；字段名与值在同一 Markdown 流中，没有设计稿的独立数值列。
- 分组标题是黑色小标题，而非蓝色分级标题；实际间距、宽度和字体与图片画布不同。
- 真实卡片思考档位显示“未提供”，该采集/显示路径仍待诊断。
- 折叠/展开操作可用；手机、深色主题和完整长任务视觉矩阵仍未完成。

因此**不以 1058 tests passed 覆盖失败的视觉 gate**。

## 0.17.1 当时尚未交付（历史清单）

这不是当前待办：网格、思考采集、运行态、历史用量和真实轮次的后续证据已在当前入口关联；账户账单、独立 Dashboard 等仍未实现，不因旧清单存在而自动加入新一轮任务。

1. 目标字段网格、配色/留白精修和跨端截图基线。
2. 思考档位真实请求路径核查；配置 max、发送参数、服务端确认分开。
3. LCM 已提交压缩事件及前后值，不以摘要 API 结束冒充压缩完成。
4. 账户身份、轮换分摊、真实账单和额度 API。
5. 飞书内历史报表按钮或 Dashboard；当前是 CLI/JSON。
6. 可信旧月份导入和 226 家账号端到端认证；目前不自动回填或推算。

## README 图像规则

SVG 是脚本生成的架构图/当前字段结构示意，配套 JSON 使用合成数据，明确标记**不是飞书客户端截图**。原 AI 设计图仅代表目标，不再作为“已实现效果”的宣传截图。

本轮文档和示意资产更新不改动运行逻辑，不需要重启 Gateway。下一步见[设计审查](FOOTER-DESIGN-AUDIT.md)与[计划](FOOTER-V2-PLAN.md)。
