package com.voiceguard.app.data

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.provider.Telephony
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class SmsMsg(val id: Long, val thread: Long, val address: String, val body: String, val date: Long,
                  val incoming: Boolean, val read: Boolean)

data class SmsThread(val thread: Long, val address: String, val last: SmsMsg, val unread: Int, val count: Int,
                     val worst: SmsGuard.Verdict)

/**
 * The phone's SMS inbox, read on the phone (nothing is uploaded). VoiceGuard is not the SMS app: it shows the
 * messages, checks them for scam tricks and can reply; sent replies are stored by Android in the normal inbox.
 */
object Sms {
    fun canRead(ctx: Context) = ctx.checkSelfPermission(Manifest.permission.READ_SMS) == PackageManager.PERMISSION_GRANTED

    private val COLS = arrayOf(Telephony.Sms._ID, Telephony.Sms.THREAD_ID, Telephony.Sms.ADDRESS, Telephony.Sms.BODY,
        Telephony.Sms.DATE, Telephony.Sms.TYPE, Telephony.Sms.READ)

    private fun query(ctx: Context, where: String?, args: Array<String>?, limit: Int): List<SmsMsg> {
        if (!canRead(ctx)) return emptyList()
        val out = ArrayList<SmsMsg>()
        runCatching {
            ctx.contentResolver.query(Telephony.Sms.CONTENT_URI, COLS, where, args, Telephony.Sms.DATE + " DESC")?.use { c ->
                while (c.moveToNext() && out.size < limit) {
                    val type = c.getInt(5)
                    if (type != Telephony.Sms.MESSAGE_TYPE_INBOX && type != Telephony.Sms.MESSAGE_TYPE_SENT) continue
                    out.add(SmsMsg(c.getLong(0), c.getLong(1), c.getString(2).orEmpty(), c.getString(3).orEmpty(), c.getLong(4),
                        type == Telephony.Sms.MESSAGE_TYPE_INBOX, c.getInt(6) == 1))
                }
            }
        }
        return out
    }

    /** Conversations, newest first, each with its riskiest incoming message. */
    suspend fun threads(ctx: Context, scan: Int = 1500): List<SmsThread> = withContext(Dispatchers.IO) {
        query(ctx, null, null, scan).groupBy { it.thread }.map { (t, msgs) ->
            val worst = msgs.filter { it.incoming }.take(20).map { SmsGuard.check(it.body, it.address) }
                .maxByOrNull { it.score } ?: SmsGuard.Verdict.NONE
            SmsThread(t, msgs.first().address, msgs.first(), msgs.count { it.incoming && !it.read }, msgs.size, worst)
        }.sortedByDescending { it.last.date }
    }

    /** One conversation, oldest first (chat order). */
    suspend fun messages(ctx: Context, thread: Long): List<SmsMsg> = withContext(Dispatchers.IO) {
        query(ctx, Telephony.Sms.THREAD_ID + "=?", arrayOf(thread.toString()), 500).reversed()
    }

    /** Same sender? Phone numbers compare by digits; letter headers (VM-HDFCBK) compare as text. */
    fun sameAddress(a: String, b: String): Boolean =
        if (a.any { it.isLetter() } || b.any { it.isLetter() }) a.equals(b, ignoreCase = true)
        else Numbers.normalize(a) == Numbers.normalize(b)

    /** Messages exchanged with one sender (a conversation, or the caller on the call screen), oldest first. */
    suspend fun withAddress(ctx: Context, address: String, limit: Int = 300): List<SmsMsg> = withContext(Dispatchers.IO) {
        query(ctx, null, null, 3000).filter { sameAddress(it.address, address) }.take(limit).reversed()
    }

    suspend fun unread(ctx: Context): Int = withContext(Dispatchers.IO) {
        if (!canRead(ctx)) return@withContext 0
        runCatching {
            ctx.contentResolver.query(Telephony.Sms.Inbox.CONTENT_URI, arrayOf(Telephony.Sms._ID), Telephony.Sms.READ + "=0", null, null)
                ?.use { it.count } ?: 0
        }.getOrDefault(0)
    }
}
