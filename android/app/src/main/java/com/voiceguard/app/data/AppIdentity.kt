package com.voiceguard.app.data

import android.content.pm.ApplicationInfo
import android.content.pm.PackageManager
import com.voiceguard.app.VgApp
import java.security.MessageDigest

/**
 * Castle layer 7 (Royal guard): every request says which app made it - "version;signing-certificate-SHA-256;debuggable" -
 * so the server can spot a modified or re-signed copy of VoiceGuard (VG_APP_CERTS / VG_APP_TRUST_MODE on the server).
 */
object AppIdentity {
    val header: String by lazy {
        runCatching {
            val ctx = VgApp.instance
            val pm = ctx.packageManager
            val info = pm.getPackageInfo(ctx.packageName, PackageManager.GET_SIGNING_CERTIFICATES)
            val cert = info.signingInfo?.apkContentsSigners?.firstOrNull()?.toByteArray()
            val sha = cert?.let { MessageDigest.getInstance("SHA-256").digest(it).joinToString("") { b -> "%02x".format(b) } } ?: "none"
            val debuggable = (ctx.applicationInfo.flags and ApplicationInfo.FLAG_DEBUGGABLE) != 0
            "${info.versionName};$sha;${if (debuggable) 1 else 0}"
        }.getOrDefault("unknown;none;0")
    }
}
