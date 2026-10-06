package com.voiceguard.app.data

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.CallLog
import android.provider.ContactsContract
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

data class Contact(val name: String, val number: String, val normalized: String, val starred: Boolean)

data class CallEntry(val number: String, val name: String?, val type: Int, val date: Long, val durationS: Long)

/** The phone's own address book and call log (VoiceGuard Dialer, feature 1). */
object Contacts {
    fun canReadContacts(ctx: Context) =
        ctx.checkSelfPermission(Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    fun canReadLog(ctx: Context) =
        ctx.checkSelfPermission(Manifest.permission.READ_CALL_LOG) == PackageManager.PERMISSION_GRANTED

    @Volatile private var cache: List<Contact>? = null

    suspend fun all(ctx: Context, refresh: Boolean = false): List<Contact> = withContext(Dispatchers.IO) {
        if (!canReadContacts(ctx)) return@withContext emptyList()
        cache?.takeIf { !refresh }?.let { return@withContext it }
        val out = LinkedHashMap<String, Contact>()
        ctx.contentResolver.query(
            ContactsContract.CommonDataKinds.Phone.CONTENT_URI,
            arrayOf(ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME, ContactsContract.CommonDataKinds.Phone.NUMBER,
                ContactsContract.CommonDataKinds.Phone.STARRED),
            null, null, ContactsContract.CommonDataKinds.Phone.DISPLAY_NAME + " COLLATE NOCASE ASC",
        )?.use { c ->
            while (c.moveToNext()) {
                val name = c.getString(0) ?: continue
                val num = c.getString(1) ?: continue
                val n = Numbers.normalize(num)
                if (n.length < 5 || out.containsKey(n)) continue
                out[n] = Contact(name, num, n, c.getInt(2) == 1)
            }
        }
        out.values.toList().also { cache = it }
    }

    /** Caller ID: name saved in the phone for this number, if any. */
    fun nameFor(ctx: Context, number: String?): String? {
        if (number.isNullOrBlank() || !canReadContacts(ctx)) return null
        return runCatching {
            val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
            ctx.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME), null, null, null)?.use {
                if (it.moveToFirst()) it.getString(0) else null
            }
        }.getOrNull()
    }

    suspend fun callLog(ctx: Context, limit: Int = 150): List<CallEntry> = withContext(Dispatchers.IO) {
        if (!canReadLog(ctx)) return@withContext emptyList()
        val out = ArrayList<CallEntry>()
        ctx.contentResolver.query(
            CallLog.Calls.CONTENT_URI,
            arrayOf(CallLog.Calls.NUMBER, CallLog.Calls.CACHED_NAME, CallLog.Calls.TYPE, CallLog.Calls.DATE, CallLog.Calls.DURATION),
            null, null, CallLog.Calls.DATE + " DESC",
        )?.use { c ->
            while (c.moveToNext() && out.size < limit) {
                out.add(CallEntry(c.getString(0).orEmpty(), c.getString(1), c.getInt(2), c.getLong(3), c.getLong(4)))
            }
        }
        out
    }

    /** Keypad search: digits match the number, or the name spelled on a phone keypad (T9). */
    fun matches(c: Contact, digits: String): Boolean {
        if (digits.isEmpty()) return false
        if (c.number.filter { it.isDigit() }.contains(digits)) return true
        val t9 = c.name.lowercase().map { T9[it] ?: ' ' }.joinToString("")
        return t9.split(' ').any { it.startsWith(digits) } || t9.replace(" ", "").startsWith(digits)
    }

    private val T9: Map<Char, Char> = buildMap {
        "abc".forEach { put(it, '2') }; "def".forEach { put(it, '3') }; "ghi".forEach { put(it, '4') }
        "jkl".forEach { put(it, '5') }; "mno".forEach { put(it, '6') }; "pqrs".forEach { put(it, '7') }
        "tuv".forEach { put(it, '8') }; "wxyz".forEach { put(it, '9') }
    }
}
