package com.voiceguard.app.audio

import android.annotation.SuppressLint
import android.content.Context
import android.media.AudioAttributes
import android.media.AudioFormat
import android.media.AudioManager
import android.media.AudioRecord
import android.media.AudioTrack
import android.media.MediaRecorder
import android.media.audiofx.AcousticEchoCanceler
import com.voiceguard.app.data.Api
import com.voiceguard.app.data.Prefs
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import okio.ByteString
import okio.ByteString.Companion.toByteString
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * VoiceGuard HD Call audio: 16 kHz wideband PCM over a WebSocket relay (twice the bandwidth of a
 * normal 8 kHz phone call), with the phone's echo canceller in VOICE_COMMUNICATION mode.
 */
class HdAudio(private val ctx: Context, private val callId: String) {
    val level = MutableStateFlow(-96.0)
    val remoteLevel = MutableStateFlow(-96.0)
    val connected = MutableStateFlow(false)
    var muted = false

    private var ws: WebSocket? = null
    private var track: AudioTrack? = null
    private var job: Job? = null
    private val scope = CoroutineScope(Dispatchers.IO)

    @SuppressLint("MissingPermission")
    fun start(speaker: Boolean = true) {
        val am = ctx.getSystemService(AudioManager::class.java)
        am.mode = AudioManager.MODE_IN_COMMUNICATION
        @Suppress("DEPRECATION")
        am.isSpeakerphoneOn = speaker
        val outMin = AudioTrack.getMinBufferSize(Wav.SR, AudioFormat.CHANNEL_OUT_MONO, AudioFormat.ENCODING_PCM_16BIT)
        track = AudioTrack.Builder()
            .setAudioAttributes(AudioAttributes.Builder().setUsage(AudioAttributes.USAGE_VOICE_COMMUNICATION)
                .setContentType(AudioAttributes.CONTENT_TYPE_SPEECH).build())
            .setAudioFormat(AudioFormat.Builder().setSampleRate(Wav.SR).setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                .setChannelMask(AudioFormat.CHANNEL_OUT_MONO).build())
            .setBufferSizeInBytes(maxOf(outMin, 6400)).setTransferMode(AudioTrack.MODE_STREAM).build()
            .also { it.play() }

        val url = Prefs.serverUrl.replaceFirst("http", "ws") + "/ws/hd/$callId/${Prefs.userId}?token=" +
            android.net.Uri.encode(Prefs.token.orEmpty())
        ws = Api.client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onOpen(webSocket: WebSocket, response: Response) { connected.value = true }
            override fun onMessage(webSocket: WebSocket, bytes: ByteString) {
                val arr = bytes.toByteArray()
                val pcm = ShortArray(arr.size / 2)
                ByteBuffer.wrap(arr).order(ByteOrder.LITTLE_ENDIAN).asShortBuffer().get(pcm)
                remoteLevel.value = Wav.rmsDb(pcm)
                track?.write(pcm, 0, pcm.size)
            }
            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) { connected.value = false }
            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) { connected.value = false }
        })

        job = scope.launch {
            val inMin = AudioRecord.getMinBufferSize(Wav.SR, AudioFormat.CHANNEL_IN_MONO, AudioFormat.ENCODING_PCM_16BIT)
            val rec = AudioRecord(MediaRecorder.AudioSource.VOICE_COMMUNICATION, Wav.SR, AudioFormat.CHANNEL_IN_MONO,
                AudioFormat.ENCODING_PCM_16BIT, maxOf(inMin, 3200))
            if (AcousticEchoCanceler.isAvailable()) AcousticEchoCanceler.create(rec.audioSessionId)?.enabled = true
            val frame = ShortArray(640) // 40 ms
            val bytes = ByteBuffer.allocate(frame.size * 2).order(ByteOrder.LITTLE_ENDIAN)
            rec.startRecording()
            try {
                while (isActive) {
                    val n = rec.read(frame, 0, frame.size)
                    if (n <= 0) continue
                    if (muted) frame.fill(0, 0, n)
                    level.value = Wav.rmsDb(frame, n)
                    bytes.clear()
                    for (i in 0 until n) bytes.putShort(frame[i])
                    ws?.send(bytes.array().copyOf(n * 2).toByteString())
                }
            } finally {
                rec.stop(); rec.release()
            }
        }
    }

    fun setSpeaker(on: Boolean) {
        @Suppress("DEPRECATION")
        ctx.getSystemService(AudioManager::class.java).isSpeakerphoneOn = on
    }

    fun stop() {
        job?.cancel()
        ws?.close(1000, null)
        track?.run { stop(); release() }
        track = null
        ctx.getSystemService(AudioManager::class.java).mode = AudioManager.MODE_NORMAL
    }
}
