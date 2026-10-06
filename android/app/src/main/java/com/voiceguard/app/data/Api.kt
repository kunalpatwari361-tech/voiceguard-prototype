package com.voiceguard.app.data

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import java.util.concurrent.TimeUnit

class ApiException(message: String, val code: Int = 0) : Exception(message)

/** VoiceGuard server client. All calls are suspend and run on the IO dispatcher. */
object Api {
    val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(6, TimeUnit.SECONDS)
        .readTimeout(150, TimeUnit.SECONDS)   // AI analysis on a laptop CPU can take a while
        .writeTimeout(60, TimeUnit.SECONDS)
        .pingInterval(20, TimeUnit.SECONDS)
        .build()

    private val JSON_TYPE = "application/json".toMediaType()
    private val WAV_TYPE = "audio/wav".toMediaType()

    fun url(path: String) = Prefs.serverUrl + path

    suspend fun get(path: String): JsonElement = call(Request.Builder().url(url(path)).get().build())

    suspend fun post(path: String, body: JsonObject = JsonObject(emptyMap()), headers: Map<String, String> = emptyMap()): JsonElement {
        val b = Request.Builder().url(url(path)).post(body.toString().toRequestBody(JSON_TYPE))
        headers.forEach { (k, v) -> b.header(k, v) }
        return call(b.build())
    }

    suspend fun delete(path: String): JsonElement = call(Request.Builder().url(url(path)).delete().build())

    suspend fun upload(path: String, fields: Map<String, Any?>, wav: ByteArray?, fileName: String = "audio.wav"): JsonElement {
        val mb = MultipartBody.Builder().setType(MultipartBody.FORM)
        fields.forEach { (k, v) -> if (v != null) mb.addFormDataPart(k, v.toString()) }
        if (wav != null) mb.addFormDataPart("file", fileName, wav.toRequestBody(WAV_TYPE))
        return call(Request.Builder().url(url(path)).post(mb.build()).build())
    }

    suspend fun uploadForBytes(path: String, fields: Map<String, Any?>, wav: ByteArray): ByteArray = withContext(Dispatchers.IO) {
        val mb = MultipartBody.Builder().setType(MultipartBody.FORM)
        fields.forEach { (k, v) -> if (v != null) mb.addFormDataPart(k, v.toString()) }
        mb.addFormDataPart("file", "audio.wav", wav.toRequestBody(WAV_TYPE))
        client.newCall(Request.Builder().url(url(path)).post(mb.build()).build()).execute().use {
            if (!it.isSuccessful) throw ApiException("Server error ${it.code}", it.code)
            it.body!!.bytes()
        }
    }

    suspend fun bytes(path: String): ByteArray = withContext(Dispatchers.IO) {
        client.newCall(Request.Builder().url(url(path)).build()).execute().use {
            if (!it.isSuccessful) throw ApiException("Download failed ${it.code}", it.code)
            it.body!!.bytes()
        }
    }

    private suspend fun call(req: Request): JsonElement = withContext(Dispatchers.IO) {
        try {
            client.newCall(req).execute().use { resp ->
                val text = resp.body?.string().orEmpty()
                if (!resp.isSuccessful) {
                    val detail = runCatching { VgJson.parseToJsonElement(text).asObj().str("detail") }.getOrNull()
                    throw ApiException(detail ?: "Server error ${resp.code}", resp.code)
                }
                VgJson.parseToJsonElement(text.ifBlank { "{}" })
            }
        } catch (e: ApiException) {
            throw e
        } catch (e: Exception) {
            throw ApiException("Cannot reach VoiceGuard server at ${Prefs.serverUrl}. Is the laptop server running and USB connected?")
        }
    }
}
