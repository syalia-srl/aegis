package com.syalia.aegis

import android.Manifest
import android.annotation.SuppressLint
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.os.Environment
import android.provider.Settings
import android.view.Gravity
import android.view.inputmethod.EditorInfo
import android.webkit.PermissionRequest
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.webkit.JavaScriptReplyProxy
import androidx.webkit.WebResourceErrorCompat
import androidx.webkit.WebViewClientCompat
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import org.json.JSONObject
import java.util.concurrent.Executors

/**
 * The shell: the aegis web client in a WebView on the server's own origin, so every client release reaches the app with no
 * new APK, and the client's service worker gives it its offline start. The page's only bridge is window.aegisShell, given
 * to frames of that origin alone (spec section 04): info, pair and openDeviceSettings. No device operation runs through it.
 */
class MainActivity : ComponentActivity() {
    private lateinit var prefs: Prefs
    private var webView: WebView? = null
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var micRequest: PermissionRequest? = null
    private val pool = Executors.newSingleThreadExecutor()

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        DeviceService.channels(this)
        val server = prefs.server
        if (server == null) setup(null, null) else open(server)
    }

    override fun onResume() {
        super.onResume()
        if (prefs.connected && prefs.deviceId != null) DeviceService.start(this)
    }

    /** Ask for the server's address: the first run, or after the page failed to load. */
    private fun setup(error: String?, prefill: String?) {
        webView?.destroy()
        webView = null
        val pad = (20 * resources.displayMetrics.density).toInt()
        val ink = Color.parseColor("#DCD9CF")
        val title = TextView(this).apply {
            text = "aegis"
            textSize = 26f
            setTextColor(Color.parseColor("#F1EDE2"))
        }
        val hint = TextView(this).apply {
            text = error ?: "The address of your aegis server, as you open it in a browser."
            setTextColor(if (error != null) Color.parseColor("#D9806F") else ink)
            setPadding(0, pad / 2, 0, pad / 2)
        }
        val field = EditText(this).apply {
            setText(prefill ?: "")
            this.hint = "https://aegis.example.com"
            setTextColor(ink)
            setHintTextColor(Color.parseColor("#6F6B62"))
            isSingleLine = true
            inputType = EditorInfo.TYPE_TEXT_VARIATION_URI
            imeOptions = EditorInfo.IME_ACTION_GO
        }
        val go = Button(this).apply { text = "Open" }
        val submit = {
            val base = normalizeServer(field.text.toString())
            if (base == null) {
                hint.text = "That is not an address. Try https://your-server or http://100.64.0.5:8000"
                hint.setTextColor(Color.parseColor("#D9806F"))
            } else {
                if (base != prefs.server) {
                    prefs.deviceId = null
                    prefs.connected = false
                }
                prefs.server = base
                open(base)
            }
        }
        go.setOnClickListener { submit() }
        field.setOnEditorActionListener { _, _, _ ->
            submit()
            true
        }
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            gravity = Gravity.CENTER_VERTICAL
            setPadding(pad, pad, pad, pad)
            setBackgroundColor(Color.parseColor("#11100E"))
            addView(title)
            addView(hint)
            addView(field)
            addView(go)
        }
        setContentView(box)
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun open(base: String) {
        val origin = originOf(base)
        val wv = WebView(this)
        webView = wv
        WebView.setWebContentsDebuggingEnabled(applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE != 0)
        wv.setBackgroundColor(Color.parseColor("#11100E"))
        wv.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            allowFileAccess = false
            allowContentAccess = false
        }
        wv.webViewClient = object : WebViewClientCompat() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                if (request.url.toString().startsWith(origin)) return false
                try {
                    startActivity(Intent(Intent.ACTION_VIEW, request.url))
                } catch (_: ActivityNotFoundException) {
                }
                return true
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceErrorCompat) {
                // The service worker answers offline loads itself; only a page with nothing to show lands here.
                if (request.isForMainFrame && WebViewFeature.isFeatureSupported(WebViewFeature.WEB_RESOURCE_ERROR_GET_DESCRIPTION)) {
                    setup("Could not open $base: ${error.description}", base)
                }
            }
        }
        wv.webChromeClient = object : WebChromeClient() {
            override fun onShowFileChooser(view: WebView, callback: ValueCallback<Array<Uri>>, params: FileChooserParams): Boolean {
                fileCallback?.onReceiveValue(null)
                fileCallback = callback
                return try {
                    startActivityForResult(params.createIntent(), REQ_FILE)
                    true
                } catch (_: ActivityNotFoundException) {
                    fileCallback = null
                    false
                }
            }

            override fun onPermissionRequest(request: PermissionRequest) {
                if (!request.origin.toString().startsWith(origin)) return request.deny()
                if (PermissionRequest.RESOURCE_AUDIO_CAPTURE !in request.resources) return request.deny()
                if (checkSelfPermission(Manifest.permission.RECORD_AUDIO) == PackageManager.PERMISSION_GRANTED) {
                    request.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE))
                } else {
                    micRequest = request
                    requestPermissions(arrayOf(Manifest.permission.RECORD_AUDIO), REQ_MIC)
                }
            }
        }
        wv.setDownloadListener { url, _, disposition, mime, _ ->
            val name = URLUtil.guessFileName(url, disposition, mime)
            val req = DownloadManager.Request(Uri.parse(url))
                .setMimeType(mime)
                .addRequestHeader("Cookie", android.webkit.CookieManager.getInstance().getCookie(url) ?: "")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, name)
            getSystemService(DownloadManager::class.java).enqueue(req)
        }
        if (WebViewFeature.isFeatureSupported(WebViewFeature.WEB_MESSAGE_LISTENER)) {
            WebViewCompat.addWebMessageListener(wv, "aegisShell", setOf(origin)) { _, message, _, isMainFrame, reply ->
                if (isMainFrame) receive(message.data ?: "", reply, base)
            }
        }
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                val v = webView
                if (v != null && v.canGoBack()) v.goBack() else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })
        setContentView(wv)
        wv.loadUrl(base)
    }

    /** One request from the page: {id, method}; the answer carries the same id. */
    private fun receive(data: String, reply: JavaScriptReplyProxy, base: String) {
        val msg = try {
            JSONObject(data)
        } catch (_: Exception) {
            return
        }
        val id = msg.opt("id")
        fun answer(result: Any?, error: String? = null) = runOnUiThread {
            val out = JSONObject().put("id", id)
            if (error != null) out.put("error", error) else out.put("result", result ?: JSONObject.NULL)
            reply.postMessage(out.toString())
        }
        when (msg.optString("method")) {
            "info" -> answer(info())
            "pair" -> {
                if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                    requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), REQ_NOTIFY)
                }
                pool.execute {
                    try {
                        val out = Pairing.pair(this, base)
                        runOnUiThread { DeviceService.start(this) }
                        answer(info().put("name", out.optString("name")))
                    } catch (e: OpFailure) {
                        answer(null, e.message)
                    } catch (e: Exception) {
                        answer(null, "pairing failed: $e")
                    }
                }
            }
            "openDeviceSettings" -> {
                startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:$packageName")))
                answer(true)
            }
            else -> answer(null, "unknown method")
        }
    }

    private fun info(): JSONObject = JSONObject()
        .put("shell", BuildConfig.VERSION_CODE)
        .put("version", BuildConfig.VERSION_NAME)
        .put("paired", prefs.deviceId != null)
        .put("connected", prefs.connected)

    @Deprecated("The file chooser's result; the Activity Result API needs registration before the WebView exists.")
    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        super.onActivityResult(requestCode, resultCode, data)
        if (requestCode == REQ_FILE) {
            fileCallback?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            fileCallback = null
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_MIC) {
            val r = micRequest ?: return
            micRequest = null
            if (grantResults.firstOrNull() == PackageManager.PERMISSION_GRANTED) r.grant(arrayOf(PermissionRequest.RESOURCE_AUDIO_CAPTURE)) else r.deny()
        }
    }

    companion object {
        private const val REQ_FILE = 1
        private const val REQ_MIC = 2
        private const val REQ_NOTIFY = 3
    }
}
