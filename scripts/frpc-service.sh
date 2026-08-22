#!/system/bin/sh

# Root service.d 启动模板。渲染后，设备上的脚本、目录、二进制和配置名
# 都只使用随机词组，不包含产品名称；源码模板保留清晰名称便于维护。
INSTALL_NAME='__INSTALL_NAME__'
SERVICE_SCRIPT="/data/adb/service.d/$INSTALL_NAME.sh"
INSTALL_DIR='__INSTALL_ROOT__'
BIN="$INSTALL_DIR/$INSTALL_NAME"
CONFIG="$INSTALL_DIR/$INSTALL_NAME.toml"
ADB_SCRIPT='__ADB_SCRIPT__'
SUPERVISOR_PID="$INSTALL_DIR/supervisor.pid"
CHILD_PID="$INSTALL_DIR/child.pid"
SCHEDULE_PID="$INSTALL_DIR/schedule.pid"
LOG="$INSTALL_DIR/__LOG_NAME__"
LOG_ENABLED='__LOG_ENABLED__'
SCHEDULE_ENABLED='__SCHEDULE_ENABLED__'
SCHEDULE_INTERVAL='__SCHEDULE_INTERVAL__'
STATE=/data/adb/service-state
SOURCE_DIR=${0%/*}
SOURCE_BIN="$SOURCE_DIR/$INSTALL_NAME"
SOURCE_CONFIG="$SOURCE_DIR/$INSTALL_NAME.toml"

matches() {
    pid=$1
    expected=$2
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null || return 1
    tr '\0' '\n' < "/proc/$pid/cmdline" 2>/dev/null | grep -Fx "$expected" >/dev/null
}

stop_pid() {
    file=$1
    expected=$2
    [ -s "$file" ] || return 0
    pid=$(cat "$file" 2>/dev/null)
    if matches "$pid" "$expected"; then
        kill "$pid" 2>/dev/null
        sleep 2
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$file"
}

stop_background_pid() {
    file=$1
    [ -s "$file" ] || return 0
    pid=$(cat "$file" 2>/dev/null)
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        kill "$pid" 2>/dev/null
        sleep 1
        kill -9 "$pid" 2>/dev/null
    fi
    rm -f "$file"
}

install_files() {
    [ -f "$SOURCE_BIN" ] || { echo "binary missing: $SOURCE_BIN" >&2; return 1; }
    [ -f "$SOURCE_CONFIG" ] || { echo "config missing: $SOURCE_CONFIG" >&2; return 1; }

    old_service=$(sed -n '1p' "$STATE" 2>/dev/null)
    old_dir=$(sed -n '2p' "$STATE" 2>/dev/null)
    if [ -n "$old_service" ] && [ -n "$old_dir" ] && [ "$old_service" != "$SERVICE_SCRIPT" ]; then
        old_name=${old_service##*/}
        old_name=${old_name%.sh}
        stop_pid "$old_dir/supervisor.pid" "$old_service"
        stop_pid "$old_dir/child.pid" "$old_dir/$old_name"
        case "$old_service" in /data/adb/service.d/*.sh) rm -f "$old_service" ;; esac
    fi
    mkdir -p /data/adb/service.d "$INSTALL_DIR" || return 1
    cp -f "$SOURCE_BIN" "$BIN" || return 1
    cp -f "$SOURCE_CONFIG" "$CONFIG" || return 1
    if [ -n "$ADB_SCRIPT" ]; then
        [ -f "$SOURCE_DIR/$ADB_SCRIPT" ] || { echo "ADB bootstrap script missing" >&2; return 1; }
        cp -f "$SOURCE_DIR/$ADB_SCRIPT" "$INSTALL_DIR/$ADB_SCRIPT" || return 1
        chmod 0700 "$INSTALL_DIR/$ADB_SCRIPT"
    fi
    cp -f "$0" "$SERVICE_SCRIPT" || return 1
    chown -R 0:0 "$INSTALL_DIR" "$SERVICE_SCRIPT"
    chmod 0700 "$INSTALL_DIR" "$BIN" "$SERVICE_SCRIPT"
    chmod 0600 "$CONFIG"
    if [ "$LOG_ENABLED" = "1" ]; then
        printf '%s\n%s\n%s\n' "$SERVICE_SCRIPT" "$INSTALL_DIR" "$LOG" > "$STATE"
    else
        printf '%s\n%s\n' "$SERVICE_SCRIPT" "$INSTALL_DIR" > "$STATE"
    fi
    chown 0:0 "$STATE"
    chmod 0600 "$STATE"
}

run_child() {
    printf '%s\n' "$$" > "$SUPERVISOR_PID"
    chmod 0600 "$SUPERVISOR_PID"
    child=
    cleanup() {
        [ -n "$child" ] && kill "$child" 2>/dev/null
        rm -f "$SUPERVISOR_PID" "$CHILD_PID"
        exit 0
    }
    trap cleanup HUP INT TERM
    while true; do
        if [ "$LOG_ENABLED" = "1" ]; then
            "$BIN" -c "$CONFIG" </dev/null >> "$LOG" 2>&1 &
        else
            "$BIN" -c "$CONFIG" </dev/null >/dev/null 2>&1 &
        fi
        child=$!
        printf '%s\n' "$child" > "$CHILD_PID"
        wait "$child"
        code=$?
        rm -f "$CHILD_PID"
        if [ "$LOG_ENABLED" = "1" ]; then
            printf '%s child exited: %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$code" >> "$LOG"
        fi
        sleep 10
    done
}

run_schedule() {
    printf '%s\n' "$$" > "$SCHEDULE_PID"
    chmod 0600 "$SCHEDULE_PID"
    trap 'rm -f "$SCHEDULE_PID"; exit 0' HUP INT TERM
    while [ "$SCHEDULE_ENABLED" = "1" ] && [ "$SCHEDULE_INTERVAL" -ge 10 ] 2>/dev/null; do
        sleep "$SCHEDULE_INTERVAL"
# BEGIN USER SCHEDULE BODY
__SCHEDULE_BODY__
# END USER SCHEDULE BODY
    done
    rm -f "$SCHEDULE_PID"
}

start_schedule() {
    [ "$SCHEDULE_ENABLED" = "1" ] || return 0
    case "$SCHEDULE_INTERVAL" in ''|*[!0-9]*) return 1 ;; esac
    [ "$SCHEDULE_INTERVAL" -ge 10 ] 2>/dev/null || return 1
    if [ -s "$SCHEDULE_PID" ] && kill -0 "$(cat "$SCHEDULE_PID" 2>/dev/null)" 2>/dev/null; then
        return 0
    fi
    rm -f "$SCHEDULE_PID"
    nohup setsid "$SERVICE_SCRIPT" --schedule </dev/null >/dev/null 2>&1 &
}

start() {
    [ -s "$SUPERVISOR_PID" ] && matches "$(cat "$SUPERVISOR_PID")" "$SERVICE_SCRIPT" && return 0
    rm -f "$SUPERVISOR_PID" "$CHILD_PID"
    nohup setsid "$SERVICE_SCRIPT" --run </dev/null >/dev/null 2>&1 &
    sleep 2
    matches "$(cat "$SUPERVISOR_PID" 2>/dev/null)" "$SERVICE_SCRIPT" && start_schedule
}

run_adb_bootstrap() {
    [ -n "$ADB_SCRIPT" ] || return 0
    [ -f "$INSTALL_DIR/$ADB_SCRIPT" ] || return 1
    # ADB 脚本会重启 adbd，放在 FRPC 已启动之后执行，避免 FRPC 服务没有注册成功。
    sh "$INSTALL_DIR/$ADB_SCRIPT" >/dev/null 2>&1 &
}

stop() {
    stop_background_pid "$SCHEDULE_PID"
    stop_pid "$SUPERVISOR_PID" "$SERVICE_SCRIPT"
    stop_pid "$CHILD_PID" "$BIN"
}

case "${1:-start}" in
    --run) run_child ;;
    --schedule) run_schedule ;;
    start)
        script_path=$(readlink -f "$0" 2>/dev/null)
        [ -n "$script_path" ] || script_path=$0
        if [ "$script_path" = "$SERVICE_SCRIPT" ]; then
            start
        else
            install_files && start && run_adb_bootstrap
        fi
        ;;
    restart)
        script_path=$(readlink -f "$0" 2>/dev/null)
        [ -n "$script_path" ] || script_path=$0
        if [ "$script_path" != "$SERVICE_SCRIPT" ]; then install_files || exit 1; fi
        stop && start && run_adb_bootstrap
        ;;
    stop) stop ;;
    status)
        matches "$(cat "$SUPERVISOR_PID" 2>/dev/null)" "$SERVICE_SCRIPT" || exit 1
        echo "running"
        ;;
    logs) tail -n "${2:-80}" "$LOG" 2>/dev/null ;;
    uninstall) stop; rm -f "$SERVICE_SCRIPT" "$STATE"; echo "unregistered" ;;
    *) echo "usage: $0 {start|restart|stop|status|logs [lines]|uninstall}" >&2; exit 2 ;;
esac
