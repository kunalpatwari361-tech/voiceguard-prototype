package com.voiceguard.app.familysafety

import com.voiceguard.app.familysafety.FamilySafetyLogic.Step
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Family Safety phone-side rules: consent before permissions, no sharing without permission, freshness, battery. */
class FamilySafetyLogicTest {
    @Test fun explanationComesBeforeAnyPermission() {
        assertEquals(Step.DISCLOSURE, FamilySafetyLogic.nextStep(agreed = false, hasLocation = true, notificationsNeedPermission = true, hasNotifications = true))
    }

    @Test fun permissionsAskedOneAtATime() {
        assertEquals(Step.LOCATION_PERMISSION, FamilySafetyLogic.nextStep(true, hasLocation = false, notificationsNeedPermission = true, hasNotifications = false))
        assertEquals(Step.NOTIFICATION_PERMISSION, FamilySafetyLogic.nextStep(true, hasLocation = true, notificationsNeedPermission = true, hasNotifications = false))
        assertEquals(Step.READY, FamilySafetyLogic.nextStep(true, hasLocation = true, notificationsNeedPermission = false, hasNotifications = false))
    }

    @Test fun deniedPermissionMeansNoSharing() {
        assertFalse(FamilySafetyLogic.canShare(consented = true, hasLocation = false, notificationsVisible = true))
        assertFalse(FamilySafetyLogic.canShare(consented = true, hasLocation = true, notificationsVisible = false))
        assertFalse(FamilySafetyLogic.canShare(consented = false, hasLocation = true, notificationsVisible = true))
        assertTrue(FamilySafetyLogic.canShare(consented = true, hasLocation = true, notificationsVisible = true))
    }

    @Test fun sendsOnMoveButNotTooOften() {
        assertTrue(FamilySafetyLogic.shouldSend(null, 0, null))                    // first fix
        assertFalse(FamilySafetyLogic.shouldSend(0, 30_000, 500f))                 // moved, but only 30 s later
        assertTrue(FamilySafetyLogic.shouldSend(0, 61_000, 30f))                   // moved 30 m after a minute
        assertFalse(FamilySafetyLogic.shouldSend(0, 120_000, 5f))                  // standing still
        assertTrue(FamilySafetyLogic.shouldSend(0, 5 * 60_000L, 0f))               // heartbeat every 5 minutes
    }

    @Test fun oldLocationsAreNotShownAsCurrent() {
        assertEquals("live", FamilySafetyLogic.freshness(120))
        assertEquals("stale", FamilySafetyLogic.freshness(20 * 60))
        assertEquals("offline", FamilySafetyLogic.freshness(3 * 3600))
        assertEquals("unknown", FamilySafetyLogic.freshness(null))
        assertEquals("5 min ago", FamilySafetyLogic.ageText(300))
    }

    @Test fun unknownBatteryIsNeverFull() {
        assertNull(FamilySafetyLogic.batteryPercent(-1, 100))
        assertNull(FamilySafetyLogic.batteryPercent(50, 0))
        assertNull(FamilySafetyLogic.batteryPercent(null, null))
        assertEquals(15, FamilySafetyLogic.batteryPercent(15, 100))
        assertEquals(50, FamilySafetyLogic.batteryPercent(128, 255))
    }
}
