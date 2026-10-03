# 0.19.0：真实事件驱动的运行态 Footer

状态图沿用原图 3 的六类交互；本轮详情沿用后续确认的紧凑上下布局。只改底部状态与详情，正文、工具记录和 Hermes 原生确认操作保持原有职责。

![代码对应运行态结构示意，合成数据，非客户端截图](assets/footer-runtime-states.svg)

## 事件与显示口径

| 事件证据 | 底部显示 | 明确区分 |
|---|---|---|
| 新卡或主请求开始 | 处理中、累计耗时 | 不把已发请求当成已收到回答 |
| 原生回答/思考增量 | 正在接收回答/思考 | 用量等最终响应，正文显示开关保持原语义 |
| 已有工具 tracker 的运行项 | 执行工具、工具名、已完成数量 | 不在 Footer 复制命令参数、输出或错误正文 |
| 进入审批/clarify | 等待确认，提示在 Hermes 确认消息中操作 | 仅更新 Footer，正文仍暂停；不接管批准结果 |
| 精确关联的 `pre_auxiliary_call(aux_task=compression)` | 整理上下文：摘要请求进行中 | 不把辅助请求输入估算当完整压缩前上下文 |
| 同一摘要请求返回 | 摘要已返回，等待后续主请求 | 不宣称压缩已提交；失败只标记摘要请求失败 |
| 后续主请求或原生主流恢复 | 恢复处理/回答状态 | 会话存储 ID 轮转时原生流可恢复阶段，但不借此拼接用量身份 |
| 新主请求的 provider 确实变化 | 已切换服务商、前后名称 | 不推测切换原因、余额或订阅额度 |
| 单次 `api_request_error` | 请求失败，等待后续处理；可用时显示错误类型 | 与整轮最终失败分开；不保留错误正文 |
| Hermes 整轮失败/停止 | 沿用最终失败/停止 Footer | 已有回答保留，不染红整张卡 |

来源：[Hermes 辅助调用契约](https://github.com/NousResearch/hermes-agent/blob/main/agent/auxiliary_hooks.py)、[官方折叠面板](https://github.com/larksuite/cli/blob/main/skills/lark-im/references/card/components/collapsible_panel.md)。公共辅助事件只证明请求生命周期，未包含压缩提交结果。完整 LCM 压缩前后值继续等待明确可验证的提交事件，不读取日志文本或轮询数据库推断。

## 实现

- `footer/runtime.py`：有锁、有界、无正文的阶段状态及纯 Card 2.0 渲染；辅助请求去重最多保留 256 个标识。跨会话、过期和孤立事件忽略。
- `streaming/runtime_footer.py`：复用原有 `FlushController` 与 sequence，不启动第二个投递 owner。字段变化限速约 2 秒，阶段变化优先；长时间无增量时每 5 秒刷新耗时。
- 每会话仅一个定时 handle。终态/清理取消；classic 或 disabled 模式不创建定时器、不增加 Footer 更新请求。
- `loading_icon` 保持非空 Markdown 类型及稳定 ID，正文继续插入它之前。详情只局部更新 `elements`，不重写 `expanded`，保留阅读者展开状态。
- 初始化、缺失元素重建、时间轮转与拆卡均使用相同运行态构造。缺失详情的重建会重放正文，不误将正文 reasoning 标成已更新。
- 元素计数仍由原有 Footer reserve 统一预留，避免双计数；手动确认拆卡期间暂停新调度，保持单 writer。
- clarifying 拆卡会清理当前卡的工具 tracker；新增累计基数保证逻辑轮次工具总数不随换卡归零。
- 失败更新不会推进 sequence 或确认显示成功；Footer 重试至少退避 5 秒。审批卡长期等待遇到流关闭后停止局部更新，确认后的原拆卡/收尾流程继续负责投递。

## 配置与依赖

沿用既有配置，无新运行依赖、模型调用、账号轮询或核心 AST patch seam：

```yaml
streaming:
  footer:
    enabled: true
    mode: enhanced
    details: true
```

`details: false` 仅省略详情；`mode: classic` 保留原布局。Footer 实时读数覆盖已返回的主请求，标注其不是最终总量。观察过摘要请求后，详情注明“压缩提交待确认”，不再与“压缩尚未观测”混淆。

## 2026-10-03 验证证据

- 新增 **56 项**运行态回归：全部阶段、元素预算、标识和正文脱敏、去重/乱序、辅助流不混入主请求用量、暂停期间正文不动、序号和退避、缺失元素恢复、定时 handle 清理、经典模式、拆卡累计工具数、flush 并发互斥。错误类型兼容当前 Hermes 的 `error.type` 与旧平铺字段，使用标识符白名单，拒绝保留错误正文。
- Ruff / mypy（44 源文件）通过；**1139 项全量测试通过**，仅 2 条既有 SDK 弃用警告。GitHub CI 与本地证据分别核对。
- **真实 CardKit API**：创建未附加聊天的合成卡片，七次阶段局部更新均被接受，正文仍可插入 `loading_icon` 前，关闭流与最终失败卡更新均被接受。初始卡 31 元素 / 6787 JSON 字节。
- 探针没有聊天投递、模型 API 调用或 Gateway 重启；探针指标使用隔离临时文件，不覆盖生产指标。
- 此证据证明真实 API 接受，不代替真实 Gateway 事件到飞书端的视觉/交互验收。后续已完成以下托管部署，运行态桌面检查及窄屏/主题验收继续单列。

## 托管部署：已完成健康检查

2026-10-03，部署代码提交 [`ef936587`](https://github.com/wzgrx/hermes-lark-streaming/commit/ef936587da3ed5b10753954540b4e747e53b542d)。[Tests](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37102301766) 与 [CodeQL](https://github.com/wzgrx/hermes-lark-streaming/actions/runs/37102301756) 成功后，连续复查 Gateway 空闲，保存受限访问回滚副本，使用 `--expected-revision` 发布、PM 同步、doctor/verify/install/status，再启动 Gateway 并恢复维护定时器。

- Gateway `running`、飞书 `connected`、当前活跃任务 0。
- durable launcher 的模块导入确认 **0.19.0** 来自新的 PM `workspace/plugin-sources` 快照，而非开发目录；44 个 Python 源文件逐字节匹配已测试源码。直接执行裸进程 Python 未建立 PM 作用域，该方式不构成版本验收证据。
- 9 项 doctor 检查通过；使用账本只读 `quick_check=ok`。原配置哈希未变化，Hermes 核心与 LCM 提交及核心工作区未变化。
- 当前进程日志没有 Python traceback、模块导入异常或 Footer 适配失败标记。
- 仍保留 `metrics_stale` 与 `delivery_unknown` 两条已知提示：新进程尚无实发轮次，旧指标待刷新；此前不确定投递记录不自动重发。没有将这两项清零伪装成功。
- 尚待真实 Gateway 新轮次与客户端运行态/展开保持验证；不得据以上健康检查宣称完整视觉验收通过。
