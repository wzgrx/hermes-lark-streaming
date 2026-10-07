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

Hermes 升级后、或本插件的钩子内容变化后,先在网关空闲时 `uninstall` 再 `install`:引擎会重写不一致的标记块,并拒绝在锚点缺失或不唯一时写入任何文件。

## 回滚

```bash
"$HERMES_LAUNCHER" --run-module hermes_lark_streaming uninstall
hermes plugins disable hermes-lark-streaming
hermes gateway restart
```

`uninstall` 只移除带标记的注入块;`restore` 在标记损坏时才用 `.hermes_lark.bak` 备份还原。从 0.21.x 升级见 [docs/MIGRATION.md](docs/MIGRATION.md)。
