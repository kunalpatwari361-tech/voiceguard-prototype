package com.voiceguard.app.data

import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.setValue
import com.voiceguard.app.VgApp
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * App language. English and Hindi are written in the code (tr(en, hi)). Every other Indian language comes from
 * assets/i18n/<code>.json (English text -> translation, made by backend/scripts/make_translations.py), with English
 * as the fallback for any text that has no translation yet.
 */
object I18n {
    data class Lang(val code: String, val native: String, val english: String)

    /** The 22 languages of the Eighth Schedule of the Constitution, plus English. */
    val ALL = listOf(
        Lang("en", "English", "English"), Lang("hi", "हिन्दी", "Hindi"), Lang("bn", "বাংলা", "Bengali"),
        Lang("te", "తెలుగు", "Telugu"), Lang("mr", "मराठी", "Marathi"), Lang("ta", "தமிழ்", "Tamil"),
        Lang("ur", "اردو", "Urdu"), Lang("gu", "ગુજરાતી", "Gujarati"), Lang("kn", "ಕನ್ನಡ", "Kannada"),
        Lang("ml", "മലയാളം", "Malayalam"), Lang("or", "ଓଡ଼ିଆ", "Odia"), Lang("pa", "ਪੰਜਾਬੀ", "Punjabi"),
        Lang("as", "অসমীয়া", "Assamese"), Lang("mai", "मैथिली", "Maithili"), Lang("sat", "ᱥᱟᱱᱛᱟᱲᱤ", "Santali"),
        Lang("ks", "کٲشُر", "Kashmiri"), Lang("ne", "नेपाली", "Nepali"), Lang("sd", "سنڌي", "Sindhi"),
        Lang("kok", "कोंकणी", "Konkani"), Lang("doi", "डोगरी", "Dogri"), Lang("mni", "ꯃꯤꯇꯩꯂꯣꯟ", "Manipuri"),
        Lang("brx", "बड़ो", "Bodo"), Lang("sa", "संस्कृतम्", "Sanskrit"),
    )

    /** Bumped on every language change; tr() reads it, so every screen re-renders in the new language at once. */
    var version by mutableIntStateOf(0)
    private var table: Map<String, String> = emptyMap()
    private var loaded: String? = null

    /** Languages this build has text for (English and Hindi always). */
    val available: List<Lang> by lazy { ALL.filter { it.code == "en" || it.code == "hi" || hasFile(it.code) } }

    fun current(): Lang = ALL.firstOrNull { it.code == Prefs.lang } ?: ALL[0]

    fun select(code: String) {
        Prefs.lang = code
        ensure(code)
        version++
    }

    fun text(en: String, hi: String): String {
        check(version >= 0)   // reads the state, so Compose re-renders this text when the language changes
        return when (val code = Prefs.lang) {
            "en" -> en
            "hi" -> hi
            else -> { ensure(code); table[en] ?: en }
        }
    }

    /** Server texts arrive in English + Hindi; other languages use the shipped translation when there is one. */
    fun server(en: String?, hi: String?): String? {
        check(version >= 0)
        return when (val code = Prefs.lang) {
            "hi" -> hi ?: en
            "en" -> en ?: hi
            else -> { ensure(code); en?.let { table[it] } ?: en ?: hi }
        }
    }

    private fun ensure(code: String) {
        if (loaded == code) return
        table = if (code == "en" || code == "hi") emptyMap() else runCatching {
            val text = VgApp.instance.assets.open("i18n/$code.json").bufferedReader(Charsets.UTF_8).use { it.readText() }
            (VgJson.parseToJsonElement(text) as JsonObject).mapValues { it.value.jsonPrimitive.content }
        }.getOrDefault(emptyMap())
        loaded = code
    }

    private fun hasFile(code: String) =
        runCatching { VgApp.instance.assets.open("i18n/$code.json").close(); true }.getOrDefault(false)
}
