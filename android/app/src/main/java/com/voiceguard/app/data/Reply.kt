package com.voiceguard.app.data

/**
 * Answers to the server's questions ("Are you really calling?", HD call ringing) go over HTTPS, so they work
 * straight from a notification even when the app was closed and the live link has not reconnected yet.
 */
object Reply {
    suspend fun verify(requestId: String?, answer: String?): Boolean = runCatching {
        Api.post("/api/verify/answer", json("request_id" to requestId, "answer" to answer))
    }.isSuccess

    /** Returns the call's status after answering ("accepted", "declined", "missed"…), or null if it has ended. */
    suspend fun hd(callId: String?, accept: Boolean): String? = runCatching {
        Api.post("/api/hd/answer", json("call_id" to callId, "accept" to accept)).asObj().str("status")
    }.getOrNull()
}
