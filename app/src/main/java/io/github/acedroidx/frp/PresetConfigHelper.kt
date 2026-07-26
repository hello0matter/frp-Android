package io.github.acedroidx.frp

import android.content.Context
import androidx.core.content.edit
import java.util.UUID

/** 安装后创建一个可编辑的预置 frpc 配置，并初始化其自启动设置。 */
object PresetConfigHelper {
    private const val PREF_PRESET_VERSION = "preset_config_version"
    private const val CURRENT_PRESET_VERSION = 3

    fun isEnabled(): Boolean = BuildConfig.PresetFrpcEnabled

    fun getPresetConfig(): FrpConfig = FrpConfig(FrpType.FRPC, BuildConfig.PresetFrpcFileName)

    fun ensureInitialized(context: Context) {
        if (!isEnabled()) return

        val preferences = context.getSharedPreferences("data", Context.MODE_PRIVATE)
        val configFile = getPresetConfig().getFile(context)
        val previousVersion = preferences.getInt(PREF_PRESET_VERSION, 0)

        configFile.parentFile?.mkdirs()
        when {
            !configFile.exists() -> configFile.writeText(buildPresetConfigContent())
            previousVersion < CURRENT_PRESET_VERSION -> {
                // 只清理旧版自动生成的说明和空 token 占位，不覆盖用户已修改的参数。
                configFile.writeText(
                    migrateLegacyPresetConfig(configFile.readText(), previousVersion)
                )
            }
        }

        if (previousVersion < CURRENT_PRESET_VERSION) {
            preferences.edit {
                val autoStartConfigs = preferences.getStringSet(
                    PreferencesKey.AUTO_START_FRPC_LIST,
                    emptySet()
                ).orEmpty() + BuildConfig.PresetFrpcFileName

                putStringSet(PreferencesKey.AUTO_START_FRPC_LIST, autoStartConfigs)
                putBoolean(PreferencesKey.AUTO_START, BuildConfig.PresetAutoStartOnBoot)
                putBoolean(PreferencesKey.AUTO_START_LAUNCH, BuildConfig.PresetAutoStartOnLaunch)
                putString(PreferencesKey.QUICK_TILE_CONFIG_TYPE, FrpType.FRPC.name)
                putString(PreferencesKey.QUICK_TILE_CONFIG_NAME, BuildConfig.PresetFrpcFileName)
                putInt(PREF_PRESET_VERSION, CURRENT_PRESET_VERSION)
            }
        }
    }

    private fun buildPresetConfigContent(): String {
        val tokenLine = BuildConfig.PresetToken.takeIf(String::isNotBlank)?.let {
            "auth.token = \"$it\"\n"
        }.orEmpty()

        return """
serverAddr = "${BuildConfig.PresetServerAddr}"
serverPort = ${BuildConfig.PresetServerPort}
${tokenLine}loginFailExit = false

[log]
level = "${BuildConfig.PresetLogLevel}"
disablePrintColor = true

[[proxies]]
name = "${generateProxyName()}"
type = "tcp"
localIP = "${BuildConfig.PresetLocalIp}"
localPort = ${BuildConfig.PresetLocalPort}
remotePort = ${BuildConfig.PresetRemotePort}
""".trimIndent()
    }

    private fun migrateLegacyPresetConfig(content: String, previousVersion: Int): String {
        var migrated = content.lineSequence()
            .filterNot { line ->
                val trimmed = line.trim()
                val legacyTokenPlaceholder =
                    (trimmed.startsWith("token =") || trimmed.startsWith("auth.token =")) &&
                        trimmed.contains("token", ignoreCase = true) &&
                        trimmed.any { it.code > 127 }
                trimmed.startsWith("#") || legacyTokenPlaceholder
            }
            .joinToString("\n")
            .trim()

        // 旧版默认名称只迁移一次；用户已经手动设置的名称保持不变。
        if (previousVersion < 3) {
            migrated = migrated.replace(
                Regex("(?m)^name\\s*=\\s*\"preset_tcp\"\\s*$"),
                "name = \"${generateProxyName()}\""
            )
        }
        return "$migrated\n"
    }

    private fun generateProxyName(): String =
        "device_${UUID.randomUUID().toString().replace("-", "").take(12)}"
}
