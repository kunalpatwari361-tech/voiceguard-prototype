package com.voiceguard.app.data

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/** Tiny dynamic-JSON helpers: the server's analysis reports are large and nested. */
val VgJson = Json { ignoreUnknownKeys = true; isLenient = true }

fun JsonElement?.str(): String? = (this as? JsonPrimitive)?.takeUnless { it is JsonNull }?.content
fun JsonElement?.asObj(): JsonObject? = this as? JsonObject
fun JsonElement?.asList(): List<JsonObject> = (this as? JsonArray)?.mapNotNull { it as? JsonObject } ?: emptyList()

fun JsonObject?.str(k: String): String? = this?.get(k).str()
fun JsonObject?.num(k: String): Double? = str(k)?.toDoubleOrNull()
fun JsonObject?.int(k: String): Int? = num(k)?.toInt()
fun JsonObject?.bool(k: String): Boolean? = str(k)?.toBooleanStrictOrNull()
fun JsonObject?.obj(k: String): JsonObject? = this?.get(k) as? JsonObject
fun JsonObject?.arr(k: String): List<JsonElement> = (this?.get(k) as? JsonArray) ?: emptyList()
fun JsonObject?.objs(k: String): List<JsonObject> = arr(k).mapNotNull { it as? JsonObject }

/** Pick the Hindi variant of a server text when the user prefers Hindi. */
fun JsonObject?.bi(en: String, hi: String): String? =
    if (Prefs.hindi) str(hi) ?: str(en) else str(en) ?: str(hi)

fun Any?.toJsonElement(): JsonElement = when (this) {
    null -> JsonNull
    is JsonElement -> this
    is String -> JsonPrimitive(this)
    is Number -> JsonPrimitive(this)
    is Boolean -> JsonPrimitive(this)
    is Map<*, *> -> JsonObject(entries.associate { (k, v) -> k.toString() to v.toJsonElement() })
    is Iterable<*> -> JsonArray(map { it.toJsonElement() })
    else -> JsonPrimitive(toString())
}

fun json(vararg pairs: Pair<String, Any?>): JsonObject =
    JsonObject(pairs.associate { (k, v) -> k to v.toJsonElement() })
