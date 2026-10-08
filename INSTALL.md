# 安装与更新

需要 Hermes ≥ 0.21.3(模块化网关)、Python ≥ 3.11。Hermes 的包管理器为每代依赖建独立环境,请通过托管插件安装,不要只装进 `venv`。

## 安装

在交互式终端中执行并确认依赖:

```bash
hermes plugins install wzgrx/hermes-lark-streaming --enable --force
hermes plugins doctor hermes-lark-streaming --ci
hermes pm install
```

用托管启动器检查并安装钩子(网关空闲或停止时):

```bash
HERMES_LAUNCHER="$HOME/.local/bin/hermes"
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming verify
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming install
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming status
hermes gateway restart
```

飞书凭据沿用 Hermes 现有配置或受保护的 `.env`,不要重复写入。配置见 [docs/CONFIG.md](docs/CONFIG.md)。

## 更新

```bash
hermes plugins update hermes-lark-streaming
hermes pm install
```

插件更新后在网关空闲时运行 `install`:引擎会重写内容变化的标记块,并拒绝在锚点缺失或不唯一时写入任何文件。`verify` 会提示已注入的钩子是否落后于当前插件版本。

## 随 Hermes 更新

Hermes 保持纯上游,不带本地提交;本插件的行为全部在插件内:钩子由引擎注入,日志脱敏、OpenCode Go 403 轮换、`skills.index_mode: names_only` 通过插件入口注册。更新 Hermes 的完整流程:

```bash
hermes gateway stop
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming uninstall   # 还原被注入的 5 个文件
hermes update --no-gateway-restart
hermes pm install
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming verify      # 锚点变化时在这里失败,不会写入任何文件
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming install
hermes gateway start
```

`verify` 失败说明上游改动了注入锚点,修 `hermes_lark_streaming/hooks/table.py` 后再 `install`。验收可运行 `python scripts/live_acceptance.py <chat_id>`,它用真实飞书接口走一轮模拟对话,不需要人工发消息。

## 回滚

```bash
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming uninstall
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

`uninstall` 只移除带标记的注入块;`restore` 在标记损坏时才用 `.hermes_lark.bak` 备份还原。从 0.21.x 升级见 [docs/MIGRATION.md](docs/MIGRATION.md)。
