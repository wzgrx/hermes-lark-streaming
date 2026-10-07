# 配置参考

所有键都在 Hermes `config.yaml` 的 `streaming:` 下。卡片结构与凭据在网关启动时读取一次,修改后需在空闲时重启网关;`display.*` 与 `streaming.details` 由一秒缓存热读取。

## 卡片

| 键 | 默认 | 说明 |
|---|---|---|
| `enabled` | `false` | 总开关 |
| `text_size` | `normal_v2` | 回答正文字号(`normal_v2` `heading` `notation` `normal` `small` `large`) |
| `width_mode` | `default` | `default` `compact` `fill` |
| `process` | `auto` | 过程面板:`auto` 运行或失败时展开,`open` 常开,`closed` 常闭,`off` 不显示 |
| `agent_name` | 空 | 底栏身份标签(最多 30 字符) |
| `card_ttl_sec` | `600` | 会话残留清理时限 |
| `rollover_sec` | `480` | 卡片存活到此秒数后换新卡,上限 570(飞书流式窗口约 10 分钟) |
| `adaptive_backpressure` | 开 | `{enabled, min_ms: 100, max_ms: 1500}`,飞书限频时自动放慢刷新 |
| `bots` | 空 | 多机器人:`{default, chat_bindings: {chat_id: bot_id}, items: {bot_id: {app_id_env, app_secret_env, base_url}}}`,只写环境变量名 |

Hermes 自带开关继续生效:`display.show_reasoning`、`display.show_tool_use`(也可在 `display.platforms.feishu` 下单独设置)。

## 详情面板 `details`

```yaml
streaming:
  details:
    usage: true               # 本轮 + 历史累计
    resources: false          # GPU / 显存 / 内存 / 磁盘
    timezone: Asia/Shanghai   # 日/月边界与账户时钟
    allowed_chats: []         # 非空时,所有分区只在这些会话显示
    history:
      enabled: true           # 记录本地用量账本(历史累计需要)
      path: ""                # 默认 <HERMES_HOME>/state/card-usage.sqlite3
      show_models: true
      provider_labels: {}
    accounts:
      enabled: true
      allowed_chats: [oc_xxx] # 账户额度必须显式列出会话,留空则处处隐藏
      auto_detect: false      # 仅在当前提供商的已知凭据名中发现
      accounts:
        - id: go-main
          label: Go 主账户
          provider: opencode-go
          key_env: OPENCODE_GO_API_KEY

    pricing:                  # 可选:按模型的每百万 token 单价,用于显示 💸 费用;不填就不显示
      deepseek-v4-flash: {input: 1.0, output: 2.0, cache_read: 0.1, currency: "¥"}
```

- 费用只按你填写的单价计算(输入扣除缓存部分按 cache_read 计),不内置任何价格。
- 只读采集:不开后台进程,不自动换号;凭据只写环境变量名。
- 缺失的数据不显示,也不补造;整块都没有数据时,该分区不出现。订阅到期与 Key 到期分开显示;“重置≠到期”。
- 缓存命中率只有在请求全部覆盖时才给精确值,否则标“≥”下限或“未知”。

## 校验

`python -m hermes_lark_streaming status` 查看钩子安装状态,`verify` 检查当前 Hermes 是否可注入。
