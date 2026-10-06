package com.voiceguard.app.audio

import android.annotation.SuppressLint
import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.isActive
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext

/**
 * Records 16 kHz mono audio until [stop] is called or [maxSeconds] passes.
 * [onLevel] gets the loudness (dBFS) every ~50 ms for the UI meter and simple voice-activity detection.
 */
class Recorder(private val source: Int = MediaRecorder.AudioSource.MIC) {
    @Volatile private var running = false
    var allSilent = true
        private set

    fun stop() { running = false }

    @SuppressLint("MissingPermission")
    suspend fun record(maxSeconds: Int = 30, onLevel: (Double) -> Unit = {}): ShortArray = withContext(Dispatchers.IO) {
        val min = AudioRecord.getMinBufferSize(Wav.SR, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
        val rec = AudioRecord(source, Wav.SR, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT, maxOf(min, 3200))
        val out = ShortArray(Wav.SR * maxSeconds)
        var n = 0
        val buf = ShortArray(800)
        running = true
        allSilent = true
        rec.startRecording()
        try {
            while (running && coroutineContext.isActive && n < out.size) {
                val r = rec.read(buf, 0, buf.size)
                if (r <= 0) continue
                val take = minOf(r, out.size - n)
                System.arraycopy(buf, 0, out, n, take)
                n += take
                val db = Wav.rmsDb(buf, r)
                if (db > -80) allSilent = false   // Android hands back zeros when capture is blocked (e.g. during a call)
                onLevel(db)
            }
        } finally {
            rec.stop()
            rec.release()
            running = false
        }
        out.copyOf(n)
    }
}
