# Hermes Lark Streaming

让 Hermes 的飞书回复成为一张持续更新的卡片:状态一行、回答居中、过程和详情各折叠成一行。

[安装](INSTALL.md) · [设计稿](docs/design/card-redesign.html) · [架构](docs/ARCHITECTURE.md) · [更新记录](CHANGELOG.md)

> 1.0 是完全重写。旧版(0.21.x)的三套布局合并为一套,配置键同步精简,旧配置不兼容,迁移见 [升级说明](docs/MIGRATION.md)。

## 卡片长什么样

| 区块 | 运行中 | 完成 | 工具失败 |
|---|---|---|---|
| 状态行 | 蓝点 · 步骤 3/5 · 计时 | 绿点 · 总耗时 | 红点 · N 步失败 · 回答已完成 |
| 过程(工具与思考) | 展开 | 折叠一行 | 展开,失败行浅红并给出原因 |
| 回答 | 打字机追加 | 完整 | 完整 |
| 底栏 | 模型 · 上下文 | 模型 · 上下文占比 · 缓存命中 · 身份标签 | 同完成 |
| 详情(用量/资源/账户) | 不显示 | 折叠一行 | 折叠一行 |

默认折叠时只比纯文本多三行。详情面板里三个分区各自独立开关:

- **用量**:本轮 token、缓存命中(完整覆盖才显示精确值,否则标“下限”)、历史累计。
- **资源**:GPU/显存/内存/磁盘采样(WSL 与 Windows)。
- **订阅账户**:5 小时/每周/每月额度、重置时间、到期时间;缺失的数据不显示,也不补造;整块都没有数据时,该分区不出现。

## 能力

- 流式更新、10 分钟窗口前自动换卡(旧卡封存为“已分页”)。
- 可靠投递:成功后才提交序号,稳定 UUID,投递三态台账;结果不明不会重复发送答案。
- 打断、`/stop`、排队后续、审批与 clarify 边界、后台任务、Cron 结果都以卡片呈现。
- 消息被删除或撤回后自动停止更新;创建失败时交还 Hermes 默认回复。
- 工具参数、错误与输出一律先脱敏再转义;只读采集,不开后台进程,不自动换号。

## 配置

写在 Hermes 的 `config.yaml` 中:

```yaml
streaming:
  enabled: true
  text_size: normal_v2      # 正文字号
  width_mode: default       # default | compact | fill
  process: auto             # auto | open | closed | off
  agent_name: ""            # 底栏身份标签,留空则不显示
  details:
    usage: true
    resources: false
    timezone: Asia/Shanghai
    accounts:
      enabled: false
      allowed_chats: []     # 账户额度必须显式列出会话
```

其余键见 [配置参考](docs/CONFIG.md)。Hermes 自带的 `display.show_reasoning`、`display.show_tool_use` 仍然生效。

## 运行要求

Hermes ≥ 0.21.3(模块化网关布局),Python ≥ 3.11,飞书应用具备 CardKit、消息收发与图片权限。凭据沿用 Hermes 现有配置,不额外保存。

## 开发

```bash
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check hermes_lark_streaming tests
.venv/bin/mypy --explicit-package-bases hermes_lark_streaming
```

## 许可

MIT。灵感来自 [Cheerwhy/hermes-lark-streaming](https://github.com/Cheerwhy/hermes-lark-streaming)、[openclaw-lark](https://github.com/larksuite/openclaw-lark) 与 [hermes-feishu-streaming-card](https://github.com/baileyh8/hermes-feishu-streaming-card)。
