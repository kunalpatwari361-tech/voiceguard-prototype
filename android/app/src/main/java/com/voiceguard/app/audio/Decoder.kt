package com.voiceguard.app.audio

import android.content.Context
import android.media.MediaCodec
import android.media.MediaExtractor
import android.media.MediaFormat
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.nio.ByteOrder

/** Decodes any audio the phone can play (WhatsApp .opus, .m4a, .mp3, .aac, .ogg, .wav) to 16 kHz mono PCM. */
object Decoder {
    suspend fun decode(ctx: Context, uri: Uri, maxSeconds: Int = 60): ShortArray = withContext(Dispatchers.IO) {
        val ex = MediaExtractor()
        ex.setDataSource(ctx, uri, null)
        val track = (0 until ex.trackCount).firstOrNull {
            ex.getTrackFormat(it).getString(MediaFormat.KEY_MIME)?.startsWith("audio/") == true
        } ?: error("No audio track in this file")
        ex.selectTrack(track)
        val fmt = ex.getTrackFormat(track)
        val codec = MediaCodec.createDecoderByType(fmt.getString(MediaFormat.KEY_MIME)!!)
        codec.configure(fmt, null, null, 0)
        codec.start()
        var sr = fmt.getInteger(MediaFormat.KEY_SAMPLE_RATE)
        var ch = fmt.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
        val mono = ArrayList<Short>(sr * 30)
        val info = MediaCodec.BufferInfo()
        var inputDone = false
        var outputDone = false
        while (!outputDone && mono.size < sr * maxSeconds) {
            if (!inputDone) {
                val i = codec.dequeueInputBuffer(10_000)
                if (i >= 0) {
                    val n = ex.readSampleData(codec.getInputBuffer(i)!!, 0)
                    if (n < 0) {
                        codec.queueInputBuffer(i, 0, 0, 0, MediaCodec.BUFFER_FLAG_END_OF_STREAM)
                        inputDone = true
                    } else {
                        codec.queueInputBuffer(i, 0, n, ex.sampleTime, 0)
                        ex.advance()
                    }
                }
            }
            val o = codec.dequeueOutputBuffer(info, 10_000)
            when {
                o == MediaCodec.INFO_OUTPUT_FORMAT_CHANGED -> {
                    sr = codec.outputFormat.getInteger(MediaFormat.KEY_SAMPLE_RATE)
                    ch = codec.outputFormat.getInteger(MediaFormat.KEY_CHANNEL_COUNT)
                }
                o >= 0 -> {
                    val sb = codec.getOutputBuffer(o)!!.order(ByteOrder.LITTLE_ENDIAN).asShortBuffer()
                    val frames = sb.remaining() / ch
                    for (f in 0 until frames) {
                        var s = 0
                        for (c in 0 until ch) s += sb.get(f * ch + c)
                        mono.add((s / ch).toShort())
                    }
                    codec.releaseOutputBuffer(o, false)
                    if (info.flags and MediaCodec.BUFFER_FLAG_END_OF_STREAM != 0) outputDone = true
                }
            }
        }
        codec.stop(); codec.release(); ex.release()
        resample(mono.toShortArray(), sr, Wav.SR)
    }

    fun resample(x: ShortArray, from: Int, to: Int): ShortArray {
        if (from == to || x.isEmpty()) return x
        val n = (x.size.toLong() * to / from).toInt()
        val ratio = from.toDouble() / to
        return ShortArray(n) { i ->
            val pos = i * ratio
            val a = pos.toInt().coerceAtMost(x.size - 1)
            val b = (a + 1).coerceAtMost(x.size - 1)
            val t = pos - a
            (x[a] * (1 - t) + x[b] * t).toInt().toShort()
        }
    }
}
