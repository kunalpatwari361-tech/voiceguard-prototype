package com.voiceguard.app.callerid

import com.voiceguard.app.data.VgJson
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The Caller Identity decision rules: sources, statuses and the "never verified unless it really is" guarantee. */
class CallerIdResolverTest {
    private fun json(s: String) = VgJson.parseToJsonElement(s) as JsonObject
    private val n = "+919876543210"

    @Test fun savedContactWinsAndWorksOffline() {
        val r = CallerIdResolver.resolve(n, null, "Mummy", server = null, check = NumberCheck.UNAVAILABLE)
        assertEquals(IdSource.CONTACT, r.source); assertEquals("Mummy", r.name); assertEquals("saved", r.status)
    }

    @Test fun familyBeforeContact() {
        assertEquals(IdSource.FAMILY, CallerIdResolver.resolve(n, "Rahul", "Rahul (old)", null, NumberCheck.UNAVAILABLE).source)
    }

    @Test fun unknownWhenNothingIsKnownOrServerUnreachable() {
        val r = CallerIdResolver.resolve(n, null, null, server = null, check = NumberCheck.NOT_VERIFIED)
        assertEquals(IdSource.UNKNOWN, r.source); assertNull(r.name); assertEquals("unknown", r.status)
    }

    @Test fun verifiedBusinessIsVerified() {
        val r = CallerIdResolver.resolve(n, null, null, json("""{"identity":{"name":"Example Bank","source":"verified_business","status":"verified","confidence":"high","detail":"x"}}"""), NumberCheck.PASSED)
        assertEquals(IdSource.VERIFIED_BUSINESS, r.source); assertEquals("verified", r.status); assertEquals(NumberCheck.PASSED, r.numberCheck)
    }

    @Test fun communityNameIsNeverShownAsVerified() {
        val r = CallerIdResolver.resolve(n, null, null, json("""{"identity":{"name":"Sharma","source":"community","status":"verified","confidence":"high"}}"""), NumberCheck.UNAVAILABLE)
        assertEquals(IdSource.COMMUNITY, r.source); assertEquals("unverified", r.status); assertEquals("low", r.confidence)
    }

    @Test fun voiceGuardMemberNameIsNotVerified() {
        val r = CallerIdResolver.resolve(n, null, null, json("""{"identity":{"name":"Asha","source":"voiceguard_user","status":"verified"}}"""), NumberCheck.UNAVAILABLE)
        assertEquals(IdSource.VOICEGUARD_USER, r.source); assertEquals("unverified", r.status)
    }

    @Test fun conflictingNamesStayConflicting() {
        val r = CallerIdResolver.resolve(n, null, null, json("""{"identity":{"name":"A","source":"community","status":"conflicting"}}"""), NumberCheck.UNAVAILABLE)
        assertEquals("conflicting", r.status)
    }

    @Test fun spamRiskAndTelecomNoteAreSeparateFromIdentity() {
        val r = CallerIdResolver.resolve(n, null, null, json("""{"identity":null,"spam":{"score":0.75,"label":"International"},"telecom":{"available":false,"reason":"No authorised telecom identity provider is configured."}}"""), NumberCheck.UNAVAILABLE)
        assertEquals(IdSource.UNKNOWN, r.source); assertEquals(0.75, r.spamScore!!, 1e-9)
        assertTrue(r.telecomNote!!.contains("authorised"))
    }

    @Test fun carrierStatusMapping() {
        assertEquals(NumberCheck.PASSED, CallerIdResolver.numberCheck(1))
        assertEquals(NumberCheck.FAILED, CallerIdResolver.numberCheck(2))
        assertEquals(NumberCheck.NOT_VERIFIED, CallerIdResolver.numberCheck(0))
        assertEquals(NumberCheck.UNAVAILABLE, CallerIdResolver.numberCheck(null))
        assertEquals(NumberCheck.UNAVAILABLE, CallerIdResolver.numberCheck(99))
    }

    @Test fun malformedServerReplyFallsBackToUnknown() {
        val r = CallerIdResolver.resolve(n, null, null, json("""{"identity":{"source":"community"}}"""), NumberCheck.UNAVAILABLE)
        assertEquals(IdSource.UNKNOWN, r.source)
    }
}
