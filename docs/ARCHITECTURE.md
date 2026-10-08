# 架构

目标:让 Hermes 的飞书回复成为一张持续更新的 CardKit 2.0 卡片。卡片样式见 README 截图。

## 分层与依赖方向

```
hooks ──▶ session ──▶ card (纯函数渲染)
              │  └──▶ details (用量 / 资源 / 账户 → Section, Footer)
              └─────▶ transport (CardKit 客户端、投递台账、节流、媒体)
config ◀── 所有层只读
```

下层不得 import 上层。`card` 与 `details` 不做网络 I/O 之外的副作用;`card` 完全无 I/O。

| 包 | 职责 |
|---|---|
| `card/` | `TurnView → card JSON`(时间线、页脚、详情面板、定时任务卡片),Markdown 规整,脱敏 |
| `details/` | 一轮遥测、历史用量台账、资源采样、订阅账户 → `Footer` / `Section` |
| `transport/` | CardKit 客户端、序号通道、重试、投递台账、运行中卡片登记、节流、图片、媒体 |
| `session/` | 每条消息的状态机、时间线块、工具追踪、换卡、打断/审批/clarify、定时任务与后台投递 |
| `hooks/` | 对 Hermes 的唯一接入点(声明式 AST 注入表 + 引擎 + 原生观察钩子) |
| `compat/` | 原先打在 Hermes 上的本地补丁:日志脱敏、OpenCode Go 403 轮换、技能索引只列名称 |
| `config.py` `__main__.py` | 配置解析、`--run-module` 命令(verify / install / uninstall / status) |

## 数据契约

- `card.model`:`TurnView`、`Step`、`Footer`、`Section`、`Metric`、`RenderOptions`。`details` 只产出 `Footer` 与 `Section`,不关心布局。
- `card.render`:`render_streaming(view)`、`render_final(view)`、`streaming_elements(view)`(带 id 的全部元素,供按 id 对比)、`details_element`;实时状态行 `STATUS_ID`,块元素 id 为 `块键_卡片键`(卡片键按消息与换卡代数生成,飞书按元素 id 记住面板展开状态)。
- 流式阶段按块对比:新块插在实时状态行之前,回答块用打字机更新,面板局部更新;终态用整卡更新。
- 空内容的 markdown 元素 CardKit 可能丢弃,新增元素用 insert 而不是预占位。

## 不变的行为约束(来自 legacy 测试与线上事故)

1. 单写通道:一张卡片同一时间只有一个写入者,`sequence` 严格递增;飞书明确拒绝时序号可重用,结果不明(超时、取消)时跳过该序号,遇 300317 跳号重试一次。
2. 创建失败交还 Hermes 默认回复;投递结果 `unknown` 不冒充成功,不自动重发。
3. 流式 10 分钟到期:8 分钟轮转,遇 300309 恢复;旧卡保留完整终态。
4. 元素上限 200:接近时拆卡,旧卡封存,仅最后一张带页脚/详情。
5. 打断(A→B→C)、`/stop`、排队后续、审批/clarify 边界、后台任务、Cron 投递均保持语义。
6. 消息被删除/撤回后停止更新。
7. 所有进入卡片的外部文本先脱敏再转义;缺失数据显示“未知”,不补造。
8. 卡片不会永远停在“处理中”:运行中的回合不按时长清理(只清理已结束或静默 6 小时的会话,后者收尾为“已停止”);会话失败时仍尝试一次终态更新;挂上的卡片登记在 `state/hermes-lark-streaming-open-cards.json`,收尾后删除,网关被杀或崩溃遗留的卡片在下一条消息到来时被标记为“已中断”(保留已显示的内容)。
9. 不阻塞网关事件循环:飞书令牌在线程里预取(SDK 的异步调用会同步刷新令牌);`nvidia-smi` 卡死时等待有上限;所有写入共用一个退避时间,限流时放慢刷新。

## 接入层(hooks)

Hermes 0.21.x 暂无“流式渲染器”协议;原生钩子只能观察且不带 chat/message id,因此仍以可逆的 AST 注入作为卡片所有者。
重写后注入点改为一张声明式表(锚点、插入片段、回调名),由同一个引擎编译、写入、校验、回滚;原生观察钩子仅用于遥测。
Hermes 已有结构化事件契约(`gateway/stream_events.py`、`render_message_event`、`format_tool_event`),后续可评估用适配器包装替代文本注入。
