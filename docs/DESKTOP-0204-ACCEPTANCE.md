# 0.20.4 桌面验收与交付范围

验收时间：2026-10-03 UTC。代码：`847ba0f42e2905d6431f9d1b83b70f45fb978c15`。

本次仅更新既有测试话题中的**合成预览卡片**，然后在 Windows 飞书中展开核对。
没有发送新消息、调用模型、修改真实回复、写入生产用量或再次重启 Gateway。
下列截图只裁出合成卡片，不包含聊天列表和生产用量；示例数字不是设备实时状态。

## 当前版本实拍

### 默认折叠

![0.20.4 Windows 飞书合成预览：默认折叠](assets/reference-v1-0204-collapsed.png)

保留工具、资源、正文、模型/用量、身份标签的顺序。三个面板独立折叠，正文不包在统计面板里。

### 工具展开

![0.20.4 Windows 飞书合成预览：工具展开](assets/reference-v1-0204-tools.png)

四列对齐；失败行单独着色，后续成功不会擦掉失败。
`24/24 结束`不是`24/24 成功`，回答完成也不是工具全成功。
下层入口使用“步骤摘要”，明确最近步数和文本长度限制，而非声称是完整原始日志。

### 资源展开

![0.20.4 Windows 飞书合成预览：资源展开](assets/reference-v1-0204-resources.png)

标签在上、数值在下；展开明细使用 GiB，标明 WSL 范围和采样时间。
这是一次快照，不是持续刷新的主机监控；折叠标题仍使用短单位 G 以节省宽度。

### 本轮与历史展开

![0.20.4 Windows 飞书合成预览：本轮与历史](assets/reference-v1-0204-footer.png)

保留服务商、接口、请求思考强度、请求/返回模型、输入含缓存、输出、缓存命中、
API 请求/错误及“首响应（含重试）”。缺失费用明确显示“未提供”，不是零费用。
历史显示今日/月/累计、启用起点、时区和订阅商/模型分组，说明本机账本不是供应商账单。

## 证据分层

| 层级 | 本次结论 | 边界 |
|---|---|---|
| 源码与自动测试 | 1482 项、Ruff、mypy、离线 wheel 已通过 | 见 [修复记录](DESKTOP-HARDENING.md)，不是视觉证据 |
| GitHub | 精确代码 Tests / CodeQL 通过，用户 main 已发布 | 文档提交不要求重新部署 |
| CardKit API | 既有合成实体更新成功 | 消息读取接口返回旧客户端提示占位，未据此判断 UI 成败 |
| 桌面客户端 | 1536×912 窗口，浅色；折叠/工具/资源/Footer 展开可读 | 原生界面实拍，不是网页模拟或重画 |
| 运行环境 | 托管 0.20.4，Gateway running、飞书 connected、doctor 检查及只读数据库健康通过 | 保留 metrics_stale / delivery_unknown 提醒，不删除旧证据 |
| 新版真实模型轮次 | 本次未发起 | 0.20.1/0.20.2 的真实工具与账本测试仍保留原版号 |
| 逐像素相同 | 未作此认证 | 冻结 SVG 保持不变；原生字号、客户端缩放与画布像素并不等同 |
| 手机 | 用户明确排除 | 不是当前交付阻塞项 |

[机器可读验收范围与图片哈希](assets/desktop-0204-client-checks.json) ·
[部署证据](assets/desktop-hardening-deployment-checks.json)

## 计划核对

- 已交付：研究目录、接口矩阵、任务计划、架构与五张冻结 V1 图、新卡片代码、
  历史用量、测试、用户 main 发布及托管部署。
- 226 个目录入口及 45 个 Hermes 静态配置走统一采集/显示路径；Hermes canonical
  与六类协议有测试。目录覆盖、协议测试与真实账号验证分别记录，
  不将这些数字解释为全球提供商总数或逐账号联网验证。
- 复核 models.dev 固定提交时，新增 `teamorouter` 目录只有 logo，尚无提供商定义；
  根目录 logo 也不是提供商，因此没有把目录项数量膨胀为适配数量。
- 本轮结束代码扩展；账户余额、真实费用、全新仪表盘等独立功能不插入本轮。
  剩余证据边界就是上表，不以反复修改设计基准来制造“完全一致”。

[详细计划](FOOTER-V2-PLAN.md) · [提供商覆盖](PROVIDER-COVERAGE.md) ·
[冻结 V1 与历史验收](REFERENCE-V1.md)

## 原始目标逐项复核（2026-10-04 Asia/Shanghai）

| 原始交付要求 | 当前权威证据 | 判定 |
|---|---|---|
| 网络/GitHub 研究、列出提供商与接口 | 两份日期快照、完整 226 行目录、45 个 Hermes 配置与官方协议链接 | 已交付快照；不是声称存在全球穷尽注册表 |
| 提供商适配 | `test_every_catalog_id_uses_same_canonical_path` 覆盖目录与 Hermes 配置的并集；`test_all_catalog_ids_use_same_native_reference_path` 覆盖 226 个目录 ID | 统计/展示已覆盖；不代表逐账号联网认证 |
| 协议口径 | `test_protocol_usage` 验证 canonical 和六类 raw usage；生产 `TurnFooter` 消费 Hermes 规范化 usage | 通过；raw parser 的 fixture 不冒充生产直接连接六套 SDK |
| 详细计划、架构与设计图 | `FOOTER-V2-PLAN.md`、`runtime-overview.svg`、五张冻结 V1 SVG | 文件已交付，基准未替换 |
| 新布局与运行态代码 | `cardkit/reference.py`、`footer/runtime.py`、`streaming/runtime_footer.py`；原生桌面实拍与 CardKit 回执 | 已实现并验证所列范围 |
| 历史用量 | SQLite 账本、CLI 分组、时区/月界、部分字段、去重与主辅隔离回归；0.20.2 实际账本核对 | 已实现；未补造过去数月数据 |
| 重构后保持 Hermes 单写卡链路 | `test_actual_hermes_execution_chain_does_not_repeat_or_mutate_calls`、controller/sequence 与兼容矩阵 | 自动检查通过，未增加独立推理或投递客户端 |
| GitHub main 与文档同步 | 代码 `847ba0f`，桌面证据 `7d2d460`；二者 Tests / CodeQL 均成功 | 已发布；后续文档修正不改运行代码 |
| 托管部署 | 独立部署清单、PM 导入版本、live Gateway socket、doctor、数据库只读检查 | 0.20.4 已部署，Gateway running / 飞书 connected |
| 最新版本真实模型整轮 | 当前最近实测是 0.20.2；0.20.4 只有合成服务端及桌面验收 | **尚未验证**；待确认在原测试话题发送单条只读测试 |
| 与设计图逐像素相同 | 冻结 SVG 和原生截图分别保留 | **尚未认证**；结构对应不等于像素证明 |

本次专门重跑 7 个测试文件共 **674 项，通过**：`test_footer.py`、
`test_reference_layout.py`、`test_usage_history.py`、`test_footer_execution.py`、
`test_footer_runtime.py`、`test_desktop_audit_fixes.py`、`test_footer_cache_boundaries.py`。
这组检查直接覆盖上述适配、口径、布局和账本要求，不代替最后两行的独立验收。
因此代码交付和当前部署已经验证，但不将完整目标标为无条件验收完成。
