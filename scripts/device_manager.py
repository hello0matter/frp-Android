#!/usr/bin/env python3
"""FRPC/ADB 配置与设备管理器。

只使用 Python 标准库。BAT 和设备端 Shell 仍然是实际执行层，本程序负责
设备归档 CRUD、全局搜索、设备选择以及调用这些执行入口。
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parent
PROFILE_DIR = ROOT / "profiles"
ACTIVE_PROFILE = PROFILE_DIR / "active.json"
MANAGER_SETTINGS = PROFILE_DIR / "manager-settings.json"
HELP_FILE = ROOT.parent / "FRPC_ADB_MANAGER_HELP.txt"
DEFAULTS = {
    "profileName": "new-profile",
    "serverAddr": "39.107.228.222",
    "serverPort": 7000,
    "localPort": 5555,
    "remotePort": 6004,
    "token": "",
    "installBase": "/data/adb",
    "includeAdbBootstrap": True,
    "enableFrpcLog": True,
    "enableFrpcSchedule": False,
    "frpcScheduleInterval": 3600,
    "frpcScheduleBody": """child_pid=$(cat \"$CHILD_PID\" 2>/dev/null)
if [ -n \"$child_pid\" ] && kill -0 \"$child_pid\" 2>/dev/null; then
    kill \"$child_pid\" 2>/dev/null
    if [ \"$LOG_ENABLED\" = \"1\" ]; then
        printf '%s scheduled child restart\\n' \"$(date '+%Y-%m-%d %H:%M:%S')\" >> \"$LOG\"
    fi
fi""",
    "serial": "",
    "deviceUniqueId": "",
    "deviceBrandModel": "",
    "note": "",
}
NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")


@dataclass(frozen=True)
class Device:
    serial: str
    display: str
    state: str = "device"


def device_identity(serial: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for key in (
        "ro.product.manufacturer", "ro.product.brand", "ro.product.model",
        "ro.boot.serialno", "ro.serialno", "ro.boot.hardware.sku",
    ):
        code, value = adb(["shell", "getprop", key], serial)
        values[key] = value.strip() if code == 0 else ""
    code, android_id = adb(["shell", "settings", "get", "secure", "android_id"], serial)
    values["android_id"] = android_id.strip() if code == 0 else ""
    values["uniqueId"] = values.get("ro.boot.serialno") or values.get("ro.serialno") or values.get("android_id") or serial
    values["brandModel"] = " ".join(filter(None, (values.get("ro.product.brand"), values.get("ro.product.model"))))
    return values


def now_text() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %z")


def run_command(args: list[str], timeout: int = 30) -> tuple[int, str]:
    try:
        completed = subprocess.run(
            args,
            cwd=ROOT.parent,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode, (completed.stdout + completed.stderr).strip()
    except FileNotFoundError:
        return 127, f"找不到命令: {args[0]}"
    except subprocess.TimeoutExpired:
        return 124, f"命令超时: {' '.join(args)}"


def adb(args: list[str], serial: str | None = None, timeout: int = 20) -> tuple[int, str]:
    command = ["adb"]
    if serial:
        command += ["-s", serial]
    return run_command(command + args, timeout)


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}




def repair_legacy_schedule_body(value: object) -> str:
    """Convert legacy literal \\n separators in flattened schedule templates."""
    text = str(value or "")
    # Only repair flattened server templates; preserve intentional shell escapes.
    if "\\n" in text and (
        "\\n# ===== server integration template =====" in text
        or "\\nDEVICE_SERVER_BASE=" in text
    ):
        return text.replace("\\n", "\n")
    return text


def normalize_profile(value: dict, name: str) -> dict:
    profile = dict(DEFAULTS)
    profile.update(value)
    profile["profileName"] = name
    return profile


def profile_files() -> list[Path]:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    return sorted(
        (path for path in PROFILE_DIR.glob("*.json") if path.name not in {"active.json", "manager-settings.json"}),
        key=lambda path: path.name.lower(),
    )


def get_devices() -> list[Device]:
    code, output = adb(["devices", "-l"])
    if code != 0:
        return []
    devices: list[Device] = []
    for line in output.splitlines():
        match = re.match(r"^([^\s]+)\s+(device|offline|unauthorized)(?:\s+(.*))?$", line)
        if not match or match.group(2) != "device":
            continue
        serial, state, attrs = match.group(1), match.group(2), match.group(3) or ""
        model_match = re.search(r"(?:^|\s)model:([^\s]+)", attrs)
        model = model_match.group(1).replace("_", " ") if model_match else "未知型号"
        devices.append(Device(serial, f"{serial}  |  {model}", state))
    return devices


def device_info(serial: str) -> str:
    keys = [
        "ro.product.manufacturer", "ro.product.brand", "ro.product.model",
        "ro.product.device", "ro.build.version.release", "ro.build.version.sdk",
        "ro.build.version.security_patch", "ro.product.cpu.abi",
        "ro.product.cpu.abilist", "ro.boot.serialno", "ro.boot.verifiedbootstate",
        "ro.boot.flash.locked", "ro.build.display.id", "ro.hardware",
        "sys.usb.config", "sys.usb.state", "init.svc.adbd", "service.adb.tcp.port",
        "persist.adb.tcp.port",
    ]
    code, phone_time = adb(["shell", "date", "+%Y-%m-%d %H:%M:%S %z"], serial)
    lines = [
        f"手机当前时间: {phone_time if code == 0 and phone_time else '未提供'}",
        f"查询时间（电脑）: {now_text()}",
        f"ADB 序列号: {serial}",
        "",
    ]
    for key in keys:
        code, value = adb(["shell", "getprop", key], serial)
        lines.append(f"{key}: {value if code == 0 and value else '未提供'}")
    code, root_id = adb(["shell", "su", "-c", "id"], serial)
    lines += [f"Root: {'可用' if code == 0 and 'uid=0' in root_id else '不可用'}", f"Root id: {root_id or '未返回'}"]
    for scope, key in (("global", "development_settings_enabled"), ("global", "adb_enabled"), ("global", "adb_wifi_enabled")):
        code, value = adb(["shell", "settings", "get", scope, key], serial)
        lines.append(f"settings {scope} {key}: {value if code == 0 else '未提供'}")
    return "\n".join(lines)


def parse_installed_toml(text: str) -> dict:
    """读取本项目生成的 TOML 字段，不尝试实现通用 TOML 解析器。"""
    result: dict[str, str | int] = {}
    profile_match = re.search(r"(?m)^#\s*profileName:\s*(.+?)\s*$", text)
    if profile_match:
        result["profileName"] = profile_match.group(1).strip()
    for key in ("serverAddr", "auth.token"):
        match = re.search(rf'(?m)^{re.escape(key)}\s*=\s*"((?:\\.|[^"\\])*)"\s*$', text)
        if match:
            result["token" if key == "auth.token" else key] = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    ports = re.findall(r"(?m)^(serverPort|localPort|remotePort)\s*=\s*(\d+)\s*$", text)
    result.update({key: int(value) for key, value in ports})
    return result


def installed_config(serial: str) -> dict:
    code, frpc_state = adb(["shell", "su", "-c", "cat /data/adb/service-state 2>/dev/null"], serial)
    frpc_lines = [line.strip() for line in frpc_state.splitlines() if line.strip()] if code == 0 else []
    code, adb_state = adb(["shell", "su", "-c", "cat /data/adb/tcp-adb-service-path 2>/dev/null"], serial)
    adb_service = next((line.strip() for line in adb_state.splitlines() if line.strip()), "") if code == 0 else ""
    result = {
        "installed": False,
        "frpcInstalled": False,
        "adbInstalled": bool(re.fullmatch(r"/data/adb/service\.d/[A-Za-z0-9_-]+\.sh", adb_service)),
        "adbService": adb_service,
        "frpcInstalledAt": "",
        "adbInstalledAt": "",
        "message": "设备上未找到 FRPC 或 ADB 开机服务。",
        "adbServiceConflict": False,
        "adbServiceCandidates": [],
    }
    code, candidates = adb([
        "shell", "su", "-c",
        "for f in /data/adb/service.d/*.sh; do "
        "[ -f \"$f\" ] || continue; "
        "grep -qE 'tcp-adb-service-path|ADB_KEYS_FILE|service.adb.tcp.port' \"$f\" 2>/dev/null && echo \"$f\"; "
        "done",
    ], serial)
    if code == 0:
        result["adbServiceCandidates"] = [line.strip() for line in candidates.splitlines() if line.strip()]
        result["adbServiceConflict"] = len(result["adbServiceCandidates"]) > 1
    if result["adbInstalled"]:
        code, stat_time = adb(["shell", "su", "-c", f"stat -c '%y' {adb_service} 2>/dev/null"], serial)
        if code == 0:
            result["adbInstalledAt"] = stat_time.strip()
    if len(frpc_lines) < 2:
        if result["adbInstalled"]:
            result["message"] = "已找到 ADB 开机恢复服务，但没有 FRPC 服务。"
        return result
    service, install_dir = frpc_lines[0], frpc_lines[1]
    if not re.fullmatch(r"/data/adb/service\.d/[A-Za-z0-9_-]+\.sh", service):
        result["message"] = "FRPC 服务状态文件格式无效。"
        return result
    if not re.fullmatch(r"/data/adb/[A-Za-z0-9_.-]+", install_dir):
        result["message"] = "FRPC 服务数据目录格式无效。"
        return result
    name = Path(service).stem
    config_path = f"{install_dir}/{name}.toml"
    binary_path = f"{install_dir}/{name}"
    log_path = frpc_lines[2] if len(frpc_lines) >= 3 else f"{install_dir}/runtime.log"
    if not re.fullmatch(r"/data/adb/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", log_path):
        log_path = ""
    log_exists = False
    if log_path:
        code, _ = adb(["shell", "su", "-c", f"test -f {log_path}"], serial)
        log_exists = code == 0
    toml_code, toml = adb(["shell", "su", "-c", f"cat {config_path} 2>/dev/null"], serial)
    result.update({
        "installed": True,
        "frpcInstalled": True,
        "service": service,
        "installDir": install_dir,
        "binary": binary_path,
        "configPath": config_path,
        "logPath": log_path if log_exists else "",
        "logEnabled": log_exists,
        "config": parse_installed_toml(toml) if toml_code == 0 else {},
    })
    # 定时配置以手机上的实际 SH 为准，不能仅根据本地归档推断。
    code, service_text = adb([
        "shell", "su", "-c",
        f"cat {service} 2>/dev/null",
    ], serial)
    schedule_enabled = None
    schedule_interval = None
    schedule_body = ""
    body_match = re.search(
        r"(?ms)^# BEGIN USER SCHEDULE BODY\s*\n(.*?)^# END USER SCHEDULE BODY\s*$",
        service_text,
    )
    if body_match:
        schedule_body = body_match.group(1).strip("\r\n")
    if code == 0:
        for line in service_text.splitlines():
            match = re.match(r"^(SCHEDULE_ENABLED|SCHEDULE_INTERVAL)=['\"]?([0-9]+)['\"]?\s*$", line.strip())
            if not match:
                continue
            if match.group(1) == "SCHEDULE_ENABLED":
                schedule_enabled = match.group(2) == "1"
            else:
                schedule_interval = int(match.group(2))
    result.update({
        "scheduleConfigured": schedule_enabled is not None and schedule_interval is not None,
        "scheduleEnabled": schedule_enabled if schedule_enabled is not None else False,
        "scheduleInterval": schedule_interval if schedule_interval is not None else 3600,
        "scheduleBodyConfigured": body_match is not None,
        "scheduleBody": schedule_body,
    })
    if toml_code == 0 and toml:
        code, status = adb(["shell", "su", "-c", f"sh {service} status"], serial)
        result["running"] = code == 0 and "running" in status
        result["message"] = "FRPC 和 ADB 服务状态读取完成。" if result["adbInstalled"] else "FRPC 服务状态读取完成，未找到 ADB 开机服务。"
    if result["frpcInstalled"] and not result["frpcInstalledAt"]:
        code, stat_time = adb(["shell", "su", "-c", f"stat -c '%y' {service} 2>/dev/null"], serial)
        if code == 0:
            result["frpcInstalledAt"] = stat_time.strip()
    if result["frpcInstalled"] and not result.get("config"):
        result["message"] = "已找到 FRPC 启动脚本，但无法读取 TOML 配置。"
    return result


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("FRPC / ADB 设备管理")
        self.geometry("1080x720")
        self.minsize(900, 600)
        self.profiles: dict[str, dict] = {}
        self.selected_profile = "new-profile"
        self.devices: list[Device] = []
        self.visible_devices: list[Device] = []
        self.output_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.settings = {"autoRefreshSeconds": 5}
        self.settings.update(load_json(MANAGER_SETTINGS))
        self.refresh_in_progress = False
        self.refresh_selected_after_scan = False
        self.selection_context = "archive"
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in DEFAULTS.items()}
        self.adb_var = tk.BooleanVar(value=True)
        self.log_var = tk.BooleanVar(value=True)
        self.schedule_var = tk.BooleanVar(value=False)
        self.schedule_interval_var = tk.StringVar(value="3600")
        self.frpc_installed_var = tk.BooleanVar(value=False)
        self.target_var = tk.StringVar(value="未选择设备")
        self.service_state_var = tk.StringVar(value="FRPC: 未知    ADB 开机恢复: 未知")
        self.status_var = tk.StringVar(value="就绪")
        self.current_installed: dict = {}
        self.current_installed_serial = ""
        self.build_ui()
        self.load_profiles()
        self.request_device_refresh()
        self.after(100, self.consume_output)
        self.after(1000, self.auto_refresh_tick)

    def build_ui(self) -> None:
        menu = tk.Menu(self)
        settings_menu = tk.Menu(menu, tearoff=False)
        settings_menu.add_command(label="全局设置...", command=self.show_global_settings)
        menu.add_cascade(label="设置", menu=settings_menu)
        adb_menu = tk.Menu(menu, tearoff=False)
        adb_menu.add_command(label="安装仅 ADB 到当前设备", command=self.install_adb_only)
        adb_menu.add_command(label="生成仅 ADB 安装 SH...", command=self.generate_adb_only)
        menu.add_cascade(label="仅 ADB", menu=adb_menu)
        service_menu = tk.Menu(menu, tearoff=False)
        service_menu.add_command(label="安装 FRPC + ADB 到当前设备", command=self.install_service_with_adb)
        service_menu.add_command(label="生成 FRPC + ADB 安装包...", command=self.generate_service_with_adb)
        menu.add_cascade(label="FRPC + ADB", menu=service_menu)
        self.configure(menu=menu)

        self.columnconfigure(1, weight=1)
        self.rowconfigure(1, weight=1)
        top = ttk.Frame(self, padding=8)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.columnconfigure(1, weight=1)
        ttk.Label(top, text="全局搜索").grid(row=0, column=0, padx=(0, 6))
        self.search = ttk.Entry(top)
        self.search.grid(row=0, column=1, sticky="ew")
        self.search.bind("<Return>", lambda _event: self.open_global_search())
        ttk.Button(top, text="搜索...", command=self.open_global_search).grid(row=0, column=2, padx=6)
        ttk.Button(top, text="刷新设备", command=self.request_device_refresh).grid(row=0, column=3, padx=6)
        ttk.Button(top, text="新建设备归档", command=self.new_profile).grid(row=0, column=4)
        ttk.Button(top, text="?", width=3, command=self.open_help).grid(row=0, column=5, padx=(8, 0))

        left = ttk.Frame(self, padding=(8, 0, 4, 8))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)
        profile_frame = ttk.LabelFrame(left, text="本地设备归档", padding=8)
        profile_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        profile_frame.rowconfigure(0, weight=1)
        profile_frame.columnconfigure(0, weight=1)
        self.profile_list = tk.Listbox(profile_frame, width=28, exportselection=False)
        self.profile_list.grid(row=0, column=0, sticky="nsew")
        profile_scroll = ttk.Scrollbar(profile_frame, orient="vertical", command=self.profile_list.yview)
        profile_scroll.grid(row=0, column=1, sticky="ns")
        self.profile_list.configure(yscrollcommand=profile_scroll.set)
        self.profile_list.bind("<<ListboxSelect>>", self.select_profile)
        self.profile_list.bind("<Double-Button-1>", self.profile_double_click)
        ttk.Button(profile_frame, text="删除归档", command=self.delete_profile).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        device_frame = ttk.LabelFrame(left, text="在线 ADB 设备", padding=8)
        device_frame.grid(row=1, column=0, sticky="nsew")
        device_frame.rowconfigure(0, weight=1)
        device_frame.columnconfigure(0, weight=1)
        device_frame.configure(height=155)
        device_frame.grid_propagate(False)
        self.device_list = tk.Listbox(device_frame, width=28, exportselection=False)
        self.device_list.grid(row=0, column=0, sticky="nsew")
        device_scroll = ttk.Scrollbar(device_frame, orient="vertical", command=self.device_list.yview)
        device_scroll.grid(row=0, column=1, sticky="ns")
        self.device_list.configure(yscrollcommand=device_scroll.set)
        self.device_list.bind("<<ListboxSelect>>", self.device_list_selected)
        self.device_list.bind("<Double-Button-1>", self.device_double_click)
        ttk.Button(device_frame, text="立即刷新", command=self.request_device_refresh).grid(
            row=1, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )

        right = ttk.Frame(self, padding=(4, 0, 8, 8))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        right.rowconfigure(14, weight=1)
        self.editor_widgets: list[tk.Widget] = []
        fields = [("设备归档名称", "profileName"), ("服务器地址", "serverAddr"), ("服务器端口", "serverPort"),
                   ("本地端口", "localPort"), ("远程端口", "remotePort"), ("Token", "token"),
                   ("Root 数据目录", "installBase"), ("备注", "note")]
        for row, (label, key) in enumerate(fields):
            ttk.Label(right, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            if key in {"profileName", "note"}:
                name_frame = ttk.Frame(right)
                name_frame.grid(row=row, column=1, sticky="ew", pady=5)
                name_frame.columnconfigure(0, weight=1)
                entry = ttk.Entry(name_frame, textvariable=self.vars[key])
                entry.grid(row=0, column=0, sticky="ew")
                if key == "profileName":
                    helper = ttk.Button(name_frame, text="自动命名", command=self.auto_name_archive)
                else:
                    helper = ttk.Button(name_frame, text="自动备注", command=self.auto_note_archive)
                helper.grid(row=0, column=1, padx=(6, 0))
                self.editor_widgets.extend((entry, helper))
            else:
                entry = ttk.Entry(right, textvariable=self.vars[key], show="*" if key == "token" else "")
                entry.grid(row=row, column=1, sticky="ew", pady=5)
                self.editor_widgets.append(entry)
        options = ttk.Frame(right)
        options.grid(row=8, column=1, sticky="w", pady=5)
        frpc_installed = ttk.Checkbutton(
            options,
            text="FRPC 已安装",
            variable=self.frpc_installed_var,
            state="disabled",
        )
        frpc_installed.pack(side="left")
        adb_check = ttk.Checkbutton(options, text="包含 ADB 开机恢复脚本", variable=self.adb_var)
        adb_check.pack(side="left", padx=(20, 0))
        log_check = ttk.Checkbutton(options, text="启用 FRPC 日志", variable=self.log_var)
        log_check.pack(side="left", padx=(20, 0))
        self.editor_widgets.extend((adb_check, log_check))
        schedule_frame = ttk.Frame(right)
        schedule_frame.grid(row=9, column=1, sticky="w", pady=5)
        schedule_check = ttk.Checkbutton(schedule_frame, text="启用 FRPC 定时自检", variable=self.schedule_var)
        schedule_check.pack(side="left")
        ttk.Label(schedule_frame, text="周期（秒）").pack(side="left", padx=(18, 4))
        schedule_entry = ttk.Entry(schedule_frame, textvariable=self.schedule_interval_var, width=10)
        schedule_entry.pack(side="left")
        ttk.Button(schedule_frame, text="放大编辑...", command=self.open_schedule_editor).pack(side="left", padx=(8, 0))
        ttk.Button(schedule_frame, text="服务器模板...", command=self.open_server_template_dialog).pack(side="left", padx=(6, 0))
        self.editor_widgets.extend((schedule_check, schedule_entry))
        schedule_label_frame = ttk.Frame(right)
        schedule_label_frame.grid(row=10, column=0, sticky="nw", padx=(0, 10), pady=5)
        ttk.Label(schedule_label_frame, text="定时执行段落").pack(side="left")
        schedule_body_frame = ttk.Frame(right)
        schedule_body_frame.grid(row=10, column=1, sticky="ew", pady=5)
        schedule_body_frame.rowconfigure(0, weight=1)
        schedule_body_frame.columnconfigure(0, weight=1)
        self.schedule_body_text = tk.Text(schedule_body_frame, height=6, width=70, wrap="none")
        self.schedule_body_text.grid(row=0, column=0, sticky="ew")
        schedule_body_y = ttk.Scrollbar(schedule_body_frame, orient="vertical", command=self.schedule_body_text.yview)
        schedule_body_y.grid(row=0, column=1, sticky="ns")
        schedule_body_x = ttk.Scrollbar(schedule_body_frame, orient="horizontal", command=self.schedule_body_text.xview)
        schedule_body_x.grid(row=1, column=0, sticky="ew")
        self.schedule_body_text.configure(yscrollcommand=schedule_body_y.set, xscrollcommand=schedule_body_x.set)
        self.editor_widgets.append(self.schedule_body_text)
        ttk.Label(right, text="目标设备").grid(row=11, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Label(right, textvariable=self.target_var).grid(row=11, column=1, sticky="w", pady=5)

        actions = ttk.Frame(right)
        ttk.Label(right, textvariable=self.service_state_var).grid(row=12, column=1, sticky="w", pady=(3, 0))
        actions.grid(row=13, column=0, columnspan=2, sticky="ew", pady=8)
        self.action_buttons: dict[str, ttk.Button] = {}
        action_specs = (
            ("save", "保存归档", self.save_profile),
            ("clone", "复制归档", self.clone_profile),
            ("history", "归档历史", self.show_profile_history),
            ("online_ops", "在线操作...", self.show_online_operations),
        )
        for key, label, callback in action_specs:
            button = ttk.Button(actions, text=label, command=callback)
            button.pack(side="left", padx=(0, 5))
            self.action_buttons[key] = button
        self.update_action_states()
        ttk.Label(right, text="执行输出").grid(row=14, column=0, sticky="nw", padx=(0, 10))
        output_frame = ttk.Frame(right)
        output_frame.grid(row=14, column=1, sticky="nsew")
        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.output = tk.Text(output_frame, height=14, wrap="none", state="disabled")
        self.output.grid(row=0, column=0, sticky="nsew")
        output_y = ttk.Scrollbar(output_frame, orient="vertical", command=self.output.yview)
        output_y.grid(row=0, column=1, sticky="ns")
        output_x = ttk.Scrollbar(output_frame, orient="horizontal", command=self.output.xview)
        output_x.grid(row=1, column=0, sticky="ew")
        self.output.configure(yscrollcommand=output_y.set, xscrollcommand=output_x.set)
        status = ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4))
        status.grid(row=2, column=0, columnspan=2, sticky="ew")

    def load_profiles(self) -> None:
        self.profiles = {path.stem: normalize_profile(load_json(path), path.stem) for path in profile_files()}
        active_data = load_json(ACTIVE_PROFILE)
        active_name = str(active_data.get("profileName", "")) if active_data else ""
        if not NAME_RE.fullmatch(active_name):
            active_name = ""
        if active_data and active_name:
            self.profiles[active_name] = normalize_profile(active_data, active_name)
        if not self.profiles:
            active_name = "new-profile"
            self.profiles[active_name] = normalize_profile({}, active_name)
        elif active_name not in self.profiles:
            active_name = next(iter(sorted(self.profiles)))
        self.refresh_profile_list()
        self.load_profile(active_name)

    def search_changed(self, _event=None) -> None:
        # 搜索结果单独显示在搜索窗口，主界面始终保留完整的归档和设备列表。
        return

    def open_global_search(self) -> None:
        query = self.search.get().strip()
        dialog = tk.Toplevel(self)
        dialog.title("全局搜索结果")
        dialog.geometry("900x560")
        dialog.minsize(700, 400)
        dialog.transient(self)
        dialog.columnconfigure(0, weight=1)
        dialog.rowconfigure(1, weight=1)

        header = ttk.Frame(dialog, padding=10)
        header.grid(row=0, column=0, sticky="ew")
        header.columnconfigure(1, weight=1)
        ttk.Label(header, text="搜索内容").grid(row=0, column=0, padx=(0, 8))
        query_var = tk.StringVar(value=query)
        query_entry = ttk.Entry(header, textvariable=query_var)
        query_entry.grid(row=0, column=1, sticky="ew")
        state_var = tk.StringVar(value="正在搜索本地设备归档和在线 ADB 设备...")
        ttk.Label(header, textvariable=state_var).grid(row=1, column=0, columnspan=2, sticky="w", pady=(8, 0))

        result_frame = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        result_frame.grid(row=1, column=0, sticky="nsew")
        result_frame.columnconfigure(0, weight=1)
        result_frame.rowconfigure(0, weight=1)
        columns = ("kind", "name", "detail")
        tree = ttk.Treeview(result_frame, columns=columns, show="headings", selectmode="browse")
        tree.heading("kind", text="记录类型")
        tree.heading("name", text="名称 / 序列号")
        tree.heading("detail", text="匹配内容")
        tree.column("kind", width=120, anchor="w", stretch=False)
        tree.column("name", width=220, anchor="w", stretch=False)
        tree.column("detail", width=500, anchor="w")
        tree.grid(row=0, column=0, sticky="nsew")
        scrollbar = ttk.Scrollbar(result_frame, orient="vertical", command=tree.yview)
        scrollbar.grid(row=0, column=1, sticky="ns")
        tree.configure(yscrollcommand=scrollbar.set)
        result_map: dict[str, tuple[str, str]] = {}

        action_frame = ttk.Frame(dialog, padding=(10, 0, 10, 10))
        action_frame.grid(row=2, column=0, sticky="ew")

        def render(results: list[tuple[str, str, str, str]], searched: str) -> None:
            tree.delete(*tree.get_children())
            result_map.clear()
            for index, (kind, name, detail, value) in enumerate(results):
                item_id = f"result-{index}"
                tree.insert("", "end", iid=item_id, values=(kind, name, detail))
                result_map[item_id] = (kind, value)
            state_var.set(f"搜索完成：{len(results)} 条记录" + (f"，关键词：{searched}" if searched else "，显示全部记录"))

        def run_search() -> None:
            searched = query_var.get().strip()
            state_var.set("正在搜索本地设备归档和在线 ADB 设备...")
            tree.delete(*tree.get_children())
            def worker() -> None:
                needle = searched.lower()
                results: list[tuple[str, str, str, str]] = []
                for name, profile in sorted(self.profiles.items(), key=lambda item: item[0].lower()):
                    haystack = json.dumps(profile, ensure_ascii=False, indent=2)
                    if not needle or needle in haystack.lower() or needle in name.lower():
                        results.append(("本地设备归档", name, self.search_detail(haystack, needle), name))
                online = get_devices()
                for device in online:
                    identity = device_identity(device.serial)
                    installed = installed_config(device.serial)
                    record = {"device": device.display, "serial": device.serial, "identity": identity, "installed": installed}
                    haystack = json.dumps(record, ensure_ascii=False, indent=2)
                    if not needle or needle in haystack.lower():
                        results.append(("在线 ADB 设备", device.serial, self.search_detail(haystack, needle), device.serial))
                self.output_queue.put(("global_search", (dialog, render, results, searched)))
            threading.Thread(target=worker, daemon=True).start()

        def selected_result() -> tuple[str, str] | None:
            selected = tree.selection()
            if not selected or selected[0] not in result_map:
                return None
            return result_map[selected[0]]

        def locate_result() -> None:
            result = selected_result()
            if result is None:
                return
            kind, value = result
            if kind == "本地设备归档":
                self.load_profile(value)
                self.locate_profile(value)
                self.set_status(f"已定位本地设备归档: {value}")
            else:
                self.select_online_serial(value)
                self.set_status(f"已定位在线 ADB 设备: {value}")

        def open_result_details() -> None:
            result = selected_result()
            if result is None:
                return
            kind, value = result
            locate_result()
            dialog.destroy()
            if kind == "本地设备归档":
                self.show_profile_details()
            else:
                self.show_unified_details(value)

        def modify_result() -> None:
            result = selected_result()
            if result is None:
                messagebox.showinfo("设备归档", "请先选择一条搜索结果。", parent=dialog)
                return
            kind, value = result
            if kind != "本地设备归档":
                messagebox.showinfo("设备归档", "在线 ADB 设备不是本地归档，不能直接修改。请先新建设备归档。", parent=dialog)
                return
            self.load_profile(value)
            self.locate_profile(value)
            self.set_status(f"已打开设备归档进行编辑，请点击主界面“保存归档”完成修改: {value}")
            self.append_output(f"正在编辑设备归档: {value}\n修改字段后请点击“保存归档”写入本地文件。")
            dialog.destroy()

        def delete_result() -> None:
            result = selected_result()
            if result is None:
                messagebox.showinfo("删除归档", "请先选择一条搜索结果。", parent=dialog)
                return
            kind, value = result
            if kind != "本地设备归档":
                messagebox.showinfo("删除归档", "在线 ADB 设备是实时记录，不能删除。", parent=dialog)
                return
            if not messagebox.askyesno("确认删除", f"删除设备归档 {value}？", parent=dialog):
                return
            self.selected_profile = value
            self.delete_profile()
            self.append_output(f"已删除设备归档: {value}")
            run_search()

        def create_result() -> None:
            dialog.destroy()
            self.new_profile()
            self.append_output("已创建新的设备归档草稿，请填写信息后点击“保存归档”。")

        def copy_result() -> None:
            result = selected_result()
            if result is None:
                messagebox.showinfo("复制归档", "请先选择一条本地设备归档。", parent=dialog)
                return
            kind, value = result
            if kind != "本地设备归档":
                messagebox.showinfo("复制归档", "在线 ADB 设备不是本地归档，不能复制。", parent=dialog)
                return
            self.load_profile(value)
            self.clone_profile()
            dialog.destroy()

        context_menu = tk.Menu(dialog, tearoff=False)
        context_menu.add_command(label="打开详细记录", command=open_result_details)
        context_menu.add_command(label="编辑设备归档", command=modify_result)
        context_menu.add_command(label="复制设备归档", command=copy_result)
        context_menu.add_command(label="删除设备归档", command=delete_result)
        context_menu.add_command(label="只定位到主界面", command=locate_result)

        def show_context_menu(event) -> str:
            item = tree.identify_row(event.y)
            if item:
                tree.selection_set(item)
                tree.focus(item)
                context_menu.tk_popup(event.x_root, event.y_root)
            return "break"

        ttk.Button(header, text="重新搜索", command=run_search).grid(row=0, column=2, padx=(8, 0))
        ttk.Button(header, text="关闭", command=dialog.destroy).grid(row=0, column=3, padx=(8, 0))
        ttk.Button(action_frame, text="打开详细记录", command=open_result_details).pack(side="left", padx=(0, 6))
        ttk.Button(action_frame, text="编辑设备归档", command=modify_result).pack(side="left", padx=(0, 6))
        ttk.Button(action_frame, text="复制设备归档", command=copy_result).pack(side="left", padx=(0, 6))
        ttk.Button(action_frame, text="删除设备归档", command=delete_result).pack(side="left", padx=(0, 6))
        ttk.Button(action_frame, text="新建设备归档", command=create_result).pack(side="left", padx=(0, 6))
        ttk.Button(action_frame, text="定位到主界面", command=locate_result).pack(side="left")
        tree.bind("<Double-Button-1>", lambda _event: open_result_details())
        tree.bind("<Return>", lambda _event: open_result_details())
        tree.bind("<Button-3>", show_context_menu)
        run_search()
        query_entry.focus_set()

    @staticmethod
    def search_detail(text: str, needle: str) -> str:
        if not needle:
            return "全部字段可在详细记录中查看"
        matches = [line.strip() for line in text.splitlines() if needle in line.lower()]
        if matches:
            return "; ".join(matches[:4])[:240]
        return "匹配设备记录"

    def select_online_serial(self, serial: str) -> None:
        self.search.delete(0, tk.END)
        self.vars["serial"].set(serial)
        for index, device in enumerate(self.devices):
            if device.serial == serial:
                self.device_list.selection_clear(0, tk.END)
                self.device_list.selection_set(index)
                self.device_list.activate(index)
                self.device_list.see(index)
                self.target_var.set(device.display)
                self.device_list_selected()
                return
        self.target_var.set(serial)

    def refresh_profile_list(self) -> None:
        self.profile_list.delete(0, tk.END)
        for name, profile in sorted(self.profiles.items(), key=lambda item: item[0].lower()):
            self.profile_list.insert(tk.END, name)
        names = self.profile_list.get(0, tk.END)
        if self.selected_profile in names:
            self.profile_list.selection_set(names.index(self.selected_profile))

    def load_profile(self, name: str, refresh_online: bool = True) -> None:
        self.selection_context = "archive"
        self.selected_profile = name
        profile = normalize_profile(self.profiles.get(name, {}), name)
        for key in DEFAULTS:
            if key not in {"includeAdbBootstrap", "enableFrpcLog", "enableFrpcSchedule"}:
                self.vars[key].set(str(profile.get(key, DEFAULTS[key])))
        self.adb_var.set(bool(profile.get("includeAdbBootstrap", True)))
        self.log_var.set(bool(profile.get("enableFrpcLog", True)))
        self.schedule_var.set(bool(profile.get("enableFrpcSchedule", False)))
        self.schedule_interval_var.set(str(profile.get("frpcScheduleInterval", 3600)))
        self.schedule_body_text.configure(state="normal")
        self.schedule_body_text.delete("1.0", tk.END)
        self.schedule_body_text.insert("1.0", repair_legacy_schedule_body(profile.get("frpcScheduleBody", DEFAULTS["frpcScheduleBody"])))
        self.frpc_installed_var.set(bool(profile.get("lastFrpcInstalled", False)))
        self.locate_profile(name)
        self.set_editor_enabled(True)
        self.update_action_states()
        self.set_status(f"已加载本机配置: {name}")
        serial = self.vars["serial"].get().strip()
        if refresh_online and serial and any(device.serial == serial for device in self.devices):
            self.after(0, lambda: self.request_selected_device_config(announce=True))

    def set_editor_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for widget in self.editor_widgets:
            widget.configure(state=state)

    def update_action_states(self, installed: dict | None = None) -> None:
        if not hasattr(self, "action_buttons"):
            return
        enabled: set[str]
        if self.selection_context == "archive":
            # 归档代表待部署的 FRPC 配置；设备上的运行控制要等选择在线设备后再启用。
            enabled = {"save", "clone", "history"}
            serial = self.vars["serial"].get().strip()
            if serial and any(device.serial == serial for device in self.devices):
                enabled.add("online_ops")
        else:
            installed = installed or {}
            self.current_installed = installed
            self.current_installed_serial = self.vars["serial"].get().strip()
            enabled = set()
            enabled.add("online_ops")
        for key, button in self.action_buttons.items():
            button.configure(state="normal" if key in enabled else "disabled")

    def locate_profile(self, name: str) -> None:
        names = self.profile_list.get(0, tk.END)
        if name not in names:
            return
        index = names.index(name)
        self.profile_list.selection_clear(0, tk.END)
        self.profile_list.selection_set(index)
        self.profile_list.activate(index)
        self.profile_list.see(index)

    def select_profile(self, _event=None) -> None:
        selection = self.profile_list.curselection()
        if selection:
            self.load_profile(self.profile_list.get(selection[0]))

    def profile_double_click(self, _event=None) -> None:
        selection = self.profile_list.curselection()
        if selection:
            self.load_profile(self.profile_list.get(selection[0]))
            self.show_profile_details()

    def new_profile(self) -> None:
        target_serial = self.vars["serial"].get().strip() if self.selection_context == "device" else ""
        target_unique_id = self.vars["deviceUniqueId"].get().strip() if target_serial else ""
        target_brand_model = self.vars["deviceBrandModel"].get().strip() if target_serial else ""
        name = "new-profile"
        index = 2
        while name in self.profiles:
            name = f"new-profile-{index}"
            index += 1
        self.profiles[name] = normalize_profile({
            "serial": target_serial,
            "deviceUniqueId": target_unique_id,
            "deviceBrandModel": target_brand_model,
        }, name)
        self.refresh_profile_list()
        self.load_profile(name)
        self.set_status(f"已新建未保存配置: {name}")

    def clone_profile(self) -> None:
        source_name = self.selected_profile
        source = normalize_profile(self.profiles.get(source_name, {}), source_name)
        name = f"{source_name}-copy"
        index = 2
        while name in self.profiles:
            name = f"{source_name}-copy-{index}"
            index += 1
        copied = dict(source)
        copied.update({
            "profileName": name,
            "note": "",
            "serial": "",
            "deviceUniqueId": "",
            "deviceBrandModel": "",
            "firstInstalledAt": "",
            "firstInstalledSerial": "",
            "firstInstalledDeviceInfo": "",
            "createdAt": now_text(),
            "updatedAt": now_text(),
        })
        self.profiles[name] = normalize_profile(copied, name)
        self.refresh_profile_list()
        self.load_profile(name)
        self.append_output(
            f"已复制设备归档: {source_name} -> {name}\n"
            "旧设备标识和首次安装记录已清除。安装到新手机时会重新生成随机服务路径。"
        )
        self.set_status(f"已复制设备归档，请选择目标设备并保存: {name}")

    def auto_name_archive(self) -> None:
        self.request_identity_choice("名称", "profileName", sanitize_name=True)

    def auto_note_archive(self) -> None:
        self.request_identity_choice("备注", "note", sanitize_name=False)

    def request_identity_choice(self, title: str, target_key: str, sanitize_name: bool) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            messagebox.showinfo(f"自动{title}", "请先选择在线 ADB 设备。")
            return
        self.set_status(f"正在读取设备信息以生成{title}...")
        def worker() -> None:
            self.output_queue.put(("identity_choice", (title, target_key, sanitize_name, device_identity(serial))))
        threading.Thread(target=worker, daemon=True).start()

    def apply_identity_choice(
        self,
        title: str,
        target_key: str,
        sanitize_name: bool,
        identity: dict[str, str],
    ) -> None:
        dialog = tk.Toplevel(self)
        dialog.title(f"选择设备归档{title}")
        dialog.transient(self)
        dialog.grab_set()
        options = [
            ("设备唯一 ID", identity.get("uniqueId", "")),
            ("品牌 + 型号", identity.get("brandModel", "")),
            ("当前时间", datetime.now().strftime("%Y%m%d-%H%M%S")),
            (
                "时间 + 品牌 + 型号",
                "-".join(filter(None, (
                    datetime.now().strftime("%Y%m%d-%H%M%S"),
                    identity.get("brandModel", ""),
                ))),
            ),
        ]
        selected = tk.StringVar(value=options[0][1])
        for row, (label, value) in enumerate(options):
            ttk.Radiobutton(dialog, text=f"{label}: {value or '未提供'}", variable=selected, value=value).grid(
                row=row, column=0, sticky="w", padx=14, pady=6
            )
        def apply() -> None:
            value = selected.get().strip()
            if sanitize_name:
                value = re.sub(r"[^A-Za-z0-9_-]+", "-", value).strip("-")
            if value:
                self.vars[target_key].set(value)
                self.set_status(f"已选择设备归档{title}: {value}")
            dialog.destroy()
        ttk.Button(dialog, text=f"使用此{title}", command=apply).grid(row=len(options), column=0, sticky="e", padx=14, pady=10)

    def save_profile(self) -> bool:
        name = self.vars["profileName"].get().strip()
        if not NAME_RE.fullmatch(name):
            messagebox.showerror("归档错误", "设备归档名称只能包含字母、数字、下划线和短横线。")
            return False
        if name != self.selected_profile and name in self.profiles:
            messagebox.showerror("归档错误", f"设备归档 {name} 已存在，请换一个名称。")
            return False
        serial = self.vars["serial"].get().strip()
        online = {device.serial for device in self.devices}
        old_profile = self.profiles.get(self.selected_profile, {})
        matched_online = bool(serial and serial in online and str(old_profile.get("serial", serial)) == serial)
        if not matched_online:
            if not messagebox.askyesno(
                "保存为挂起归档",
                "当前归档没有匹配的在线 ADB 设备，将保存为未联线状态。是否继续？",
            ):
                return False
        try:
            profile = dict(self.profiles.get(self.selected_profile, {}))
            profile.update({
                key: self.vars[key].get().strip()
                for key in DEFAULTS
                if key not in {"includeAdbBootstrap", "enableFrpcLog"}
            })
            for key in ("serverPort", "localPort", "remotePort"):
                profile[key] = int(profile[key])
                if not 1 <= profile[key] <= 65535:
                    raise ValueError
            profile["frpcScheduleInterval"] = int(profile["frpcScheduleInterval"])
            if not 10 <= profile["frpcScheduleInterval"] <= 2147483:
                raise ValueError
            if not profile["serverAddr"] or not profile["installBase"].startswith("/") or ".." in profile["installBase"]:
                raise ValueError
        except ValueError:
            messagebox.showerror("归档错误", "服务器地址、端口、Root 数据目录或定时周期无效。定时周期范围为 10 到 2147483 秒。")
            return False
        profile["includeAdbBootstrap"] = self.adb_var.get()
        profile["enableFrpcLog"] = self.log_var.get()
        profile["enableFrpcSchedule"] = self.schedule_var.get()
        profile["frpcScheduleBody"] = repair_legacy_schedule_body(self.schedule_body_text.get("1.0", "end-1c").replace("\r\n", "\n")).strip("\n")
        profile.setdefault("createdAt", now_text())
        history = list(profile.get("saveHistory", []))
        snapshot = {
            key: profile.get(key)
            for key in (
                *(key for key in DEFAULTS if key != "token"), "createdAt", "firstInstalledAt",
                "firstInstalledSerial", "firstInstalledDeviceInfo",
            )
        }
        history.append({
            "savedAt": now_text(),
            "connected": matched_online,
            "serial": serial,
            "deviceUniqueId": profile.get("deviceUniqueId", ""),
            "deviceBrandModel": profile.get("deviceBrandModel", ""),
            "snapshot": snapshot,
        })
        profile["saveHistory"] = history[-50:]
        profile["updatedAt"] = now_text()
        old_name = self.selected_profile
        self.profiles[name] = normalize_profile(profile, name)
        if old_name != name:
            (PROFILE_DIR / f"{old_name}.json").unlink(missing_ok=True)
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        data = json.dumps(self.profiles[name], ensure_ascii=False, indent=2) + "\n"
        (PROFILE_DIR / f"{name}.json").write_text(data, encoding="utf-8")
        ACTIVE_PROFILE.write_text(data, encoding="utf-8")
        self.selected_profile = name
        self.refresh_profile_list()
        self.append_output(f"已保存设备归档: {name}\n已设为当前归档: {ACTIVE_PROFILE}")
        self.set_status(f"设备归档已保存并设为当前: {name}")
        return True

    def show_profile_history(self) -> None:
        profile = normalize_profile(self.profiles.get(self.selected_profile, {}), self.selected_profile)
        history = profile.get("saveHistory", [])
        dialog = tk.Toplevel(self)
        dialog.title(f"归档历史 - {self.selected_profile}")
        dialog.geometry("820x460")
        dialog.transient(self)
        frame = ttk.Frame(dialog, padding=10)
        frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(frame, columns=("time", "state", "device"), show="headings", selectmode="browse")
        tree.heading("time", text="保存时间")
        tree.heading("state", text="状态")
        tree.heading("device", text="设备")
        tree.column("time", width=190, stretch=False)
        tree.column("state", width=130, stretch=False)
        tree.column("device", width=420)
        tree.pack(fill="both", expand=True)
        reversed_history = list(reversed(history))
        for index, item in enumerate(reversed_history):
            tree.insert("", "end", iid=str(index), values=(
                item.get("savedAt", "未记录"),
                "已联线" if item.get("connected") else "挂起 / 未联线",
                " | ".join(filter(None, (item.get("serial", ""), item.get("deviceBrandModel", "")))) or "未记录",
            ))
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=(10, 0))

        def rollback() -> None:
            selected = tree.selection()
            if not selected:
                messagebox.showinfo("回撤归档", "请先选择一条保存记录。", parent=dialog)
                return
            item = reversed_history[int(selected[0])]
            snapshot = item.get("snapshot")
            if not isinstance(snapshot, dict):
                messagebox.showinfo("回撤归档", "这条旧记录没有完整配置快照，不能回撤。", parent=dialog)
                return
            if not messagebox.askyesno("确认回撤", f"回撤到 {item.get('savedAt', '该时间')} 的配置？", parent=dialog):
                return
            restored = dict(profile)
            restored.update(snapshot)
            restored["saveHistory"] = history
            restored["updatedAt"] = now_text()
            self.profiles[self.selected_profile] = normalize_profile(restored, self.selected_profile)
            self.persist_profile(self.selected_profile)
            self.load_profile(self.selected_profile)
            self.append_output(f"已回撤设备归档 {self.selected_profile} 到: {item.get('savedAt', '未记录')}")
            dialog.destroy()

        ttk.Button(actions, text="回撤到所选记录", command=rollback).pack(side="left")
        ttk.Button(actions, text="关闭", command=dialog.destroy).pack(side="right")

    def show_online_operations(self) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial or not any(device.serial == serial for device in self.devices):
            messagebox.showinfo("在线操作", "请先在左下方选择在线 ADB 设备。")
            return
        dialog = tk.Toplevel(self)
        dialog.title(f"在线操作 - {serial}")
        dialog.transient(self)
        dialog.grab_set()
        body = ttk.Frame(dialog, padding=14)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text="FRPC 操作", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        frpc = ttk.Frame(body)
        frpc.pack(fill="x", pady=(6, 14))
        ttk.Label(body, text="ADB 开机恢复操作", font=("TkDefaultFont", 10, "bold")).pack(anchor="w")
        adb_frame = ttk.Frame(body)
        adb_frame.pack(fill="x", pady=(6, 10))
        installed = self.current_installed if self.current_installed_serial == serial else {}
        status_known = self.current_installed_serial == serial
        buttons: list[ttk.Button] = []

        def add_button(parent, label, command, enabled=True):
            button = ttk.Button(parent, text=label, command=lambda: (dialog.destroy(), command()))
            button.pack(side="left", padx=(0, 6))
            button.configure(state="normal" if enabled else "disabled")
            buttons.append(button)

        add_button(frpc, "推送 FRPC 配置", self.push_saved_frpc_profile, True)
        add_button(frpc, "启动", lambda: self.service_action("start"), status_known and bool(installed.get("frpcInstalled")))
        add_button(frpc, "停止", lambda: self.service_action("stop"), status_known and bool(installed.get("frpcInstalled")))
        add_button(frpc, "重启", lambda: self.service_action("restart"), status_known and bool(installed.get("frpcInstalled")))
        add_button(frpc, "卸载", lambda: self.service_action("uninstall"), status_known and bool(installed.get("frpcInstalled")))
        add_button(adb_frame, "安装 ADB", lambda: self.install_adb_only(save_before=False), status_known and not installed.get("adbInstalled"))
        add_button(adb_frame, "卸载 ADB", self.uninstall_adb, status_known and bool(installed.get("adbInstalled")))
        ttk.Label(
            body,
            text="推送配置会由手机端脚本清理旧 FRPC 服务后重新安装。ADB 卸载只删除开机恢复脚本，不会停止当前 adbd。",
            foreground="#666666",
            wraplength=560,
        ).pack(anchor="w", pady=(4, 8))
        ttk.Button(body, text="关闭", command=dialog.destroy).pack(anchor="e")

    def delete_profile(self) -> None:
        name = self.selected_profile
        if not messagebox.askyesno("确认删除", f"删除配置 {name}？"):
            return
        self.profiles.pop(name, None)
        (PROFILE_DIR / f"{name}.json").unlink(missing_ok=True)
        if self.profiles:
            self.load_profile(next(iter(sorted(self.profiles))))
            self.persist_profile(self.selected_profile)
        else:
            self.profiles["new-profile"] = normalize_profile({}, "new-profile")
            self.load_profile("new-profile")
            ACTIVE_PROFILE.unlink(missing_ok=True)
        self.refresh_profile_list()
        self.set_status(f"已删除设备归档: {name}")

    def request_device_refresh(self) -> None:
        if self.refresh_in_progress:
            return
        self.refresh_in_progress = True
        self.set_status("正在扫描在线 ADB 设备...")
        def worker() -> None:
            self.output_queue.put(("devices", get_devices()))
        threading.Thread(target=worker, daemon=True).start()

    def apply_devices(self, devices: list[Device]) -> None:
        previous = self.vars["serial"].get().strip()
        self.devices = devices
        self.refresh_device_list()
        index = next((i for i, device in enumerate(self.visible_devices) if device.serial == previous), -1)
        if index >= 0:
            self.device_list.selection_set(index)
            self.device_list.activate(index)
            self.target_var.set(self.visible_devices[index].display)
        elif not devices:
            self.vars["serial"].set("")
            self.target_var.set("未选择设备")
        self.refresh_in_progress = False
        self.set_status(f"ADB 扫描完成，在线设备: {len(devices)}")
        if index >= 0 and self.selection_context == "device" and not self.refresh_selected_after_scan:
            self.request_selected_device_config()
        if self.refresh_selected_after_scan:
            self.refresh_selected_after_scan = False
            self.after(2500, lambda: self.request_selected_device_config(announce=True))

    def request_selected_device_config(self, announce: bool = False) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial or not any(device.serial == serial for device in self.devices):
            return
        if announce:
            self.set_status(f"正在刷新设备服务状态: {serial}")

        def worker() -> None:
            self.output_queue.put((
                "device_config",
                (serial, installed_config(serial), device_identity(serial), announce),
            ))

        threading.Thread(target=worker, daemon=True).start()

    def refresh_device_list(self) -> None:
        self.visible_devices = list(self.devices)
        self.device_list.delete(0, tk.END)
        for device in self.visible_devices:
            self.device_list.insert(tk.END, device.display)

    def auto_refresh_tick(self) -> None:
        self.request_device_refresh()
        try:
            seconds = max(2, int(self.settings.get("autoRefreshSeconds", 5)))
        except (TypeError, ValueError):
            seconds = 5
        self.after(seconds * 1000, self.auto_refresh_tick)

    def device_list_selected(self, _event=None) -> None:
        selection = self.device_list.curselection()
        if not selection or selection[0] >= len(self.visible_devices):
            return
        device = self.visible_devices[selection[0]]
        self.selection_context = "device"
        self.update_action_states({})
        self.vars["serial"].set(device.serial)
        self.target_var.set(device.display)
        self.set_status(f"正在读取设备已安装配置: {device.serial}")
        def worker() -> None:
            self.output_queue.put(("device_config", (device.serial, installed_config(device.serial), device_identity(device.serial))))
        threading.Thread(target=worker, daemon=True).start()

    def device_double_click(self, _event=None) -> None:
        selection = self.device_list.curselection()
        if selection and selection[0] < len(self.visible_devices):
            self.show_unified_details(self.visible_devices[selection[0]].serial)

    def match_profile(self, config: dict) -> str | None:
        declared = str(config.get("profileName", ""))
        if declared in self.profiles:
            return declared
        keys = ("serverAddr", "serverPort", "localPort", "remotePort", "token")
        for name, profile in self.profiles.items():
            if all(str(profile.get(key, "")) == str(config.get(key, "")) for key in keys):
                return name
        return None

    def match_device_profile(self, serial: str, config: dict) -> str | None:
        matched = self.match_profile(config) if config else None
        if matched:
            return matched
        return next(
            (name for name, profile in self.profiles.items() if str(profile.get("serial", "")) == serial),
            None,
        )

    def apply_device_config(
        self,
        serial: str,
        result: dict,
        identity: dict | None = None,
        verbose: bool = True,
    ) -> None:
        if serial != self.vars["serial"].get():
            return
        config = result.get("config", {})
        matched = self.match_device_profile(serial, config)
        if not matched and config:
            imported_name = str(config.get("profileName") or f"device-{serial}")
            if not NAME_RE.fullmatch(imported_name):
                imported_name = f"device-{re.sub(r'[^A-Za-z0-9_-]', '-', serial)}"
            if imported_name in self.profiles:
                index = 2
                base_name = imported_name
                while imported_name in self.profiles:
                    imported_name = f"{base_name}-{index}"
                    index += 1
            imported = normalize_profile(config, imported_name)
            imported.update({
                "serial": serial,
                "installBase": str(Path(str(result["installDir"])).parent).replace("\\", "/"),
                "includeAdbBootstrap": bool(result.get("adbInstalled")),
                "enableFrpcLog": bool(result.get("logEnabled")),
                "createdAt": now_text(),
                "updatedAt": now_text(),
            })
            self.profiles[imported_name] = imported
            self.refresh_profile_list()
            matched = imported_name
        if matched:
            profile = self.profiles[matched]
            profile.update({
                "serial": serial,
                "includeAdbBootstrap": bool(result.get("adbInstalled")),
                "enableFrpcLog": bool(result.get("frpcInstalled") and result.get("logEnabled")),
                "lastSeenAt": now_text(),
                "lastFrpcInstalled": bool(result.get("frpcInstalled")),
                "lastAdbInstalled": bool(result.get("adbInstalled")),
                "lastFrpcRunning": bool(result.get("running")),
                "lastScheduleEnabled": bool(result.get("scheduleEnabled")) if result.get("scheduleConfigured") else None,
                "lastScheduleInterval": result.get("scheduleInterval") if result.get("scheduleConfigured") else None,
            })
            if identity:
                profile.update({
                    "deviceUniqueId": identity.get("uniqueId", ""),
                    "deviceBrandModel": identity.get("brandModel", ""),
                })
            self.persist_profile(matched)
            self.load_profile(matched, refresh_online=False)
            self.vars["serial"].set(serial)
        frpc_state = "运行中" if result.get("running") else ("已安装但未运行" if result.get("frpcInstalled") else "未安装")
        adb_state = "已安装" if result.get("adbInstalled") else "未安装"
        self.frpc_installed_var.set(bool(result.get("frpcInstalled")))
        self.adb_var.set(bool(result.get("adbInstalled")))
        self.log_var.set(bool(result.get("frpcInstalled") and result.get("logEnabled")))
        if result.get("scheduleConfigured"):
            self.schedule_var.set(bool(result.get("scheduleEnabled")))
            self.schedule_interval_var.set(str(result.get("scheduleInterval", 3600)))
        if result.get("scheduleBodyConfigured"):
            self.schedule_body_text.configure(state="normal")
            self.schedule_body_text.delete("1.0", tk.END)
            self.schedule_body_text.insert("1.0", repair_legacy_schedule_body(result.get("scheduleBody", "")))
        if identity:
            self.vars["deviceUniqueId"].set(identity.get("uniqueId", ""))
            self.vars["deviceBrandModel"].set(identity.get("brandModel", ""))
        self.selection_context = "device"
        self.set_editor_enabled(False)
        self.service_state_var.set(f"FRPC: {frpc_state}    ADB 开机恢复: {adb_state}")
        self.update_action_states(result)
        if not result.get("frpcInstalled"):
            if verbose:
                self.append_output(str(result.get("message", "设备上没有已安装配置。")))
            self.set_status(f"{serial}: FRPC {frpc_state}，ADB 开机恢复 {adb_state}")
            return
        lines = [
            f"设备: {serial}",
            f"FRPC 运行状态: {'运行中' if result.get('running') else '未运行'}",
            f"FRPC 定时自检: {'已启用' if result.get('scheduleEnabled') else '未启用'}" if result.get("scheduleConfigured") else "FRPC 定时自检: 未读取到配置",
            f"FRPC 定时周期: {result.get('scheduleInterval')} 秒" if result.get("scheduleConfigured") else "FRPC 定时周期: 未读取到配置",
            f"ADB 开机恢复: {adb_state}",
            f"FRPC 安装时间（手机）: {result.get('frpcInstalledAt') or '未记录'}",
            f"ADB 安装时间（手机）: {result.get('adbInstalledAt') or '未记录'}",
            f"匹配配置: {matched or '未匹配'}",
            f"启动脚本: {result.get('service', '')}",
            f"ADB 启动脚本: {result.get('adbService', '') or '未安装'}",
            f"数据目录: {result.get('installDir', '')}",
            f"FRPC 二进制: {result.get('binary', '')}",
            f"TOML 配置: {result.get('configPath', '')}",
            f"Token: {'已读取' if config.get('token') else '未配置'}",
        ]
        if verbose:
            self.append_output("\n".join(lines))
        self.set_status(f"{serial}: 已读取设备配置" + (f"，定位到 {matched}" if matched else ""))

    def persist_profile(self, name: str) -> None:
        """将当前归档的设备识别信息等后台读取结果静默写入本地。"""
        profile = normalize_profile(self.profiles.get(name, {}), name)
        self.profiles[name] = profile
        PROFILE_DIR.mkdir(parents=True, exist_ok=True)
        data = json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
        (PROFILE_DIR / f"{name}.json").write_text(data, encoding="utf-8")
        if name == self.selected_profile:
            ACTIVE_PROFILE.write_text(data, encoding="utf-8")

    @staticmethod
    def read_frpc_logs(serial: str, installed: dict) -> str:
        log_path = installed.get("logPath")
        if not log_path:
            return "FRPC 日志未启用，或设备上尚未生成日志文件。"
        code, output = adb(["shell", "su", "-c", f"tail -n 120 {log_path} 2>/dev/null"], serial, 30)
        return output if code == 0 else f"读取日志失败：{output or '命令失败'}"

    def show_unified_details(self, serial: str) -> None:
        self.set_status(f"正在读取设备完整详情: {serial}")
        def worker() -> None:
            installed = installed_config(serial)
            self.output_queue.put(("unified_details", {
                "serial": serial,
                "identity": device_identity(serial),
                "info": device_info(serial),
                "installed": installed,
                "logs": self.read_frpc_logs(serial, installed),
            }))
        threading.Thread(target=worker, daemon=True).start()

    def apply_unified_details(self, data: dict) -> None:
        serial = str(data["serial"])
        identity = data["identity"]
        installed = data["installed"]
        profile_name = self.match_device_profile(serial, installed.get("config", {}))
        profile = normalize_profile(self.profiles.get(profile_name or self.selected_profile, {}), profile_name or self.selected_profile)
        lines = [
            "设备详情",
            f"归档名称: {profile.get('profileName', self.selected_profile)}",
            f"本地备注: {profile.get('note') or '无'}",
            f"唯一 ID: {identity.get('uniqueId', '未提供')}",
            f"ADB 序列号: {serial}",
            f"品牌型号: {identity.get('brandModel', '未提供')}",
            "",
            f"FRPC: {'运行中' if installed.get('running') else ('已安装但未运行' if installed.get('frpcInstalled') else '未安装')}",
            f"ADB 开机恢复: {'已安装' if installed.get('adbInstalled') else '未安装'}",
            f"FRPC 安装脚本: {installed.get('service') or '无'}",
            f"FRPC 数据目录: {installed.get('installDir') or '无'}",
            f"FRPC 二进制: {installed.get('binary') or '无'}",
            f"FRPC TOML: {installed.get('configPath') or '无'}",
            f"FRPC 日志状态: {'已启用' if installed.get('logEnabled') else '未启用或尚未生成'}",
            f"FRPC 日志文件: {installed.get('logPath') or '无'}",
            f"FRPC 定时自检: {'已启用' if installed.get('scheduleEnabled') else '未启用'}" if installed.get("scheduleConfigured") else "FRPC 定时自检: 未读取到配置",
            f"FRPC 定时周期: {installed.get('scheduleInterval')} 秒" if installed.get("scheduleConfigured") else "FRPC 定时周期: 未读取到配置",
            "手机 SH 定时执行段落:",
            installed.get("scheduleBody") if installed.get("scheduleBodyConfigured") else "未读取到定时执行段落",
            f"FRPC 安装时间（手机文件时间）: {installed.get('frpcInstalledAt') or '未记录'}",
            f"ADB 脚本: {installed.get('adbService') or '未安装'}",
            f"ADB 安装时间（手机文件时间）: {installed.get('adbInstalledAt') or '未记录'}",
            f"ADB 服务冲突检查: {'发现多个 ADB 恢复脚本: ' + ', '.join(installed.get('adbServiceCandidates', [])) if installed.get('adbServiceConflict') else '未发现多个受管理的 ADB 恢复脚本'}",
            "",
            "本地归档配置:",
            f"服务器: {profile.get('serverAddr')}:{profile.get('serverPort')}",
            f"映射: 127.0.0.1:{profile.get('localPort')} -> {profile.get('remotePort')}",
            f"Token: {'已保存' if profile.get('token') else '未配置'}",
            f"备注: {profile.get('note') or '无'}",
            f"归档创建时间: {profile.get('createdAt', '未记录')}",
            f"归档保存次数: {len(profile.get('saveHistory', []))}",
            f"第一次安装时间（手机）: {profile.get('firstInstalledAt', '未记录')}",
            "",
            data["info"],
            "",
            "FRPC 最近日志:",
            data.get("logs") or "无日志，或当前无法读取。",
        ]
        dialog = tk.Toplevel(self)
        dialog.title(f"设备详情 - {profile.get('profileName', self.selected_profile)}")
        dialog.geometry("980x760")
        text_frame = ttk.Frame(dialog, padding=8)
        text_frame.pack(fill="both", expand=True)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        text = tk.Text(text_frame, wrap="none")
        text.grid(row=0, column=0, sticky="nsew")
        text_y = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text_y.grid(row=0, column=1, sticky="ns")
        text_x = ttk.Scrollbar(text_frame, orient="horizontal", command=text.xview)
        text_x.grid(row=1, column=0, sticky="ew")
        text.configure(yscrollcommand=text_y.set, xscrollcommand=text_x.set)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        self.set_status(f"已打开设备详情: {serial}")

    def show_profile_details(self) -> None:
        profile = normalize_profile(self.profiles.get(self.selected_profile, {}), self.selected_profile)
        details = [
            f"设备归档名称: {profile.get('profileName', self.selected_profile)}",
            f"备注: {profile.get('note') or '无'}",
            f"创建时间: {profile.get('createdAt', '未记录')}",
            f"最后修改: {profile.get('updatedAt', '未记录')}",
            f"保存历史次数: {len(profile.get('saveHistory', []))}",
            f"FRPC 定时自检配置: {'已启用' if profile.get('enableFrpcSchedule') else '未启用'}",
            f"FRPC 定时周期: {profile.get('frpcScheduleInterval', 3600)} 秒",
            f"手机最近读取的定时状态: {('已启用' if profile.get('lastScheduleEnabled') else '未启用') if profile.get('lastScheduleEnabled') is not None else '尚未读取'}",
            f"手机最近读取的定时周期: {profile.get('lastScheduleInterval') or '尚未读取'} 秒",
            "归档定时执行段落:",
            repair_legacy_schedule_body(profile.get("frpcScheduleBody", DEFAULTS["frpcScheduleBody"])),
            f"第一次安装（手机时间）: {profile.get('firstInstalledAt', '尚未安装')}",
            f"第一次安装设备: {profile.get('firstInstalledSerial', '未记录')}",
            "",
            "第一次安装时的手机配置:",
            str(profile.get("firstInstalledDeviceInfo", "尚未记录")),
        ]
        dialog = tk.Toplevel(self)
        dialog.title(f"设备归档详情 - {self.selected_profile}")
        dialog.geometry("820x620")
        text_frame = ttk.Frame(dialog, padding=8)
        text_frame.pack(fill="both", expand=True)
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)
        text = tk.Text(text_frame, wrap="none")
        text.grid(row=0, column=0, sticky="nsew")
        text_y = ttk.Scrollbar(text_frame, orient="vertical", command=text.yview)
        text_y.grid(row=0, column=1, sticky="ns")
        text_x = ttk.Scrollbar(text_frame, orient="horizontal", command=text.xview)
        text_x.grid(row=1, column=0, sticky="ew")
        text.configure(yscrollcommand=text_y.set, xscrollcommand=text_x.set)
        text.insert("1.0", "\n".join(details))
        text.configure(state="disabled")

    def open_schedule_editor(self) -> None:
        """在独立大窗口编辑定时执行段落。"""
        dialog = tk.Toplevel(self)
        dialog.title("放大编辑 - 定时执行段落")
        dialog.geometry("1100x700")
        dialog.transient(self)

        frame = ttk.Frame(dialog, padding=8)
        frame.pack(fill="both", expand=True)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        editor = tk.Text(frame, wrap="none", undo=True, font=("Consolas", 10))
        editor.grid(row=0, column=0, sticky="nsew")
        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=editor.yview)
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x = ttk.Scrollbar(frame, orient="horizontal", command=editor.xview)
        scroll_x.grid(row=1, column=0, sticky="ew")
        editor.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        editor.insert("1.0", self.schedule_body_text.get("1.0", "end-1c"))

        buttons = ttk.Frame(frame)
        buttons.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0))
        ttk.Label(
            buttons,
            text="应用后会回写主界面的定时段落；保存归档并重新推送后才会到手机。",
            foreground="#666666",
        ).pack(side="left")

        def apply() -> None:
            self.schedule_body_text.configure(state="normal")
            self.schedule_body_text.delete("1.0", tk.END)
            self.schedule_body_text.insert("1.0", editor.get("1.0", "end-1c"))
            self.set_status("已更新定时执行段落，请保存归档后再推送")
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="应用", command=apply).pack(side="right")
        editor.focus_set()
    def open_server_template_dialog(self) -> None:
        """向定时执行段落追加服务器注册、心跳、自毁和上传模板。"""
        dialog = tk.Toplevel(self)
        dialog.title("服务器接口模板")
        dialog.geometry("620x430")
        dialog.transient(self)
        dialog.grab_set()
        frame = ttk.Frame(dialog, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(1, weight=1)

        ttk.Label(frame, text="服务器基础地址").grid(row=0, column=0, sticky="w", padx=(0, 10), pady=6)
        base_url = tk.StringVar(value="http://50.114.113.121")
        ttk.Entry(frame, textvariable=base_url).grid(row=0, column=1, sticky="ew", pady=6)
        ttk.Label(frame, text="软件类型").grid(row=1, column=0, sticky="w", padx=(0, 10), pady=6)
        software_type = tk.StringVar(value="frp-android")
        ttk.Entry(frame, textvariable=software_type).grid(row=1, column=1, sticky="ew", pady=6)

        options = ttk.LabelFrame(frame, text="插入内容", padding=10)
        options.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(8, 6))
        register_var = tk.BooleanVar(value=True)
        heartbeat_var = tk.BooleanVar(value=True)
        self_destruct_var = tk.BooleanVar(value=True)
        upload_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(options, text="注册设备", variable=register_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(options, text="心跳并接收命令", variable=heartbeat_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(options, text="处理自毁命令（危险）", variable=self_destruct_var).pack(anchor="w", pady=3)
        ttk.Checkbutton(options, text="写入上传接口（默认不上传）", variable=upload_var).pack(anchor="w", pady=3)
        ttk.Label(
            frame,
            text="确认后只追加到编辑框，不会立即执行或推送。注册会使用本地标记避免重复注册；上传只定义接口，不会自动上传。",
            foreground="#9a3412",
            wraplength=580,
        ).grid(row=3, column=0, columnspan=2, sticky="w", pady=(4, 10))

        def insert_template() -> None:
            url = base_url.get().strip().rstrip("/")
            kind = software_type.get().strip() or "frp-android"
            if not re.fullmatch(r"https?://[^\\s]+", url):
                messagebox.showerror("地址错误", "服务器地址必须以 http:// 或 https:// 开头。", parent=dialog)
                return
            if not re.fullmatch(r"[A-Za-z0-9._-]+", kind):
                messagebox.showerror("类型错误", "软件类型只能包含字母、数字、点、下划线和短横线。", parent=dialog)
                return
            blocks = [
                "",
                "# ===== server integration template =====",
                f"DEVICE_SERVER_BASE='{url}'",
                f"DEVICE_SOFTWARE_TYPE='{kind}'",
                "",
                "post_json() {",
                "    post_url=$1",
                "    post_body=$2",
                "    if command -v curl >/dev/null 2>&1; then",
                "        curl -fsS --connect-timeout 10 --max-time 20 -X POST \\",
                "            -H 'Content-Type: application/json' -d \"$post_body\" \"$post_url\" 2>/dev/null",
                "    elif command -v wget >/dev/null 2>&1; then",
                "        wget -qO- --timeout=20 --post-data=\"$post_body\" \\",
                "            --header='Content-Type: application/json' \"$post_url\" 2>/dev/null",
                "    else",
                "        return 127",
                "    fi",
                "}",
                "",
                "device_ack() {",
                "    ack_id=$1",
                "    ack_result=$2",
                "    [ -n \"$ack_id\" ] || return 0",
                "    ack_body=\"{\\\"device_id\\\":\\\"$DEVICE_ID\\\",\\\"software_type\\\":\\\"$DEVICE_SOFTWARE_TYPE\\\",\\\"command_ids\\\":\\\"$ack_id\\\",\\\"result\\\":\\\"$ack_result\\\"}\"",
                "    post_json \"$DEVICE_SERVER_BASE/device/ack\" \"$ack_body\" >/dev/null || true",
                "}",
                "",
                "device_managed_cleanup() {",
                "    cleanup_code=$1",
                "    case \"$cleanup_code\" in",
                "        1)",
                "            case \"$INSTALL_DIR\" in /data/adb/*) ;; *) return 1 ;; esac",
                "            ( sleep 1; stop_background_pid \"$CHILD_PID\" 2>/dev/null || true; stop_background_pid \"$SUPERVISOR_PID\" 2>/dev/null || true; rm -f \"$SERVICE_SCRIPT\" \"$STATE\"; rm -rf -- \"$INSTALL_DIR\" ) >/dev/null 2>&1 &",
                "            return 0",
                "            ;;",
                "        2)",
                "            case \"$INSTALL_DIR\" in /data/adb/*) ;; *) return 1 ;; esac",
                "            ( sleep 1; stop_background_pid \"$CHILD_PID\" 2>/dev/null || true; stop_background_pid \"$SUPERVISOR_PID\" 2>/dev/null || true; rm -f \"$SERVICE_SCRIPT\" \"$STATE\"; rm -rf -- \"$INSTALL_DIR\"; [ -z \"$ADB_SCRIPT\" ] || rm -f \"/data/adb/service.d/$ADB_SCRIPT\" ) >/dev/null 2>&1 &",
                "            return 0",
                "            ;;",
                "        *) return 2 ;;",
                "    esac",
                "}",
                "",
            ]
            if register_var.get():
                blocks += [
                    "# ????????????????????????????",
                    "device_status_body=\"{\\\"device_id\\\":\\\"$DEVICE_ID\\\",\\\"software_type\\\":\\\"$DEVICE_SOFTWARE_TYPE\\\"}\"",
                    "device_status_response=$(post_json \"$DEVICE_SERVER_BASE/device/status\" \"$device_status_body\")",
                    "case \"$device_status_response\" in",
                    "    *\\\"registered\\\":true*) : ;;",
                    "    *)",
                    "        register_body=\"{\\\"device_id\\\":\\\"$DEVICE_ID\\\",\\\"software_type\\\":\\\"$DEVICE_SOFTWARE_TYPE\\\",\\\"name\\\":\\\"$DEVICE_ID\\\"}\"",
                    "        post_json \"$DEVICE_SERVER_BASE/device/register\" \"$register_body\" >/dev/null || true",
                    "        ;;",
                    "esac",
                    "",
                ]
            if heartbeat_var.get():
                blocks += [
                    "# 心跳：每次定时执行时请求，返回结果保存在 heartbeat_response。",
                    "heartbeat_body=\"{\\\"device_id\\\":\\\"$DEVICE_ID\\\",\\\"software_type\\\":\\\"$DEVICE_SOFTWARE_TYPE\\\",\\\"name\\\":\\\"$DEVICE_ID\\\"}\"",
                    "heartbeat_response=$(post_json \"$DEVICE_SERVER_BASE/device/heartbeat\" \"$heartbeat_body\")",
                    "",
                ]
            if self_destruct_var.get():
                blocks += [
                    "# Managed cleanup only: never delete device-wide data or other modules.",
                    "if command -v jq >/dev/null 2>&1 && [ -n \"$heartbeat_response\" ]; then",
                    "    command_file=\"$INSTALL_DIR/.pending-commands.$$.tsv\"",
                    "    printf '%s' \"$heartbeat_response\" | jq -r '.data.pending_commands[]? | [.id, .type, (.payload.code // .payload.level // \"\")] | @tsv' 2>/dev/null > \"$command_file\"",
                    "    cleanup_requested=0",
                    "    while IFS=\"$(printf '\\t')\" read -r command_id command_type command_code; do",
                    "        [ -n \"$command_id\" ] || continue",
                    "        case \"$command_type\" in",
                    "            cleanup)",
                    "                if device_managed_cleanup \"$command_code\"; then device_ack \"$command_id\" \"cleanup_completed\"; cleanup_requested=1; else device_ack \"$command_id\" \"cleanup_rejected\"; fi",
                    "                ;;",
                    "            self_destruct) device_ack \"$command_id\" \"unsupported_legacy_command\" ;;",
                    "            *) device_ack \"$command_id\" \"unsupported_command\" ;;",
                    "        esac",
                    "    done < \"$command_file\"",
                    "    rm -f \"$command_file\"",
                    "    [ \"$cleanup_requested\" = \"1\" ] && exit 0",
                    "fi",
                    "",
                ]
            if upload_var.get():
                blocks += [
                    "# 上传接口：只定义函数，不会自动上传。需要时手动调用 upload_json。",
                    "upload_json() {",
                    "    upload_body=$1",
                    "    post_json \"$DEVICE_SERVER_BASE/device/upload\" \"$upload_body\"",
                    "}",
                    "# 示例（默认注释，不会执行）：",
                    "# upload_json '{\"device_id\":\"'\"$DEVICE_ID\"'\",\"type\":\"example\",\"data\":{}}'",
                    "",
                ]
            blocks.append("# ===== end server integration template =====")
            addition = "\n".join(blocks)
            current = self.schedule_body_text.get("1.0", "end-1c").rstrip()
            self.schedule_body_text.configure(state="normal")
            self.schedule_body_text.delete("1.0", tk.END)
            self.schedule_body_text.insert("1.0", (current + "\n" + addition).lstrip())
            self.set_status("已追加服务器接口模板，请放大检查并保存归档")
            dialog.destroy()

        buttons = ttk.Frame(frame)
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(8, 0))
        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(buttons, text="追加到编辑框", command=insert_template).pack(side="right")
    def show_global_settings(self) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("全局设置")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        frame = ttk.Frame(dialog, padding=16)
        frame.pack(fill="both", expand=True)
        ttk.Label(frame, text="ADB 自动刷新间隔（秒）").grid(row=0, column=0, padx=(0, 12), pady=6)
        interval = tk.IntVar(value=int(self.settings.get("autoRefreshSeconds", 5)))
        ttk.Spinbox(frame, from_=2, to=300, textvariable=interval, width=10).grid(row=0, column=1, pady=6)
        ttk.Label(frame, text="其他全局选项后续可以继续加入此处。", foreground="#666666").grid(
            row=1, column=0, columnspan=2, sticky="w", pady=(6, 12)
        )
        def save() -> None:
            try:
                value = max(2, min(300, int(interval.get())))
            except (tk.TclError, ValueError):
                messagebox.showerror("设置错误", "刷新间隔必须是 2 到 300 秒。", parent=dialog)
                return
            self.settings["autoRefreshSeconds"] = value
            PROFILE_DIR.mkdir(parents=True, exist_ok=True)
            MANAGER_SETTINGS.write_text(json.dumps(self.settings, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            self.set_status(f"全局设置已保存，ADB 每 {value} 秒刷新")
            dialog.destroy()
        ttk.Button(frame, text="保存", command=save).grid(row=2, column=0, columnspan=2, sticky="e")

    def open_help(self) -> None:
        """打开随程序提供的纯文本操作说明。"""
        if not HELP_FILE.exists():
            messagebox.showerror("操作说明", f"找不到说明文件：{HELP_FILE}")
            return
        try:
            os.startfile(str(HELP_FILE))
        except OSError as exc:
            messagebox.showerror("操作说明", f"无法打开说明文件：{exc}")

    def choose_output_directory(self) -> str:
        default = ROOT / "personalized"
        default.mkdir(parents=True, exist_ok=True)
        return filedialog.askdirectory(title="选择生成文件保存位置", initialdir=str(default))

    def install_adb_only(self, save_before: bool = True) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            messagebox.showerror("安装失败", "请先在左下列表选择在线 ADB 设备。")
            return
        if not messagebox.askyesno(
            "确认安装 ADB 开机恢复",
            "该操作会以 Root 权限安装开机恢复脚本并持续尝试启用 adbd/TCP 5555。错误配置可能影响后续连接，是否继续？",
        ):
            return
        if save_before and not self.save_profile():
            return
        if not save_before:
            self.persist_profile(self.selected_profile)
        installer = str(ROOT / "install-tcp-adb-preauthorized.bat")
        archive_name = self.selected_profile
        def execute_install() -> tuple[int, str]:
            snapshot = device_info(serial)
            code, output = run_command(["cmd.exe", "/d", "/c", installer, serial], 180)
            if code == 0:
                installed = {}
                # ADB 可能因切换 USB 配置短暂断开，给 adbd 几秒时间重新上线。
                for _ in range(5):
                    installed = installed_config(serial)
                    if installed.get("adbInstalledAt"):
                        break
                    threading.Event().wait(2)
                phone_install_time = str(installed.get("adbInstalledAt") or "")
                if phone_install_time:
                    self.record_first_install(archive_name, serial, snapshot, phone_install_time)
                else:
                    output += "\nADB 已执行，但设备尚未重新上线，暂未读取到手机端安装时间；重新选择设备后可补记。"
            return code, output
        self.run_async(
            execute_install,
            f"正在生成随机 ADB 脚本并安装到: {serial}",
        )

    def install_frpc_only(self) -> None:
        self.adb_var.set(False)
        self.install()

    def push_saved_frpc_profile(self) -> None:
        serial = self.vars["serial"].get().strip()
        profile = self.profiles.get(self.selected_profile, {})
        if not serial:
            messagebox.showerror("推送失败", "请先选择在线 ADB 设备。")
            return
        if str(profile.get("serial", "")) not in {"", serial}:
            if not messagebox.askyesno(
                "确认推送到其他设备",
                f"归档记录的设备是 {profile.get('serial')}，当前设备是 {serial}。仍要推送？",
            ):
                return
        self.install(save_before=False, include_adb=False)

    def generate_adb_only(self) -> None:
        output_dir = self.choose_output_directory()
        if not output_dir:
            return
        renderer = str(ROOT / "render-tcp-adb-preauthorized.ps1")
        self.run_async(
            lambda: run_command([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                renderer, "-OutputDirectory", output_dir,
            ], 60),
            f"正在生成仅 ADB 安装 SH: {output_dir}",
        )

    def generate_service_package(self) -> None:
        if not self.save_profile():
            return
        output_dir = self.choose_output_directory()
        if not output_dir:
            return
        serial = self.vars["serial"].get().strip()
        renderer = str(ROOT / "render-frpc-service.ps1")
        def generate() -> tuple[int, str]:
            abi = "arm64-v8a"
            if serial:
                code, detected = adb(["shell", "getprop", "ro.product.cpu.abi"], serial)
                if code == 0 and detected in {"arm64-v8a", "armeabi-v7a", "x86_64"}:
                    abi = detected
            return run_command([
                "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                renderer, "-Abi", abi, "-OutputDirectory", output_dir,
            ], 120)
        self.run_async(generate, f"正在生成 FRPC + ADB 安装包: {output_dir}")

    def generate_service_with_adb(self) -> None:
        self.adb_var.set(True)
        self.generate_service_package()

    def install_service_with_adb(self) -> None:
        self.adb_var.set(True)
        self.install()

    def install(self, save_before: bool = True, include_adb: bool | None = None) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            messagebox.showerror("安装失败", "请先选择在线 ADB 设备。")
            return
        if save_before and not self.save_profile():
            return
        installer = str(ROOT / "install-frpc-service.bat")
        profile_name = self.selected_profile
        restore_active: str | None = None
        if include_adb is not None:
            restore_active = ACTIVE_PROFILE.read_text(encoding="utf-8") if ACTIVE_PROFILE.exists() else None
            deployment = normalize_profile(self.profiles.get(profile_name, {}), profile_name)
            deployment["includeAdbBootstrap"] = include_adb
            ACTIVE_PROFILE.write_text(json.dumps(deployment, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        def execute_install() -> tuple[int, str]:
            # 安装前采集快照；只有安装命令成功后才把它写入“第一次安装”记录。
            try:
                snapshot = device_info(serial)
                code, output = run_command(["cmd.exe", "/d", "/c", installer, serial], 180)
                if code == 0:
                    installed = installed_config(serial)
                    phone_install_time = installed.get("frpcInstalledAt") or installed.get("adbInstalledAt") or "未记录"
                    self.record_first_install(profile_name, serial, snapshot, str(phone_install_time))
                return code, output
            finally:
                if include_adb is not None:
                    if restore_active is None:
                        ACTIVE_PROFILE.unlink(missing_ok=True)
                    else:
                        ACTIVE_PROFILE.write_text(restore_active, encoding="utf-8")
        self.run_async(
            execute_install,
            f"正在安装到: {serial}",
        )

    def record_first_install(self, profile_name: str, serial: str, snapshot: str, phone_install_time: str) -> None:
        path = PROFILE_DIR / f"{profile_name}.json"
        profile = normalize_profile(load_json(path), profile_name)
        if profile.get("firstInstalledAt") or not phone_install_time or phone_install_time == "未记录":
            return
        profile["firstInstalledAt"] = phone_install_time
        profile["firstInstalledSerial"] = serial
        profile["firstInstalledDeviceInfo"] = snapshot
        profile["updatedAt"] = now_text()
        data = json.dumps(profile, ensure_ascii=False, indent=2) + "\n"
        path.write_text(data, encoding="utf-8")
        ACTIVE_PROFILE.write_text(data, encoding="utf-8")
        self.output_queue.put(("profile_metadata", profile_name))

    def uninstall_adb(self) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            self.append_output("卸载 ADB 失败：请先选择在线 ADB 设备。")
            return
        if not messagebox.askyesno(
            "确认卸载 ADB",
            "仅删除 ADB 开机恢复服务，不停止当前 adbd。手机重启后可能无法再次通过网络 ADB 连接，是否继续？",
        ):
            return
        def execute() -> tuple[int, str]:
            code, state = adb(["shell", "su", "-c", "cat /data/adb/tcp-adb-service-path 2>/dev/null"], serial, 30)
            service = next((line.strip() for line in state.splitlines() if line.strip()), "") if code == 0 else ""
            if not re.fullmatch(r"/data/adb/service\.d/[A-Za-z0-9_-]+\.sh", service):
                return 1, "设备上未找到 ADB 开机恢复服务。"
            code, output = adb(["shell", "su", "-c", f"sh {service} uninstall"], serial, 30)
            return code, output
        self.run_async(execute, f"正在卸载 ADB 开机恢复服务: {serial}")

    def service_action(self, action: str) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            self.append_output("请先选择在线 ADB 设备。")
            return
        def execute() -> tuple[int, str]:
            code, state = adb(["shell", "su", "-c", "cat /data/adb/service-state 2>/dev/null"], serial)
            lines = state.splitlines() if code == 0 else []
            if len(lines) < 1 or not re.fullmatch(r"/data/adb/service\.d/[A-Za-z0-9_-]+\.sh", lines[0].strip()):
                return 1, "设备上未找到已安装的服务。请先点击安装当前。"
            service = lines[0].strip()
            result = adb(["shell", "su", "-c", f"sh {service} {action}"], serial, 30)
            return result
        self.run_async(execute, f"正在执行 {action}: {serial}")

    def run_async(self, function, initial: str) -> None:
        self.append_output(f"[{now_text()}] 开始: {initial}")
        self.set_status(initial)
        def worker() -> None:
            try:
                result = function()
                if isinstance(result, tuple):
                    code, output = result
                    success = code == 0
                    label = "成功" if success else ("超时" if code == 124 else "失败")
                    text = f"结果: {label}（退出码 {code}）\n{output or '命令没有返回其他输出。'}"
                    self.output_queue.put(("result_status", (success, f"{initial}: {label}")))
                    if success:
                        self.output_queue.put(("refresh_after_action", None))
                else:
                    text = str(result)
                    self.output_queue.put(("result_status", (True, f"{initial}: 完成")))
            except Exception as exc:  # GUI 后台任务必须回显错误而不是崩溃
                text = f"执行失败: {exc}"
                self.output_queue.put(("result_status", (False, f"{initial}: 执行异常")))
            self.output_queue.put(("result", text))
        threading.Thread(target=worker, daemon=True).start()

    def consume_output(self) -> None:
        try:
            while True:
                kind, output = self.output_queue.get_nowait()
                if kind == "devices":
                    self.apply_devices(output)  # type: ignore[arg-type]
                elif kind == "device_config":
                    values = output  # type: ignore[assignment]
                    serial, result, identity = values[:3]
                    verbose = values[3] if len(values) > 3 else True
                    self.apply_device_config(serial, result, identity, verbose)
                elif kind == "identity_choice":
                    title, target_key, sanitize_name, identity = output  # type: ignore[misc]
                    self.apply_identity_choice(title, target_key, sanitize_name, identity)
                elif kind == "unified_details":
                    self.apply_unified_details(output)  # type: ignore[arg-type]
                elif kind == "global_search":
                    dialog, render, results, searched = output  # type: ignore[misc]
                    if dialog.winfo_exists():
                        render(results, searched)
                        self.set_status(f"全局搜索完成，找到 {len(results)} 条记录")
                elif kind == "profile_metadata":
                    name = str(output)
                    self.profiles[name] = normalize_profile(load_json(PROFILE_DIR / f"{name}.json"), name)
                    self.refresh_profile_list()
                    self.set_status(f"已记录配置 {name} 的第一次安装手机信息")
                elif kind == "refresh_after_action":
                    # 安装脚本可能暂时切换 USB 配置，先刷新列表，再延迟读取服务状态。
                    self.refresh_selected_after_scan = True
                    self.request_device_refresh()
                elif kind == "result_status":
                    success, status = output  # type: ignore[misc]
                    self.set_status(("成功: " if success else "失败: ") + status)
                else:
                    self.append_output(str(output))
        except queue.Empty:
            pass
        self.after(100, self.consume_output)

    def set_output(self, value: str) -> None:
        self.output.configure(state="normal")
        self.output.delete("1.0", tk.END)
        self.output.insert(tk.END, value)
        self.output.configure(state="disabled")

    def append_output(self, value: str) -> None:
        self.output.configure(state="normal")
        if self.output.get("1.0", "end-1c"):
            self.output.insert(tk.END, "\n")
        self.output.insert(tk.END, value)
        self.output.see(tk.END)
        self.output.configure(state="disabled")

    def set_status(self, value: str) -> None:
        self.status_var.set(value)


def main() -> None:
    try:
        App().mainloop()
    except tk.TclError as exc:
        print(f"无法启动图形界面: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    main()
