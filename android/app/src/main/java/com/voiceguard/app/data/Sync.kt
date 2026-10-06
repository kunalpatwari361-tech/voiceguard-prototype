package com.voiceguard.app.data

import kotlinx.serialization.json.JsonObject

/** Keeps offline copies of the scam list, blocked numbers and family so call screening works instantly. */
object Sync {
    suspend fun lists() {
        runCatching {
            Prefs.scamNumbers = Api.get("/api/scamlist").asList().mapNotNull { it.str("number") }.toSet()
        }
        val uid = Prefs.userId ?: return
        runCatching {
            Prefs.blockedNumbers = Api.get("/api/blocked/$uid").asList().mapNotNull { it.str("number") }.toSet()
        }
    }

    suspend fun family(): JsonObject? {
        val fid = Prefs.familyId ?: return null
        val f = Api.get("/api/family/$fid").asObj() ?: return null
        Prefs.familyJson = f.toString()
        return f
    }

    fun cachedFamily(): JsonObject? =
        Prefs.familyJson?.let { runCatching { VgJson.parseToJsonElement(it).asObj() }.getOrNull() }

    /** Family members other than me. */
    fun others(): List<JsonObject> = cachedFamily().objs("members").filter { it.str("id") != Prefs.userId }

    fun memberByPhone(number: String?): JsonObject? {
        val n = Numbers.normalize(number ?: return null)
        return cachedFamily().objs("members").firstOrNull { it.str("phone") == n }
    }

    fun member(id: String?): JsonObject? = cachedFamily().objs("members").firstOrNull { it.str("id") == id }
}

object Numbers {
    fun normalize(raw: String): String {
        var n = raw.filter { it.isDigit() || it == '+' }
        if (n.startsWith("00")) n = "+" + n.drop(2)
        if (n.startsWith("0") && n.length == 11) n = "+91" + n.drop(1)
        if (n.length == 10 && n[0] in "6789") n = "+91$n"
        return n
    }

    fun pretty(n: String?): String {
        if (n.isNullOrBlank()) return "Unknown number"
        return if (n.startsWith("+91") && n.length == 13) "+91 ${n.substring(3, 8)} ${n.substring(8)}" else n
    }
}
