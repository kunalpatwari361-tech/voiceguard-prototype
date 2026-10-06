package com.voiceguard.app.audio

import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder
import kotlin.math.log10
import kotlin.math.sqrt

/** 16 kHz mono PCM16 helpers. */
object Wav {
    const val SR = 16000

    fun encode(pcm: ShortArray, sr: Int = SR): ByteArray {
        val data = ByteBuffer.allocate(pcm.size * 2).order(ByteOrder.LITTLE_ENDIAN)
        pcm.forEach { data.putShort(it) }
        val body = data.array()
        val h = ByteBuffer.allocate(44).order(ByteOrder.LITTLE_ENDIAN)
        h.put("RIFF".toByteArray()).putInt(36 + body.size).put("WAVE".toByteArray())
        h.put("fmt ".toByteArray()).putInt(16).putShort(1).putShort(1).putInt(sr).putInt(sr * 2)
            .putShort(2).putShort(16)
        h.put("data".toByteArray()).putInt(body.size)
        return ByteArrayOutputStream().apply { write(h.array()); write(body) }.toByteArray()
    }

    /** Parse a PCM16 WAV produced by the server/demo clips (mono, any rate). */
    fun decode(bytes: ByteArray): Pair<ShortArray, Int> {
        val bb = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN)
        var pos = 12
        var sr = SR
        var channels = 1
        while (pos + 8 <= bytes.size) {
            val id = String(bytes, pos, 4)
            val size = bb.getInt(pos + 4)
            if (id == "fmt ") {
                channels = bb.getShort(pos + 10).toInt()
                sr = bb.getInt(pos + 12)
            }
            if (id == "data") {
                val n = minOf(size, bytes.size - pos - 8) / 2
                val all = ShortArray(n) { bb.getShort(pos + 8 + it * 2) }
                return if (channels == 1) all to sr else ShortArray(n / channels) { all[it * channels] } to sr
            }
            pos += 8 + size
        }
        return ShortArray(0) to sr
    }

    fun rmsDb(buf: ShortArray, n: Int = buf.size): Double {
        if (n == 0) return -96.0
        var s = 0.0
        for (i in 0 until n) { val v = buf[i] / 32768.0; s += v * v }
        return 20 * log10(sqrt(s / n) + 1e-9)
    }
}
