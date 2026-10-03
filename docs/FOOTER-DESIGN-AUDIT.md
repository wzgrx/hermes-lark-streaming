# Footer 视觉差异与官方工具链审查

> 后续变更：用户将详情要求更新为“好看、紧凑、高信息密度、减少纵向占用”。0.18.0 已实现紧凑布局并检查真实桌面合成预览；旧三列目标和五组长表不再作为当前详情验收标准。部署及其他原计划目标保持独立，详见 [紧凑设计与当前证据](FOOTER-COMPACT.md)。
2026-10-03 · 审查代码 `1d3cb3c` / 0.17.1 · **视觉验收未通过**。

## 结论

现有设计中的两行摘要、蓝色标题、分组、字段对齐和折叠详情，主要属于原生 Card JSON 2.0 的能力范围。当前差异首先是实现没有完整照稿，而不是少装了一个 SDK。

Node SDK、Python SDK、CLI 都可以提交卡片数据；最终布局由飞书客户端按组件协议渲染。更换调用语言不等于更换客户端渲染器。原生交互、跨端自适应和任意设计图片的逐像素一致，是三个不同的验收目标。

本轮比较依据：原始设计图、实际 `footer/render.py`、0.17.1 发出的 Card JSON、官方源码/文档、此前服务端回执，以及本轮直接打开 Windows 飞书窗口后观察的折叠/展开画面。实机确认：黑色分组小标题、两列字段、值没有独立对齐；真实卡片的思考档位显示“未提供”。视觉 gate 保持失败。私人聊天窗口截图不放入公开仓库；README 使用明确标记的合成数据结构图。

## 设计稿与当前实现的差异

| 项目 | 设计稿 | 0.17.1 实现 | 处理方向 |
|---|---|---|---|
| 详情排列 | 分组标题／字段名／数值，三列 | 两列；右侧是粗体字段名加数值的 Markdown | 实现真正的字段网格；长模型名要逐行保持对齐 |
| 分组标题 | 蓝色、明显分级 | 普通粗体，沿用 footer 字号 | 统一标题颜色和字号 token |
| 分隔与留白 | 水平分隔、细竖线、整齐节奏 | 水平 hr；没有竖线；部分间距使用客户端默认 | 用实际受支持的容器样式替代绘图线条，固定间距档位 |
| 模型展示 | 简洁的显示名 | 请求/返回原始模型 ID | 摘要友好名与详情原始 ID 分离，保留真实身份 |
| 展开图 | 已展开的演示状态 | 默认折叠，需点击“本轮详情” | 比较相同展开状态；默认折叠是原计划要求 |
| 统计数字 | 固定的示例数据 | 实际值或“未提供” | 布局可对齐，数值不照抄；不得填造费用/压缩状态 |
| 思考档位 | 示例标注 max | 本轮观察到真实卡片显示“未提供” | 检查实际请求信封/采集路径；缺少展示字段不等于模型实际关闭思考 |
| 图片右侧说明框 | 讲解注释 | 不属于消息卡片内容 | 不复制进每条消息 |
| 宽度与字体 | 固定画布、绘图字体 | 客户端宽度、平台字号与字体、缩放 | 以确定的客户端/宽度/主题作为截图基线 |

`text_size: normal`、`width_mode` 等只能调整协议暴露的样式。原生卡片不是任意 HTML/CSS 页面；局部 HTML 文本语法也不等于完整浏览器布局。

## 三条技术路线

| 路线 | 要增加什么 | 优点 | 代价与边界 | 建议 |
|---|---|---|---|---|
| 原生 CardKit 2.0 | 重做 JSON 布局；官方卡片搭建工具预览；截图验收 | 保留流式、折叠、复制文字、跨端交互 | 字体和组件行为由客户端管理；不承诺所有屏幕逐像素一致 | **Footer 首选** |
| 图片型统计摘要 | 本地 SVG/HTML 渲染为 PNG，上传图片并使用 `img` | 图片内部构图固定，最接近设计画布 | 缩放/压缩仍影响屏幕像素；图片内文字和折叠不是原生交互；不适合每个流式增量重画 | 可用于月报导出，不替换主卡片 |
| 网页用量面板 | Web 前端、托管、访问控制、卡片跳转入口 | HTML/CSS、图表、筛选与历史报表自由度高 | 新增服务与运维；显示在网页而非聊天卡片内部 | 历史用量增长后按需做 |

推荐保留现有 `lark-oapi` + Hermes 单投递 owner，不为了外观新增第二条 WebSocket 或 Node sidecar。

## 官方 Node SDK：有用，但不是换肤插件

