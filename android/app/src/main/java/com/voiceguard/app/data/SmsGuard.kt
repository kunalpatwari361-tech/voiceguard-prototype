package com.voiceguard.app.data

import com.voiceguard.app.ui.tr

/**
 * Scam check for SMS, on the phone (messages never leave it). Same idea as the server's Scam Words engine:
 * independent warning signs combine as 1 - Π(1 - weight). Real banks send from letter headers (AX-HDFCBK);
 * "bank" messages from a personal mobile number, links next to KYC/prize/urgency, and requests to share an OTP
 * are the classic Indian SMS scams.
 */
object SmsGuard {
    data class Verdict(val score: Double, val reasons: List<String>, val hasLink: Boolean, val isOtp: Boolean) {
        val level get() = when { score >= 0.6 -> "danger"; score >= 0.35 -> "caution"; else -> "safe" }
        companion object { val NONE = Verdict(0.0, emptyList(), false, false) }
    }

    private class Rule(val weight: Double, val en: String, val hi: String, val words: List<String>)

    private val RULES = listOf(
        Rule(0.55, "Asks you to share an OTP / PIN", "OTP / PIN बताने को कह रहा है", listOf(
            "share otp", "share the otp", "send otp", "tell otp", "otp batao", "otp bata", "otp bhejo", "otp share",
            "share the code", "forward this code", "send the code", "share pin", "cvv", "ओटीपी बताएं", "ओटीपी भेजें")),
        Rule(0.45, "KYC / account-block threat", "KYC / खाता बंद होने की धमकी", listOf(
            "kyc", "account will be blocked", "account blocked", "account suspended", "will be suspended", "pan card",
            "update your pan", "aadhaar", "aadhar", "sim will be blocked", "number will be blocked", "re-kyc",
            "खाता बंद", "केवाईसी", "आधार")),
        Rule(0.45, "Electricity cut threat", "बिजली कटने की धमकी", listOf(
            "electricity", "bijli", "power will be disconnected", "will be disconnected", "light will be cut", "बिजली")),
        Rule(0.4, "Prize, lottery or refund bait", "इनाम / लॉटरी / रिफंड का लालच", listOf(
            "lottery", "you have won", "you won", "prize", "lucky draw", "kbc", "reward points", "cashback", "refund",
            "gift", "congratulations", "लॉटरी", "इनाम", "बधाई")),
        Rule(0.4, "Job / investment bait", "नौकरी / निवेश का लालच", listOf(
            "work from home", "part time job", "part-time job", "earn daily", "daily income", "earn rs", "investment",
            "double your money", "telegram", "task based", "like videos", "trading tips")),
        Rule(0.35, "Police / customs / court threat", "पुलिस / कस्टम / कोर्ट की धमकी", listOf(
            "cbi", "customs", "narcotics", "parcel", "courier", "fedex", "digital arrest", "warrant", "court", "police",
            "पुलिस", "पार्सल", "वारंट")),
        Rule(0.35, "Family emergency story", "परिवार की इमरजेंसी की कहानी", listOf(
            "accident", "hospital", "in trouble", "new number", "naya number", "this is my new number", "phone kho gaya",
            "एक्सीडेंट", "अस्पताल", "नया नंबर")),
        Rule(0.3, "Asks for money", "पैसे माँग रहा है", listOf(
            "send money", "transfer money", "paise bhejo", "pay now", "pay immediately", "upi id", "scan the qr",
            "पैसे भेजो", "पैसे भेज")),
        Rule(0.25, "Rushes you", "जल्दबाज़ी करवा रहा है", listOf(
            "urgent", "immediately", "within 24 hours", "today only", "last chance", "tonight", "jaldi", "turant",
            "जल्दी", "तुरंत")),
        Rule(0.45, "Wants you to install an app", "ऐप इंस्टॉल करवाना चाहता है", listOf(
            ".apk", "anydesk", "teamviewer", "quick support", "download the app", "install the app")),
    )

    private val LINK = Regex("""(https?://|www\.|bit\.ly|tinyurl|t\.me/|wa\.me/|[a-z0-9-]+\.(xyz|top|click|link|in/|co/))""", RegexOption.IGNORE_CASE)
    private val OTP = Regex("""(otp|one[ -]time password|verification code|ओटीपी)""", RegexOption.IGNORE_CASE)
    private val CODE = Regex("""(?<!\d)\d{4,8}(?!\d)""")

    /** A personal mobile number (not a bank/company letter header like VM-HDFCBK). */
    fun isPersonalNumber(sender: String?): Boolean {
        val d = sender.orEmpty().filter { it.isDigit() }
        return sender.orEmpty().none { it.isLetter() } && d.length >= 10
    }

    fun check(text: String, sender: String?): Verdict {
        val t = text.lowercase()
        val reasons = mutableListOf<String>()
        var keep = 1.0
        fun add(w: Double, why: String) { keep *= 1 - w; reasons += why }
        for (r in RULES) if (r.words.any { t.contains(it) }) add(r.weight, tr(r.en, r.hi))
        val link = LINK.containsMatchIn(text)
        if (link) add(if (reasons.isEmpty()) 0.15 else 0.35, tr("Contains a link", "इसमें लिंक है"))
        val personal = isPersonalNumber(sender)
        if (personal && (link || reasons.isNotEmpty()) && listOf("bank", "kyc", "account", "electricity", "sbi", "hdfc", "icici", "paytm").any { t.contains(it) })
            add(0.4, tr("'Bank' or company message from a personal mobile number", "बैंक/कंपनी का मैसेज किसी निजी मोबाइल नंबर से"))
        val n = sender?.let { Numbers.normalize(it) }
        if (n != null && n in Prefs.scamNumbers) add(0.6, tr("Number reported as scam", "नंबर स्कैम के रूप में रिपोर्टेड"))
        if (n != null && n == Prefs.lastRiskyNumber?.let { Numbers.normalize(it) } && Prefs.inRiskyWindow(60))
            add(0.4, tr("From the number that just made a risky call", "उसी नंबर से जिसने अभी ख़तरनाक कॉल की"))
        val otp = OTP.containsMatchIn(text) && CODE.containsMatchIn(text)
        return Verdict(1 - keep, reasons, link, otp)
    }
}
