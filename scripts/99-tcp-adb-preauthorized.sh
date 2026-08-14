#!/system/bin/sh

# Magisk、KernelSU 和 APatch 的 service.d 通用启动脚本。
# 启用经典 TCP ADB，并把指定电脑的公钥加入设备信任列表。

PORT=5555
ADB_KEYS_FILE=/data/misc/adb/adb_keys
INSTALL_NAME='__INSTALL_NAME__'
SERVICE_SCRIPT="/data/adb/service.d/$INSTALL_NAME.sh"
SERVICE_STATE_FILE=/data/adb/tcp-adb-service-path

# 公钥可以公开；对应的私钥 adbkey 必须只保存在授权电脑上。
TRUSTED_ADB_KEYS=$(cat <<'EOF'
__ADB_PUBLIC_KEY__
EOF
)

install_self() {
    script_path=$(readlink -f "$0" 2>/dev/null)
    [ -n "$script_path" ] || script_path=$0
    [ "$script_path" = "$SERVICE_SCRIPT" ] && return 0

    mkdir -p "${SERVICE_SCRIPT%/*}" || return 1
    cp -f "$script_path" "$SERVICE_SCRIPT" || return 1
    chown 0:0 "$SERVICE_SCRIPT"
    chmod 0700 "$SERVICE_SCRIPT"

    previous_service_script=$(cat "$SERVICE_STATE_FILE" 2>/dev/null)
    case "$previous_service_script" in
        /data/adb/service.d/*.sh)
            [ "$previous_service_script" = "$SERVICE_SCRIPT" ] ||
                rm -f "$previous_service_script"
            ;;
    esac

    legacy_service_script=/data/adb/service.d/99-tcp-adb.sh
    [ "$legacy_service_script" = "$SERVICE_SCRIPT" ] || rm -f "$legacy_service_script"
    printf '%s\n' "$SERVICE_SCRIPT" > "$SERVICE_STATE_FILE"
    chown 0:0 "$SERVICE_STATE_FILE"
    chmod 0600 "$SERVICE_STATE_FILE"
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

case "$INSTALL_NAME" in
    ''|*[!a-z-]*|-*|*-|*--*) template_is_valid=0 ;;
    *) template_is_valid=1 ;;
esac
case "$INSTALL_NAME" in
    *-*) ;;
    *) template_is_valid=0 ;;
esac
case "$TRUSTED_ADB_KEYS" in
    Q*) ;;
    *) template_is_valid=0 ;;
esac
if [ "$template_is_valid" != "1" ]; then
    exit 1
fi

if ! install_self; then
    exit 1
fi

if ! install_trusted_adb_keys; then
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

[ "$(getprop init.svc.adbd)" = "running" ] || exit 1
[ "$(getprop service.adb.tcp.port)" = "$PORT" ] || exit 1

exit 0
