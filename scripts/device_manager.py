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
DEFAULTS = {
    "profileName": "default",
    "serverAddr": "39.107.228.222",
    "serverPort": 7000,
    "localPort": 5555,
    "remotePort": 6004,
    "token": "",
    "installBase": "/data/adb",
    "includeAdbBootstrap": True,
    "enableFrpcLog": True,
    "serial": "",
    "deviceUniqueId": "",
    "deviceBrandModel": "",
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
    code, toml = adb(["shell", "su", "-c", f"cat {config_path} 2>/dev/null"], serial)
    result.update({
        "installed": True,
        "frpcInstalled": True,
        "service": service,
        "installDir": install_dir,
        "binary": binary_path,
        "configPath": config_path,
        "logPath": log_path if log_exists else "",
        "logEnabled": log_exists,
        "config": parse_installed_toml(toml) if code == 0 else {},
    })
    if code == 0 and toml:
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
        self.selected_profile = "default"
        self.devices: list[Device] = []
        self.visible_devices: list[Device] = []
        self.output_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.settings = {"autoRefreshSeconds": 5}
        self.settings.update(load_json(MANAGER_SETTINGS))
        self.refresh_in_progress = False
        self.selection_context = "archive"
        self.vars = {key: tk.StringVar(value=str(value)) for key, value in DEFAULTS.items()}
        self.adb_var = tk.BooleanVar(value=True)
        self.log_var = tk.BooleanVar(value=True)
        self.target_var = tk.StringVar(value="未选择设备")
        self.service_state_var = tk.StringVar(value="FRPC: 未知    ADB 开机恢复: 未知")
        self.status_var = tk.StringVar(value="就绪")
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

        left = ttk.Frame(self, padding=(8, 0, 4, 8))
        left.grid(row=1, column=0, sticky="nsew")
        left.rowconfigure(0, weight=3)
        left.rowconfigure(1, weight=2)
        profile_frame = ttk.LabelFrame(left, text="本地设备归档", padding=8)
        profile_frame.grid(row=0, column=0, sticky="nsew", pady=(0, 6))
        profile_frame.rowconfigure(0, weight=1)
        self.profile_list = tk.Listbox(profile_frame, width=28, exportselection=False)
        self.profile_list.grid(row=0, column=0, sticky="nsew")
        self.profile_list.bind("<<ListboxSelect>>", self.select_profile)
        self.profile_list.bind("<Double-Button-1>", self.profile_double_click)
        ttk.Button(profile_frame, text="删除归档", command=self.delete_profile).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        device_frame = ttk.LabelFrame(left, text="在线 ADB 设备", padding=8)
        device_frame.grid(row=1, column=0, sticky="nsew")
        device_frame.rowconfigure(0, weight=1)
        self.device_list = tk.Listbox(device_frame, width=28, exportselection=False)
        self.device_list.grid(row=0, column=0, sticky="nsew")
        self.device_list.bind("<<ListboxSelect>>", self.device_list_selected)
        self.device_list.bind("<Double-Button-1>", self.device_double_click)
        ttk.Button(device_frame, text="立即刷新", command=self.request_device_refresh).grid(row=1, column=0, sticky="ew", pady=(8, 0))

        right = ttk.Frame(self, padding=(4, 0, 8, 8))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(1, weight=1)
        right.rowconfigure(11, weight=1)
        fields = [("设备归档名称", "profileName"), ("服务器地址", "serverAddr"), ("服务器端口", "serverPort"),
                  ("本地端口", "localPort"), ("远程端口", "remotePort"), ("Token", "token"),
                  ("Root 数据目录", "installBase")]
        for row, (label, key) in enumerate(fields):
            ttk.Label(right, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)
            if key == "profileName":
                name_frame = ttk.Frame(right)
                name_frame.grid(row=row, column=1, sticky="ew", pady=5)
                name_frame.columnconfigure(0, weight=1)
                ttk.Entry(name_frame, textvariable=self.vars[key]).grid(row=0, column=0, sticky="ew")
                ttk.Button(name_frame, text="自动命名", command=self.auto_name_archive).grid(row=0, column=1, padx=(6, 0))
            else:
                entry = ttk.Entry(right, textvariable=self.vars[key], show="*" if key == "token" else "")
                entry.grid(row=row, column=1, sticky="ew", pady=5)
        options = ttk.Frame(right)
        options.grid(row=7, column=1, sticky="w", pady=5)
        ttk.Checkbutton(options, text="包含 ADB 开机恢复脚本", variable=self.adb_var).pack(side="left")
        ttk.Checkbutton(options, text="启用 FRPC 日志", variable=self.log_var).pack(side="left", padx=(20, 0))
        ttk.Label(right, text="目标设备").grid(row=8, column=0, sticky="w", padx=(0, 10), pady=5)
        ttk.Label(right, textvariable=self.target_var).grid(row=8, column=1, sticky="w", pady=5)

        actions = ttk.Frame(right)
        ttk.Label(right, textvariable=self.service_state_var).grid(row=9, column=1, sticky="w", pady=(3, 0))
        actions.grid(row=10, column=0, columnspan=2, sticky="ew", pady=8)
        self.action_buttons: dict[str, ttk.Button] = {}
        action_specs = (
            ("save", "保存归档", self.save_profile),
            ("clone", "复制归档", self.clone_profile),
            ("install_frpc", "安装 FRPC", self.install_frpc_only),
            ("install_adb", "安装 ADB", self.install_adb_only),
            ("start_frpc", "启动 FRPC", lambda: self.service_action("start")),
            ("stop_frpc", "停止 FRPC", lambda: self.service_action("stop")),
            ("restart_frpc", "重启 FRPC", lambda: self.service_action("restart")),
            ("uninstall_frpc", "卸载 FRPC", lambda: self.service_action("uninstall")),
            ("uninstall_adb", "卸载 ADB", self.uninstall_adb),
        )
        for key, label, callback in action_specs:
            button = ttk.Button(actions, text=label, command=callback)
            button.pack(side="left", padx=(0, 5))
            self.action_buttons[key] = button
        self.update_action_states()
        ttk.Label(right, text="执行输出").grid(row=11, column=0, sticky="nw", padx=(0, 10))
        self.output = tk.Text(right, height=14, wrap="none", state="disabled")
        self.output.grid(row=11, column=1, sticky="nsew")
        status = ttk.Label(self, textvariable=self.status_var, relief="sunken", anchor="w", padding=(8, 4))
        status.grid(row=2, column=0, columnspan=2, sticky="ew")

    def load_profiles(self) -> None:
        self.profiles = {path.stem: normalize_profile(load_json(path), path.stem) for path in profile_files()}
        active_data = load_json(ACTIVE_PROFILE)
        active_name = str(active_data.get("profileName", "default")) if active_data else "default"
        if not NAME_RE.fullmatch(active_name):
            active_name = "default"
        if active_data:
            self.profiles[active_name] = normalize_profile(active_data, active_name)
        self.profiles.setdefault("default", normalize_profile({}, "default"))
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
            if value == "default":
                messagebox.showinfo("删除归档", "默认设备归档不能删除。", parent=dialog)
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

    def load_profile(self, name: str) -> None:
        self.selection_context = "archive"
        self.selected_profile = name
        profile = normalize_profile(self.profiles.get(name, {}), name)
        for key in DEFAULTS:
            if key not in {"includeAdbBootstrap", "enableFrpcLog"}:
                self.vars[key].set(str(profile.get(key, DEFAULTS[key])))
        self.adb_var.set(bool(profile.get("includeAdbBootstrap", True)))
        self.log_var.set(bool(profile.get("enableFrpcLog", True)))
        self.locate_profile(name)
        self.update_action_states()
        self.set_status(f"已加载本机配置: {name}")

    def update_action_states(self, installed: dict | None = None) -> None:
        if not hasattr(self, "action_buttons"):
            return
        enabled: set[str]
        if self.selection_context == "archive":
            # 归档代表待部署的 FRPC 配置；设备上的运行控制要等选择在线设备后再启用。
            enabled = {"save", "clone", "install_frpc"}
        else:
            installed = installed or {}
            enabled = set()
            # 在线设备未安装 FRPC 时允许直接重新安装；设备端脚本会负责清理旧残留。
            if not installed.get("frpcInstalled"):
                enabled.add("install_frpc")
            if not installed.get("adbInstalled"):
                enabled.add("install_adb")
            if installed.get("frpcInstalled"):
                enabled.update({"start_frpc", "stop_frpc", "restart_frpc", "uninstall_frpc"})
            if installed.get("adbInstalled"):
                enabled.add("uninstall_adb")
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
        name = "new-profile"
        index = 2
        while name in self.profiles:
            name = f"new-profile-{index}"
            index += 1
        self.profiles[name] = normalize_profile({}, name)
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
        serial = self.vars["serial"].get().strip()
        if not serial:
            messagebox.showinfo("自动命名", "请先选择在线 ADB 设备。")
            return
        self.set_status("正在读取设备唯一 ID 和型号...")
        def worker() -> None:
            self.output_queue.put(("auto_name", device_identity(serial)))
        threading.Thread(target=worker, daemon=True).start()

    def apply_auto_name(self, identity: dict[str, str]) -> None:
        dialog = tk.Toplevel(self)
        dialog.title("选择设备归档名称")
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
            (
                "时间 + 唯一 ID",
                "-".join(filter(None, (
                    datetime.now().strftime("%Y%m%d-%H%M%S"),
                    identity.get("uniqueId", ""),
                ))),
            ),
        ]
        selected = tk.StringVar(value=options[0][1])
        for row, (label, value) in enumerate(options):
            ttk.Radiobutton(dialog, text=f"{label}: {value or '未提供'}", variable=selected, value=value).grid(
                row=row, column=0, sticky="w", padx=14, pady=6
            )
        def apply() -> None:
            value = re.sub(r"[^A-Za-z0-9_-]+", "-", selected.get()).strip("-")
            if value:
                self.vars["profileName"].set(value)
                self.set_status(f"已选择设备归档名称: {value}")
            dialog.destroy()
        ttk.Button(dialog, text="使用此名称", command=apply).grid(row=len(options), column=0, sticky="e", padx=14, pady=10)

    def save_profile(self) -> bool:
        name = self.vars["profileName"].get().strip()
        if not NAME_RE.fullmatch(name):
            messagebox.showerror("归档错误", "设备归档名称只能包含字母、数字、下划线和短横线。")
            return False
        if name != self.selected_profile and name in self.profiles:
            messagebox.showerror("归档错误", f"设备归档 {name} 已存在，请换一个名称。")
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
            if not profile["serverAddr"] or not profile["installBase"].startswith("/") or ".." in profile["installBase"]:
                raise ValueError
        except ValueError:
            messagebox.showerror("归档错误", "服务器地址、端口和 Root 数据目录无效。")
            return False
        profile["includeAdbBootstrap"] = self.adb_var.get()
        profile["enableFrpcLog"] = self.log_var.get()
        profile.setdefault("createdAt", now_text())
        profile["updatedAt"] = now_text()
        old_name = self.selected_profile
        self.profiles[name] = normalize_profile(profile, name)
        if old_name != name and old_name != "default":
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

    def delete_profile(self) -> None:
        name = self.selected_profile
        if name == "default":
            messagebox.showinfo("提示", "默认配置不能删除。")
            return
        if not messagebox.askyesno("确认删除", f"删除配置 {name}？"):
            return
        self.profiles.pop(name, None)
        (PROFILE_DIR / f"{name}.json").unlink(missing_ok=True)
        self.load_profile("default")
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

    def apply_device_config(self, serial: str, result: dict, identity: dict | None = None) -> None:
        if serial != self.vars["serial"].get():
            return
        self.selection_context = "device"
        if identity:
            self.vars["deviceUniqueId"].set(identity.get("uniqueId", ""))
            self.vars["deviceBrandModel"].set(identity.get("brandModel", ""))
            self.profiles.setdefault(self.selected_profile, normalize_profile({}, self.selected_profile))
            self.profiles[self.selected_profile].update({
                "deviceUniqueId": identity.get("uniqueId", ""),
                "deviceBrandModel": identity.get("brandModel", ""),
            })
        frpc_state = "运行中" if result.get("running") else ("已安装但未运行" if result.get("frpcInstalled") else "未安装")
        adb_state = "已安装" if result.get("adbInstalled") else "未安装"
        self.service_state_var.set(f"FRPC: {frpc_state}    ADB 开机恢复: {adb_state}")
        self.update_action_states(result)
        if not result.get("frpcInstalled"):
            self.append_output(str(result.get("message", "设备上没有已安装配置。")))
            self.set_status(f"{serial}: FRPC {frpc_state}，ADB 开机恢复 {adb_state}")
            return
        config = result.get("config", {})
        matched = self.match_profile(config)
        if matched:
            self.load_profile(matched)
            self.vars["serial"].set(serial)
            self.locate_profile(matched)
        elif config:
            imported_name = str(config.get("profileName") or f"device-{serial}")
            if not NAME_RE.fullmatch(imported_name):
                imported_name = f"device-{re.sub(r'[^A-Za-z0-9_-]', '-', serial)}"
            imported = normalize_profile(config, imported_name)
            imported["serial"] = serial
            imported["installBase"] = str(Path(str(result["installDir"])).parent).replace("\\", "/")
            self.profiles[imported_name] = imported
            self.refresh_profile_list()
            self.load_profile(imported_name)
            self.vars["serial"].set(serial)
            self.locate_profile(imported_name)
            matched = imported_name
        self.selection_context = "device"
        self.update_action_states(result)
        lines = [
            f"设备: {serial}",
            f"FRPC 运行状态: {'运行中' if result.get('running') else '未运行'}",
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
        self.append_output("\n".join(lines))
        self.set_status(f"{serial}: 已读取设备配置" + (f"，定位到 {matched}" if matched else ""))

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
        profile_name = self.match_profile(installed.get("config", {})) if installed.get("frpcInstalled") else None
        profile = normalize_profile(self.profiles.get(profile_name or self.selected_profile, {}), profile_name or self.selected_profile)
        lines = [
            "设备详情",
            f"归档名称: {profile.get('profileName', self.selected_profile)}",
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
            f"FRPC 安装时间（手机文件时间）: {installed.get('frpcInstalledAt') or '未记录'}",
            f"ADB 脚本: {installed.get('adbService') or '未安装'}",
            f"ADB 安装时间（手机文件时间）: {installed.get('adbInstalledAt') or '未记录'}",
            f"ADB 服务冲突检查: {'发现多个 ADB 恢复脚本: ' + ', '.join(installed.get('adbServiceCandidates', [])) if installed.get('adbServiceConflict') else '未发现多个受管理的 ADB 恢复脚本'}",
            "",
            "本地归档配置:",
            f"服务器: {profile.get('serverAddr')}:{profile.get('serverPort')}",
            f"映射: 127.0.0.1:{profile.get('localPort')} -> {profile.get('remotePort')}",
            f"Token: {'已保存' if profile.get('token') else '未配置'}",
            f"归档创建时间: {profile.get('createdAt', '未记录')}",
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
        text = tk.Text(dialog, wrap="none")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", "\n".join(lines))
        text.configure(state="disabled")
        self.set_status(f"已打开设备详情: {serial}")

    def show_profile_details(self) -> None:
        profile = normalize_profile(self.profiles.get(self.selected_profile, {}), self.selected_profile)
        details = [
            f"设备归档名称: {profile.get('profileName', self.selected_profile)}",
            f"创建时间: {profile.get('createdAt', '未记录')}",
            f"最后修改: {profile.get('updatedAt', '未记录')}",
            f"第一次安装（手机时间）: {profile.get('firstInstalledAt', '尚未安装')}",
            f"第一次安装设备: {profile.get('firstInstalledSerial', '未记录')}",
            "",
            "第一次安装时的手机配置:",
            str(profile.get("firstInstalledDeviceInfo", "尚未记录")),
        ]
        dialog = tk.Toplevel(self)
        dialog.title(f"设备归档详情 - {self.selected_profile}")
        dialog.geometry("820x620")
        text = tk.Text(dialog, wrap="none")
        text.pack(fill="both", expand=True, padx=8, pady=8)
        text.insert("1.0", "\n".join(details))
        text.configure(state="disabled")

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

    def choose_output_directory(self) -> str:
        default = ROOT / "personalized"
        default.mkdir(parents=True, exist_ok=True)
        return filedialog.askdirectory(title="选择生成文件保存位置", initialdir=str(default))

    def install_adb_only(self) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            messagebox.showerror("安装失败", "请先在左下列表选择在线 ADB 设备。")
            return
        if not self.save_profile():
            return
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

    def install(self) -> None:
        serial = self.vars["serial"].get().strip()
        if not serial:
            messagebox.showerror("安装失败", "请先选择在线 ADB 设备。")
            return
        if not self.save_profile():
            return
        installer = str(ROOT / "install-frpc-service.bat")
        profile_name = self.selected_profile
        def execute_install() -> tuple[int, str]:
            # 安装前采集快照；只有安装命令成功后才把它写入“第一次安装”记录。
            snapshot = device_info(serial)
            code, output = run_command(["cmd.exe", "/d", "/c", installer, serial], 180)
            if code == 0:
                installed = installed_config(serial)
                phone_install_time = installed.get("frpcInstalledAt") or installed.get("adbInstalledAt") or "未记录"
                self.record_first_install(profile_name, serial, snapshot, str(phone_install_time))
            return code, output
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
            if code == 0:
                self.output_queue.put(("device_config", (serial, installed_config(serial), device_identity(serial))))
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
            if result[0] == 0:
                self.output_queue.put(("device_config", (serial, installed_config(serial), device_identity(serial))))
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
                    serial, result, identity = output  # type: ignore[misc]
                    self.apply_device_config(serial, result, identity)
                elif kind == "auto_name":
                    self.apply_auto_name(output)  # type: ignore[arg-type]
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
