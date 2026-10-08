# 从 0.21.x 升级到 1.0

1.0 完全重写,布局只有一套。旧配置键不再读取,请按下表改写后再重启网关。

| 旧键 | 新键 |
|---|---|
| `layout: reference` / `reference-v2`、`footer.mode`、`header.enabled`、`footer.fields/show_label/text_size` | 删除(布局固定) |
| `footer.enabled: false` | 不再支持;详情面板按 `details.*` 单独关闭 |
| `panel_expanded: true` | `process: open` |
| `body.text_size` | `text_size`(旧写法仍可读) |
| `footer.history.*` | `details.history.*`,时区改为 `details.timezone` |
| `footer.accounts.*` | `details.accounts.*` |
| `resources.enabled` | `details.resources` |
| `footer.details: false` | `details.usage/resources/accounts` 分别设为 false |
| `history_compaction.*`、`callback_ttl_sec` | 删除 |
| `agent_name` | 删除(页脚不再显示身份标签,配置里留着也会被忽略) |
| `bots`、`width_mode`、`adaptive_backpressure`、`card_ttl_sec` | 不变 |

## 步骤

1. 网关空闲时停止:`hermes gateway stop`。
2. 卸载旧钩子:`hermes --run-module hermes_lark_streaming uninstall`(新引擎也能识别并清理 0.x 的标记块)。
3. 更新插件并 `hermes pm install`。
4. 改写 `config.yaml`,运行 `verify`、`install`、`status`。
5. `hermes gateway start`,发一条消息核对卡片。

旧卡片保留原样,不会被重写。回滚:`restore`,并重新安装旧版本(标签 `legacy-0.21.5`)。

## 行为变化

- 工具与思考合并为一个“过程”面板;成功工具的输出不再显示,只保留摘要;失败时显示原因。
- 资源、账户、用量合并为一个“详情”面板,仅在回答结束后出现。
- 流式阶段不再显示详情,状态行带实时计时。
- 不再支持 Hermes 旧的单文件 `gateway/run.py` 布局;需要模块化网关(0.21.3+)。
