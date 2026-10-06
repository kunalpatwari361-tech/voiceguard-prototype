package com.voiceguard.app.data

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.Uri
import android.provider.ContactsContract
import android.provider.ContactsContract.CommonDataKinds.Phone

/** Read-only access to the phone's address book (caller names in the dialer, in-call screen and call screening). */
object Contacts {
    data class Contact(val name: String, val number: String)

    @Volatile private var cache: List<Contact>? = null

    fun granted(ctx: Context) =
        ctx.checkSelfPermission(Manifest.permission.READ_CONTACTS) == PackageManager.PERMISSION_GRANTED

    /** Every contact with a phone number, sorted by name, one row per distinct number. Call off the main thread. */
    fun all(ctx: Context, refresh: Boolean = false): List<Contact> {
        if (!granted(ctx)) return emptyList()
        if (!refresh) cache?.let { return it }
        val out = LinkedHashMap<String, Contact>()
        runCatching {
            ctx.contentResolver.query(Phone.CONTENT_URI, arrayOf(Phone.DISPLAY_NAME_PRIMARY, Phone.NUMBER),
                null, null, "${Phone.DISPLAY_NAME_PRIMARY} COLLATE NOCASE ASC")?.use { c ->
                while (c.moveToNext()) {
                    val name = c.getString(0)?.trim().orEmpty()
                    val n = Numbers.normalize(c.getString(1).orEmpty())
                    if (n.isNotEmpty() && n !in out) out[n] = Contact(name.ifEmpty { n }, n)
                }
            }
        }
        return out.values.toList().also { cache = it }
    }

    /** Saved name for a number, or null if it is not in the address book. Call off the main thread. */
    fun nameFor(ctx: Context, number: String): String? {
        if (number.isBlank() || !granted(ctx)) return null
        return runCatching {
            val uri = Uri.withAppendedPath(ContactsContract.PhoneLookup.CONTENT_FILTER_URI, Uri.encode(number))
            ctx.contentResolver.query(uri, arrayOf(ContactsContract.PhoneLookup.DISPLAY_NAME), null, null, null)?.use { c ->
                if (c.moveToFirst()) c.getString(0) else null
            }
        }.getOrNull()
    }

    /** Dialer search: matches name text or the digits typed so far. */
    fun search(list: List<Contact>, q: String): List<Contact> {
        val t = q.trim()
        if (t.isEmpty()) return list
        val digits = t.filter { it.isDigit() }
        return list.filter { c ->
            c.name.contains(t, ignoreCase = true) || (digits.length >= 2 && c.number.filter { it.isDigit() }.contains(digits))
        }
    }
}
