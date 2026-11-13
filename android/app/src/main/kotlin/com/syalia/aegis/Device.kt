package com.syalia.aegis

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.webkit.CookieManager
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec

/** What the app keeps: the server's address and, once paired, the device's id there. */
class Prefs(context: Context) {
    private val p = context.getSharedPreferences("aegis", Context.MODE_PRIVATE)

    var server: String?
        get() = p.getString("server", null)
        set(v) = p.edit().putString("server", v).apply()

    var deviceId: String?
        get() = p.getString("device_id", null)
        set(v) = p.edit().putString("device_id", v).apply()

    /** Whether the person turned the device service on; Disconnect turns it off until they pair or reconnect. */
    var connected: Boolean
        get() = p.getBoolean("connected", false)
        set(v) = p.edit().putBoolean("connected", v).apply()
}

/** The base URL with exactly one trailing slash, from what the person typed. */
fun normalizeServer(typed: String): String? {
    var s = typed.trim()
    if (s.isEmpty()) return null
    if (!s.startsWith("http://") && !s.startsWith("https://")) s = "https://$s"
    val url = try {
        URL(s)
    } catch (_: Exception) {
        return null
    }
    if (url.host.isNullOrEmpty()) return null
    val path = url.path.substringBefore('?').trimEnd('/')
    val port = if (url.port == -1) "" else ":${url.port}"
    return "${url.protocol}://${url.host}$port$path/"
}

/** The origin of a base URL: what the page's bridge is allowed for. */
fun originOf(base: String): String {
    val u = URL(base)
    val port = if (u.port == -1) "" else ":${u.port}"
    return "${u.protocol}://${u.host}$port"
}

fun b64url(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)

fun unb64url(s: String): ByteArray = Base64.decode(s, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)

/** A P-256 key made in the Android Keystore; its private half never leaves the phone. */
object DeviceKey {
    private const val ALIAS = "aegis-device"

    private fun store(): KeyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }

    fun ensure() {
        if (store().containsAlias(ALIAS)) return
        val gen = KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore")
        gen.initialize(
            KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_SIGN)
                .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
                .setDigests(KeyProperties.DIGEST_SHA256)
                .build(),
        )
        gen.generateKeyPair()
    }

    /** The public half as base64url SubjectPublicKeyInfo DER, what the server's pair route takes. */
    fun publicKey(): String {
        ensure()
        return b64url(store().getCertificate(ALIAS).publicKey.encoded)
    }

    /** ECDSA with SHA-256 over ``data``, DER, base64url. */
    fun sign(data: ByteArray): String {
        ensure()
        val key = store().getKey(ALIAS, null) as PrivateKey
        return b64url(Signature.getInstance("SHA256withECDSA").run {
            initSign(key)
            update(data)
            sign()
        })
    }
}

/** Pairing: post the public key with the WebView's session cookie. Blocking; call it off the main thread. */
object Pairing {
    fun pair(context: Context, base: String): JSONObject {
        val cookies = CookieManager.getInstance().getCookie(base) ?: throw OpFailure("signed_out", "sign in to aegis first")
        val body = JSONObject().put("name", "${Build.MANUFACTURER} ${Build.MODEL}".trim()).put("public_key", DeviceKey.publicKey())
        val conn = URL(base + "api/devices/pair").openConnection() as HttpURLConnection
        try {
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 15_000
            conn.readTimeout = 15_000
            conn.setRequestProperty("Content-Type", "application/json")
            conn.setRequestProperty("Cookie", cookies)
            conn.outputStream.use { it.write(body.toString().toByteArray()) }
            val code = conn.responseCode
            val text = (if (code < 400) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
            if (code != 200) throw OpFailure("pair_failed", "the server answered $code: $text")
            val out = JSONObject(text)
            val prefs = Prefs(context)
            prefs.deviceId = out.getString("id")
            prefs.connected = true
            return out
        } finally {
            conn.disconnect()
        }
    }
}
