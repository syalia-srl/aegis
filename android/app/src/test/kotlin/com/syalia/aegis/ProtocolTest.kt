package com.syalia.aegis

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class ProtocolTest {
    @Test
    fun parsesEachServerFrame() {
        assertEquals(Protocol.Frame.Challenge("abc"), Protocol.parse("""{"t":"challenge","nonce":"abc"}"""))
        assertEquals(Protocol.Frame.Welcome("Pixel", 2), Protocol.parse("""{"t":"welcome","name":"Pixel","latest_shell":2}"""))
        val call = Protocol.parse("""{"t":"call","id":"c1","session":"fix","op":"compass.read","args":{"seconds":3}}""")
        assertTrue(call is Protocol.Frame.Call)
        call as Protocol.Frame.Call
        assertEquals("compass.read", call.op)
        assertEquals(3, call.args.getInt("seconds"))
        val n = Protocol.parse("""{"t":"notify","title":"a","body":"b","tag":"t","kind":"needs_you","key":"x"}""")
        assertEquals(Protocol.Frame.Notify("a", "b", "t", "needs_you"), n)
    }

    @Test
    fun anythingElseIsUnknown() {
        assertEquals(Protocol.Frame.Unknown, Protocol.parse("not json"))
        assertEquals(Protocol.Frame.Unknown, Protocol.parse("""{"t":"later"}"""))
    }

    @Test
    fun aCallWithoutArgsGetsAnEmptyObject() {
        val call = Protocol.parse("""{"t":"call","id":"c1","op":"device.info"}""") as Protocol.Frame.Call
        assertEquals(0, call.args.length())
    }

    @Test
    fun buildsHelloAndReplies() {
        val hello = JSONObject(Protocol.hello("d_1", "sig", 1, 35, "Pixel", listOf("device.info")))
        assertEquals("hello", hello.getString("t"))
        assertEquals("device.info", hello.getJSONArray("ops").getString(0))
        val ok = JSONObject(Protocol.result("c1", JSONObject().put("x", 1)))
        assertEquals(1, ok.getJSONObject("result").getInt("x"))
        val err = JSONObject(Protocol.error("c1", "denied", "no"))
        assertEquals("denied", err.getJSONObject("error").getString("code"))
        assertFalse(err.has("result"))
    }
}

class GrantsTest {
    @Test
    fun senseRunsWithoutAskingAndUnknownOpsAreNotRun() {
        assertTrue(Grants.allowedWithoutAsking("compass.read"))
        assertNull(Grants.ruleFor("apps.install"))
        assertFalse(Grants.allowedWithoutAsking("apps.install"))
    }

    @Test
    fun aDeniedClassDenies() {
        val rules = Grants.DEFAULTS + (Grants.Cls.SENSE to Grants.Rule.DENY)
        assertFalse(Grants.allowedWithoutAsking("sensor.read", rules))
    }
}
