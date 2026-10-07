# 架构(1.0 重写)

目标:让 Hermes 的飞书回复成为一张持续更新的 CardKit 2.0 卡片。设计稿见 [design/card-redesign.html](design/card-redesign.html)。

## 分层与依赖方向

```
hooks ──▶ session ──▶ card (纯函数渲染)
              │  └──▶ details (用量 / 资源 / 账户 → Section, Footer)
              └─────▶ transport (CardKit 客户端、投递台账、节流、媒体)
config ◀── 所有层只读
```

下层不得 import 上层。`card` 与 `details` 不做网络 I/O 之外的副作用;`card` 完全无 I/O。

| 包 | 职责 | 来源(legacy/) |
|---|---|---|
| `card/` | `TurnView → card JSON`,Markdown 规整,脱敏 | 新写;`markdown`、`redact` 为移植 |
| `details/` | 一轮遥测、历史用量账本、资源、订阅账户 → `Footer` / `Section` | `footer/*`、`host`、`account_*` |
| `transport/` | CardKit 客户端、序号、重试、投递台账、节流、图片、媒体 | `feishu.py`、`delivery.py`、`streaming/flush|image|media`、`card_limits.py` |
| `session/` | 每条消息的状态机、分段、工具追踪、续卡、打断/审批/clarify | `controller.py`、`streaming/*` |
| `hooks/` | 对 Hermes 的唯一接入点(AST 注入表 + 原生观察钩子) | `patcher.py`、`modular_patcher.py`、`native_hooks.py` |
| `config.py` `cli.py` `doctor.py` | 配置、命令行、自检 | 新写 |

## 数据契约

- `card.model`:`TurnView`、`Step`、`Footer`、`Section`、`Metric`、`RenderOptions`。`details` 只产出 `Footer` 与 `Section`,不关心布局。
- `card.render`:`render_streaming(view)`、`render_final(view)`,以及 `status_element/process_element/footer_element/details_element` 供增量更新;元素 id 常量 `STATUS_ID PROCESS_ID ANSWER_ID FOOTER_ID DETAILS_ID`。
- 流式阶段按块对比:新块插在实时状态行之前,回答块用打字机更新,面板局部更新;终态用整卡更新。
- 空内容的 markdown 元素 CardKit 可能丢弃,新增元素用 insert 而不是预占位。

## 不变的行为约束(来自 legacy 测试与线上事故)

1. 单写通道:一张卡片同一时间只有一个写入者,`sequence` 严格递增且成功后才提交。
2. 创建失败交还 Hermes 默认回复;投递结果 `unknown` 不冒充成功,不自动重发。
3. 流式 10 分钟到期:8 分钟轮转,遇 300309 恢复;旧卡保留完整终态。
4. 元素上限 200:接近时拆卡,旧卡封存,仅最后一张带页脚/详情。
5. 打断(A→B→C)、`/stop`、排队后续、审批/clarify 边界、后台任务、Cron 投递均保持语义。
6. 消息被删除/撤回后停止更新。
7. 所有进入卡片的外部文本先脱敏再转义;缺失数据显示“未知”,不补造。

## 接入层(hooks)

Hermes 0.21.x 暂无“流式渲染器”协议;原生钩子只能观察且不带 chat/message id,因此仍以可逆的 AST 注入作为卡片所有者。
重写后注入点改为一张声明式表(锚点、插入片段、回调名),由同一个引擎编译、写入、校验、回滚;原生观察钩子仅用于遥测。
Hermes 已有结构化事件契约(`gateway/stream_events.py`、`render_message_event`、`format_tool_event`),后续可评估用适配器包装替代文本注入。
