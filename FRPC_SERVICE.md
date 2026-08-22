# FRPC Root 开机服务使用说明

这套脚本通过 Root 将 FRPC 注册到 Android 的 `service.d` 开机目录。它不安装 APK，也不依赖应用前台服务。

## 前置条件

- 手机已解锁 Bootloader，并安装 Magisk、KernelSU 或 APatch 之一。
- 手机已经可以通过 ADB 连接，并且 Root 授权可用。
- Windows 已安装 `adb`，且 `adb` 在 `PATH` 中。
- 手机 ABI 支持 `arm64-v8a`、`armeabi-v7a` 或 `x86_64`。
- `app/src/main/jniLibs/<ABI>/libfrpc.so` 已存在。

如果电脑使用 Proxifier，请为 `adb.exe` 添加高优先级 `Direct` 规则，避免 ADB 连接被转发到 HTTP 代理。

## 快速安装

最简单的流程是：先运行设备管理器，再运行安装 BAT：

```bat
scripts\manage-frpc.bat
scripts\install-frpc-service.bat
```

设备管理器可以保存多个设备归档并切换当前归档。`active.json` 只保存当前选中的归档，安装 BAT 不要求再次输入参数。

设备管理器会自动读取在线 ADB 设备。连接多个设备时，可在左下设备列表中选择设备。双击设备即可查看型号、Android 版本、SDK、ABI、Root、USB 调试、TCP ADB、Boot 状态、服务脚本实际路径和采集时间等信息。IMEI 等敏感标识只有 Root 读取成功时才会显示，否则标记为不可用。

在项目根目录打开 PowerShell 或 CMD，执行：

```bat
scripts\install-frpc-service.bat
```

脚本会自动完成：

1. 读取手机 ABI。
2. 生成随机词组名称。
3. 生成 FRPC TOML 配置。
4. 将二进制、配置和启动脚本推送到手机。
5. 使用 `su` 安装并立即启动服务。
6. 将启动脚本注册到 `/data/adb/service.d/`。

安装 BAT 优先使用当前设备归档保存的 ADB 序列号。如果该设备不在线但只有一个在线设备，则自动使用唯一在线设备；如果有多个在线设备，会要求先在设备管理器选择设备。

## 手动安装

只生成部署包，不自动操作手机：

```bat
scripts\personalize-frpc-service.bat
```

脚本会输出一个临时目录，例如：

```text
...\scripts\personalized\service\golden-flame\
```

然后手动执行：

```powershell
adb push "...\golden-flame" /data/local/tmp/
adb shell su -c "sh /data/local/tmp/golden-flame/golden-flame.sh"
```

将命令中的路径和名称替换成脚本实际输出的值。

## 配置参数

默认配置为：

```text
服务器地址：39.107.228.222
服务器端口：7000
本地地址：127.0.0.1
本地端口：5555
远程端口：6004
```

需要修改参数时直接运行渲染器：

```powershell
.\scripts\render-frpc-service.ps1 `
  -Abi arm64-v8a `
  -ServerAddr 39.107.228.222 `
  -ServerPort 7000 `
  -LocalPort 5555 `
  -RemotePort 6004
