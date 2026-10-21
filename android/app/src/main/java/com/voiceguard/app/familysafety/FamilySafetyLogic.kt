package com.voiceguard.app.familysafety

/**
 * Family Safety decisions that do not need Android (unit-tested in src/test): which permission step comes next,
 * when to send a fix, how fresh a location is, and how to read the battery. The server applies the same rules
 * again - the phone is never trusted to decide who may see what.
 */
object FamilySafetyLogic {
    const val LIVE_S = 6 * 60
    const val STALE_S = 60 * 60
    const val MOVE_INTERVAL_MS = 60_000L          // at most one fix a minute while moving
    const val HEARTBEAT_MS = 5 * 60_000L          // and at least one every 5 minutes while still
    const val MIN_DISTANCE_M = 25f

    enum class Step { DISCLOSURE, LOCATION_PERMISSION, NOTIFICATION_PERMISSION, READY }

    /** Permissions are asked one at a time, each after its explanation, and only after the member agreed. */
    fun nextStep(agreed: Boolean, hasLocation: Boolean, notificationsNeedPermission: Boolean, hasNotifications: Boolean): Step = when {
        !agreed -> Step.DISCLOSURE
        !hasLocation -> Step.LOCATION_PERMISSION
        notificationsNeedPermission && !hasNotifications -> Step.NOTIFICATION_PERMISSION
        else -> Step.READY
    }

    /** Sharing runs only with consent, location permission and a visible notification. */
    fun canShare(consented: Boolean, hasLocation: Boolean, notificationsVisible: Boolean) = consented && hasLocation && notificationsVisible

    fun shouldSend(lastSentAtMs: Long?, nowMs: Long, movedM: Float?): Boolean {
        if (lastSentAtMs == null) return true
        val since = nowMs - lastSentAtMs
        if (since >= HEARTBEAT_MS) return true
        return since >= MOVE_INTERVAL_MS && (movedM ?: 0f) >= MIN_DISTANCE_M
    }

    fun freshness(ageS: Int?): String = when {
        ageS == null -> "unknown"
        ageS <= LIVE_S -> "live"
        ageS <= STALE_S -> "stale"
        else -> "offline"
    }

    fun ageText(ageS: Int?): String = when {
        ageS == null -> "no update yet"
        ageS < 60 -> "just now"
        ageS < 3600 -> "${ageS / 60} min ago"
        ageS < 86_400 -> "${ageS / 3600} h ago"
        else -> "${ageS / 86_400} days ago"
    }

    /** Battery percent from the system's level/scale, or null when unknown (never assumed full). */
    fun batteryPercent(level: Int?, scale: Int?): Int? {
        if (level == null || scale == null || level < 0 || scale <= 0 || level > scale) return null
        return (level * 100 + scale / 2) / scale
    }
}
