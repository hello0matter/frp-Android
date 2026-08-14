#!/system/bin/sh

# Magisk、KernelSU 和 APatch 的 service.d 通用启动脚本。
# 启用经典 TCP ADB，并把指定电脑的公钥加入设备信任列表。

PORT=5555
LOG_FILE=/data/adb/tcp-adb-preauthorized.log
ADB_KEYS_FILE=/data/misc/adb/adb_keys
SERVICE_SCRIPT=/data/adb/service.d/99-tcp-adb.sh

# 公钥可以公开；对应的私钥 adbkey 必须只保存在授权电脑上。
TRUSTED_ADB_KEYS=$(cat <<'EOF'
QAAAANNmR7ClHpqT4mJmVYqxuIfgY8ZaSqpoYUmtpsp+1/Y4GeGy8Ji0rN76A4u9C0tQUK7XtGTs1ZGrHmP8vtg1C8nfkoYELSYblWz8SkiRHe+hkUEqOy+YBTSV9ezQwgziMnMlRuyIdOOszk5pR44Mk74LH0uHDoKqg/OrhuvgAObEzDeVB8OeRLEgghRqNI6NYTNdiySEk8QNmUQDdO6Zw/c8GpLzYjG06eSkyVGiLCdBhQdan1SHsRAnV7Pk8VWdFWbX+lhDioKpZrZ9uBs9HZRh23dR/B/3KB8j28DOC3DSjPHYN9UFMsu5bS60nYSJdzDmB4Z21e42mJnNBA+VpcsY4yrcBryAnsmXFet3tlQ74EXhdVk2C5rXUFb4qrCWQxTQBmRqzb0TNcGNZ+c+Lo3+98WeuQjUH/C8Xf9SY4iLEkm8m6x1hgWoc3VdwoDeG3oyntUfXjIwYL78lQwRNjANLk/muC0BEJ9brpxGYGZLDvDxSvU5s9wGWTZ5LM3umxnif2s6Jlrq9UKlTd9goT0VXk7E2q2wo5U54/hsv14nTIorzeLNT2OwiiVvmGvh/aCarzXbxDXx4wNX9KHCGI/FwgPJ0Dg6Xb6eiNAdFLyLyrC45uR9y07E7GWIhJYfuI5rErXHreS1VnS5n3prITd8Tf/t72Ib0g5rxTDyMjDwQOjiqQEAAQA= Administrator@XIAOXIONG
EOF
)

log() {
    printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG_FILE"
}

install_self() {
    script_path=$(readlink -f "$0" 2>/dev/null)
    [ -n "$script_path" ] || script_path=$0
    [ "$script_path" = "$SERVICE_SCRIPT" ] && return 0

    mkdir -p "${SERVICE_SCRIPT%/*}" || return 1
    cp -f "$script_path" "$SERVICE_SCRIPT" || return 1
    chown 0:0 "$SERVICE_SCRIPT"
    chmod 0700 "$SERVICE_SCRIPT"
    log "Installed boot script to $SERVICE_SCRIPT"
}

install_trusted_adb_keys() {
    [ -d "${ADB_KEYS_FILE%/*}" ] || return 1
    [ -f "$ADB_KEYS_FILE" ] || : > "$ADB_KEYS_FILE"

    printf '%s\n' "$TRUSTED_ADB_KEYS" | while IFS= read -r adb_key; do
        [ -n "$adb_key" ] || continue
        grep -qxF "$adb_key" "$ADB_KEYS_FILE" 2>/dev/null ||
            printf '%s\n' "$adb_key" >> "$ADB_KEYS_FILE"
    done

    chown system:shell "$ADB_KEYS_FILE"
    chmod 0640 "$ADB_KEYS_FILE"
    command -v restorecon >/dev/null 2>&1 && restorecon "$ADB_KEYS_FILE"
}

while [ "$(getprop sys.boot_completed)" != "1" ]; do
    sleep 2
done

if ! install_self; then
    log "Failed to install boot script"
    exit 1
fi

if ! install_trusted_adb_keys; then
    log "Failed to install trusted ADB keys"
    exit 1
fi

if command -v resetprop >/dev/null 2>&1; then
    resetprop service.adb.tcp.port "$PORT"
    resetprop -p persist.adb.tcp.port "$PORT"
else
    setprop service.adb.tcp.port "$PORT"
    setprop persist.adb.tcp.port "$PORT"
fi

# adbd 在启动时加载端口和信任公钥，因此写入后统一重启一次。
stop adbd 2>/dev/null
start adbd 2>/dev/null
sleep 2

adb_enabled=$(settings get global adb_enabled)
development_enabled=$(settings get global development_settings_enabled)
adb_state=$(getprop init.svc.adbd)
tcp_port=$(getprop service.adb.tcp.port)
adb_secure=$(getprop ro.adb.secure)
trusted_key_count=$(wc -l < "$ADB_KEYS_FILE" 2>/dev/null)

log "adb_enabled=$adb_enabled development_settings_enabled=$development_enabled adbd=$adb_state tcp_port=$tcp_port ro.adb.secure=$adb_secure trusted_key_count=$trusted_key_count"

[ "$adb_state" = "running" ] || exit 1
[ "$tcp_port" = "$PORT" ] || exit 1

exit 0