核实 [node-sdk 源码快照](https://github.com/larksuite/node-sdk/tree/394c83092395a51402ee408b751d7f9fb05f5518)，`package.json` 标记 `1.74.0`（这是该源码版本，不据此断言 npm 最新发布版本）。

- [Channel 文档](https://github.com/larksuite/node-sdk/blob/394c83092395a51402ee408b751d7f9fb05f5518/docs/channel.zh.md)：统一事件、消息归一化、发送、流式与卡片回调。
- [sender.ts](https://github.com/larksuite/node-sdk/blob/394c83092395a51402ee408b751d7f9fb05f5518/channel/outbound/sender.ts)：通过 `cardkit.v1.card.create`、元素更新和 settings API 工作。
- [markdown-stream.ts](https://github.com/larksuite/node-sdk/blob/394c83092395a51402ee408b751d7f9fb05f5518/channel/outbound/streaming/markdown-stream.ts)：构造 schema 2.0 数据及流式控制，不是在飞书内安装浏览器渲染引擎。
- 对本项目的价值：参考队列、流式控制与事件规范；迁移 transport 必须另做投递/打断/审批/去重回归，而非直接并行启用。

## 官方 CLI：设计规范与操作工具，不是客户端截图器

核实 [CLI 源码快照](https://github.com/larksuite/cli/tree/7beffb086d7fa3c5b843d8affa7c089f49cfc65e)。本机 `lark-cli 1.0.96`，查询到官方最新 Release 为 [v1.0.97](https://github.com/larksuite/cli/releases/tag/v1.0.97)。这次审查保持工具与 Gateway 环境不变；版本差异没有解释当前两列/三列实现差异。

- [官方卡片样式指南](https://github.com/larksuite/cli/blob/7beffb086d7fa3c5b843d8affa7c089f49cfc65e/skills/lark-im/references/card/lark-im-card-style.md)：层级、分组、间距、颜色和结构自检。
- [Card 2.0 schema 指南](https://github.com/larksuite/cli/blob/7beffb086d7fa3c5b843d8affa7c089f49cfc65e/skills/lark-im/references/card/card-2.0-schema.md)：容器、字号 token、浅深色 token、宽度模式。
- CLI 适合读取接口/schema、发送测试、监听回调、整理测试证据。其卡片转文本能力不等于截图，更不等于恢复客户端像素。
- [PR #1198](https://github.com/larksuite/cli/pull/1198) 的“complete card message format”改进的是读取消息时的文本转换、字段保留与折叠内容展开，不是为聊天客户端增加任意 CSS。

## Issue / PR 相关性判定

| 来源 | 核实状态 | 与本问题的关系 |
|---|---|---|
| [node-sdk #79](https://github.com/larksuite/node-sdk/issues/79) | closed | 卡片回调类型支持，不是页脚布局方案 |
| [node-sdk #188](https://github.com/larksuite/node-sdk/issues/188) | open | 报告加急卡片锁屏通知出现兼容提示；属于通知场景，不据此诊断本机客户端过旧 |
| [node-sdk #189](https://github.com/larksuite/node-sdk/issues/189) | closed | 流式重复边界字符丢失，属于内容完整性；不是三列布局问题 |
| [cli #1198](https://github.com/larksuite/cli/pull/1198) | merged，2026-06-03 | 读取卡片的文本转换改进，不是截图或视觉验收 |

Issue 的报告、关闭状态不自动证明本项目受影响或已经修复；本轮没有凭标题移植补丁。

## 重新确定验收流程

1. **先冻结内容与样式契约**：默认/展开/失败/停止四个状态，已知/未知/部分用量，短/长模型名。金额和压缩无来源时保留明确缺失。
2. **JSON 即设计真源**：由实际 `build_footer` 生成 fixture；在[官方卡片搭建工具](https://open.feishu.cn/tool/cardbuilder)导入预览，避免先画任意图片再声称已经实现。
3. **原生布局实现**：统一 token、分组标题、字段/值网格、图标与间距。预算测试涵盖完整正文 + Footer，保留单卡 200 元素与本项目更保守的大小门禁。
4. **发送与 API 回归**：创建、流式、关闭、终态更新，确认 receipt；API 成功只通过服务端 gate。
5. **真实截图验收**：同一 JSON，在已记录版本/窗口宽度/缩放/主题的飞书桌面端检查折叠与展开，另测移动端窄屏。浏览器自绘 SVG/HTML 预览仅用于文档，不替代客户端截图。
6. **部署 gate**：回归与截图均通过后才将视觉任务标为完成；保持回滚点、原模型配置及历史数据库。

本轮交付研究、文档校正和可复现示意资产；没有将新的三列重构或网页面板描述成已部署功能。

## 官方组件参考

- [Card JSON 2.0](https://open.feishu.cn/document/feishu-cards/card-json-v2-structure)
- [分栏](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/containers/column-set)
- [折叠面板](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/containers/collapsible-panel)
- [Markdown](https://open.feishu.cn/document/feishu-cards/card-json-v2-components/content-components/rich-text)

开放平台部分页面是动态页面，本轮字段核对同时使用上方冻结的官方 CLI 仓库组件参考，不将空页面抓取当作完整阅读。
