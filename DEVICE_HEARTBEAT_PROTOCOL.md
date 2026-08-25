# 纯 Shell / Linux 端设备心跳协议

本项目的 FRP 纯 Shell 端只使用设备管理协议，不复用 Android Hook 项目的卡密校验链。

## 接口职责

- `POST /device/status`：安装或首次运行时查询设备是否已经注册。
- `POST /device/register`：设备首次出现时登记设备信息。
- `POST /device/heartbeat`：周期性短连接；更新在线时间，并返回配置和 `pending_commands`。
- `POST /device/ack`：确认已经处理的命令。
- `POST /device/upload`：可选结果上传接口；模板只定义函数，不会自动上传。

## 纯 Shell 的工作循环

```text
开机运行 service.d 脚本
  -> status
  -> 未注册时 register
  -> 每次定时段执行 heartbeat
  -> 读取 pending_commands
  -> 只处理 FRP 自己声明的命令
  -> ack
```

`heartbeat` 是短 HTTP 请求，不是长连接。服务器不能主动连接一个已经离线或被系统杀掉的客户端；命令会留在 `pending_commands` 中，等待客户端下次上线。

## Android heartbeatVerify 的边界

Android Hook 项目的 `heartbeatVerify` 属于另外的卡密/授权平台链路。纯 Shell 端不调用它，也不依赖它。两套状态分别维护：

- 设备在线状态：`device/heartbeat`
- 外部授权状态：Android 自己的 `heartbeatVerify`

## 命令安全边界

纯 Shell 端只允许处理受管的 FRP 命令。当前模板支持 `cleanup` 类型，并读取：

```json
{
  "type": "cleanup",
  "payload": {
    "code": 1
  }
}
```

- `code=1`：删除本 FRP 安装目录、FRP service.d 脚本和状态文件。
- `code=2`：在 code=1 基础上，删除本次安装的 ADB 恢复脚本。
- 其他数字：拒绝执行并 ACK 为 `cleanup_rejected`。

清理路径必须满足 `/data/adb/*` 前缀，只能删除本程序创建的路径，不能删除整个 `/data/adb`、用户数据、其他 Magisk 模块或系统文件。

为了避免在没有 JSON 解析器时误匹配命令，模板只有在设备上存在 `jq` 时才处理结构化命令；没有 `jq` 时不会执行清理命令，也不会伪造成功 ACK。

## 多端扩展

服务端协议可以被 Android、Linux/Shell、Windows 和 iOS 共用。平台差异应该放在客户端适配层：

- 身份采集：由各平台提供 `device_id`、型号和版本。
- 定时调度：Android Service/WorkManager、Linux service.d/systemd、Windows Task Scheduler、iOS 后台任务。
- 命令能力：客户端只执行自己声明支持的命令。
- 结果确认：统一使用 `/device/ack`。

不要把 Android 专有的授权接口、IMEI、Xposed 逻辑塞进纯 Shell 端。