```

如果 FRP 服务端启用了 token，可以通过环境变量传入：

```powershell
$env:FRP_TOKEN = "你的Token"
.\scripts\render-frpc-service.ps1
```

也可以直接传参：

```powershell
.\scripts\render-frpc-service.ps1 -Token "你的Token"
```

不要把真实 token 提交到 Git 或写入公共文档。

设备归档保存在本机 `scripts\profiles\`，不会提交到 Git。每个归档带有 `createdAt`、`updatedAt`、所选设备序列号和首次安装时的手机信息快照，可以保存多套服务器和端口配置，在设备管理器切换后重新运行安装 BAT。归档中的 token 是明文保存的，请限制该目录访问权限。

## 设备上的路径

每次生成都会使用随机词组，例如 `golden-flame`：

```text
/data/adb/service.d/golden-flame.sh
/data/adb/golden-flame/golden-flame
/data/adb/golden-flame/golden-flame.toml
/data/adb/golden-flame/supervisor.pid
/data/adb/golden-flame/child.pid
/data/adb/golden-flame/schedule.pid
/data/adb/golden-flame/runtime.log
```

设备侧生成的目录、脚本、二进制和配置名均使用随机词组，不包含产品名称。`service.d` 只保存启动脚本，较大的 ELF 文件保存在独立数据目录。

## 查看状态和日志

先查看安装脚本输出的随机名称，然后执行：

```powershell
adb shell su -c "sh /data/adb/service.d/golden-flame.sh status"
adb shell su -c "sh /data/adb/service.d/golden-flame.sh logs 100"
```

检查 FRPC 是否连接成功：

```powershell
adb shell su -c "cat /data/adb/golden-flame/runtime.log"
```

日志超过 256 KiB 时会自动覆盖为新的运行日志，避免无限占用存储空间。

## 停止和重启

停止当前服务：

```powershell
adb shell su -c "sh /data/adb/service.d/golden-flame.sh stop"
```

重新启动：

```powershell
adb shell su -c "sh /data/adb/service.d/golden-flame.sh restart"
```

服务 supervisor 只会管理自己启动的 FRPC 子进程，重复执行 `start` 不会产生第二个 FRPC 实例。

## 定时自检

设备管理器右侧可以勾选“启用 FRPC 定时自检”并设置周期，周期范围为 10 到 2147483 秒。配置会写入生成的服务 SH：

```sh
SCHEDULE_ENABLED='1'
SCHEDULE_INTERVAL='3600'
```

定时器由手机端 SH 在后台运行，到达周期后只结束当前 FRPC 子进程，由同一个 supervisor 自动重新拉起。因此不会创建第二个 FRPC 服务，也不会依赖 Windows 程序常驻。在线读取设备时，管理器会直接解析手机上的 SH，详情中的定时状态和周期以手机实际脚本为准；旧版没有这两个字段的脚本会显示“未读取到配置”。

定时自检只负责周期性重启 FRPC，不会修改 ADB、USB 调试或其他系统设置。若启用日志，定时重启会在随机日志文件中留下 `scheduled child restart` 记录。

## 卸载服务

卸载开机启动入口并停止进程：

```powershell
adb shell su -c "sh /data/adb/service.d/golden-flame.sh uninstall"
```

该命令会删除 `service.d` 启动脚本，但保留数据目录，便于检查日志。确认不再需要后再删除：

```powershell
adb shell su -c "rm -rf /data/adb/golden-flame"
```

将 `golden-flame` 替换为实际随机名称。不要直接删除正在运行的目录，先执行 `uninstall`。

## 与 APK、ADB 模块的关系

- 这套 Shell 服务只负责启动 FRPC。
- 默认生成包会额外附带 ADB 开机恢复脚本，但它和 FRPC 使用两个独立的启动入口。FRPC 服务只负责启动 FRPC，ADB 脚本只负责恢复 `adbd`、USB 调试和 TCP 5555。
- 设备上两种服务分别通过 `/data/adb/service-state`（FRPC）和 `/data/adb/tcp-adb-service-path`（ADB 开机恢复）识别，二者可以单独存在。
- 独立安装的旧 ADB SH 与 FRPC 包内附带的 ADB SH 使用同一个 `/data/adb/tcp-adb-service-path`。新脚本安装时会替换该状态文件登记的旧脚本，因此正常不会并行运行两份；详情页会扫描 `service.d` 中多个 ADB 特征脚本并提示潜在冲突。
- FRPC 日志由设备端实际日志文件判断是否开启，不使用额外标志文件。日志文件使用随机单词且没有 `.log` 扩展名，详情页会显示路径并读取最近 120 行。
- 设备管理器可以取消“包含 ADB 开机恢复脚本”。取消后生成包只包含 FRPC。
- 启用 ADB 脚本时，首次安装或重启 ADB 可能会让当前 ADB 连接短暂断开，这是预期行为。
- Magisk ADB 模块只负责 ADB 恢复时，不会因为本服务自动启动 APK。
- 如果同时启用了 APK 的 FRPC 自启动，应关闭其中一个，否则可能出现两个 FRPC 实例争抢同一个远程端口。Shell 模式下建议关闭 APK 的 FRP 自启动。
- 手机重启后，必须依赖 Magisk、KernelSU 或 APatch 的 `service.d` 机制才能自动启动。

## 图形化管理

运行：

```bat
scripts\manage-frpc.bat
```

图形界面由 `scripts/device_manager.py` 提供，只需要 Windows 的 Python 3.11+ 和内置 Tkinter，不需要安装第三方库。它负责设备归档的新建、编辑、保存、删除和全局搜索；手机端安装、服务启动、停止、重启和卸载仍由现有 BAT 与 Shell 执行。这样关闭图形界面不会影响手机上的服务。

四个 BAT 仍然保留，作为命令行和救援入口：`install-frpc-service.bat` / `personalize-frpc-service.bat` 管理 FRPC，`install-tcp-adb-preauthorized.bat` / `personalize-tcp-adb-preauthorized.bat` 管理单独的 ADB 开机恢复服务。日常多设备、多归档操作建议使用 Python 界面，BAT 不再承担设备归档选择界面。

管理窗口支持：

- 左上显示本机设备归档，单击或双击后在右侧编辑，选中的归档保持蓝色定位。
- 设备归档支持新建、查询、编辑、删除和复制。复制归档只保留 FRPC 参数，会清除旧设备绑定和首次安装记录；再次安装时设备端会生成新的随机服务路径。
- 左下自动监听在线 ADB 设备；点击设备后读取其安装状态、启动脚本、数据目录、二进制和 TOML 配置。
- 点击顶部“搜索...”或在搜索框按回车，会打开独立的全局搜索窗口；窗口会显示搜索进度和全部命中记录，范围包括设备归档字段、设备序列号、型号及当前在线 ADB 设备。双击结果可定位主界面左侧记录并加载详情。
- 查看当前设备的详细系统信息。
- 查看服务状态。
- 启动、停止、重启和卸载服务。
- 在线设备检测到 ADB 开机恢复服务后可卸载 ADB 服务；卸载只移除开机脚本，不停止当前 adbd。
- 安装当前配置。
- “仅 ADB”菜单可以安装随机命名的 ADB 恢复脚本，或选择目录生成安装 SH。
- “FRPC + ADB”菜单可以安装组合服务，或选择目录生成组合安装包。
- 底部状态栏显示当前正在扫描、读取、生成、安装或执行的操作。

在管理窗口点击安装时，安装脚本会明确使用左下列表中当前选中的设备，不会被归档中旧的设备序列号覆盖。直接运行 `install-frpc-service.bat` 时，仍按当前归档或唯一在线设备自动选择。

安装 FRPC 时，当前归档提供服务器、端口和 Token 等参数，在线 ADB 设备只提供目标连接。安装前不会由 Python 预先卸载服务；设备端安装 SH 会读取现有状态文件，在手机上停止并清理旧的同类服务，然后注册新的随机路径。这样不会因为 GUI 预卸载导致 ADB 连接提前中断。

当设备上还没有 `/data/adb/service-state` 时，管理器会显示“未找到已安装的服务”，不会把 adb 的错误输出误当成服务路径。

新生成的 TOML 会以注释保存设备归档名称。点击设备时管理器优先按该名称定位本机归档；旧安装没有名称注释时，则按服务器地址、服务器端口、本地端口、远程端口和 Token 组合匹配。Token 会载入右侧密码输入框，但执行输出只显示“已读取”，不会明文打印。

管理窗口通过 `/data/adb/service-state` 找到当前随机命名的启动脚本，不需要手动输入随机目录名。

## 常见问题

### `permission denied`

确认执行命令使用了 `su -c`，并在 Root 管理器中允许当前 shell 的 Root 权限。

### `binary missing`

确认对应 ABI 的文件存在：

```text
app/src/main/jniLibs/arm64-v8a/libfrpc.so
app/src/main/jniLibs/armeabi-v7a/libfrpc.so
app/src/main/jniLibs/x86_64/libfrpc.so
```

### FRPC 启动后立即退出

优先查看日志：

```powershell
adb shell su -c "sh /data/adb/service.d/golden-flame.sh logs 100"
```

常见原因包括服务器地址或 token 错误、远程端口已被占用、手机本地 `5555` 没有监听，以及网络不可达。

### ADB 显示 `offline`

先确认电脑端没有被 Proxifier 或其他代理程序接管，再执行：

```powershell
adb disconnect <手机IP>:5555
adb connect <手机IP>:5555
adb devices
```

截图中出现过登录密码和 Authorization token，相关凭据应立即更换，不能继续使用。
