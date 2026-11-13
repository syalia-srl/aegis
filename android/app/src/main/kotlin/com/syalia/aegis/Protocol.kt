package com.syalia.aegis

import org.json.JSONArray
import org.json.JSONObject

/**
 * The device socket's frames (src/aegis/devices.py and the plan's section 01). The server speaks first with a challenge;
 * the phone answers hello, then replies to calls. Pure, so the JVM tests cover it.
 */
object Protocol {
    sealed class Frame {
        data class Challenge(val nonce: String) : Frame()
        data class Welcome(val name: String, val latestShell: Int) : Frame()
        data class Call(val id: String, val session: String, val op: String, val args: JSONObject) : Frame()
        data class Notify(val title: String, val body: String, val tag: String, val kind: String) : Frame()
        object Unknown : Frame()
    }

    fun parse(text: String): Frame {
        val o = try {
            JSONObject(text)
        } catch (_: Exception) {
            return Frame.Unknown
        }
        return when (o.optString("t")) {
            "challenge" -> Frame.Challenge(o.optString("nonce"))
            "welcome" -> Frame.Welcome(o.optString("name"), o.optInt("latest_shell", 0))
            "call" -> Frame.Call(o.optString("id"), o.optString("session"), o.optString("op"), o.optJSONObject("args") ?: JSONObject())
            "notify" -> Frame.Notify(o.optString("title"), o.optString("body"), o.optString("tag"), o.optString("kind"))
            else -> Frame.Unknown
        }
    }

    fun hello(id: String, sig: String, shell: Int, api: Int, model: String, ops: Collection<String>): String =
        JSONObject()
            .put("t", "hello")
            .put("id", id)
            .put("sig", sig)
            .put("shell", shell)
            .put("api", api)
            .put("model", model)
            .put("ops", JSONArray(ops.toList()))
            .toString()

    fun result(id: String, result: Any?): String =
        JSONObject().put("t", "reply").put("id", id).put("result", result ?: JSONObject.NULL).toString()

    fun error(id: String, code: String, message: String): String =
        JSONObject()
            .put("t", "reply")
            .put("id", id)
            .put("error", JSONObject().put("code", code).put("message", message))
            .toString()
}

/** A refusal or failure an operation reports to the agent by its code. */
class OpFailure(val code: String, message: String) : Exception(message)
