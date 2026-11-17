package com.syalia.aegis

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit

/**
 * The phone's side of the device socket (src/aegis/devices.py). A foreground service, so Android keeps it while the person
 * has it on; its notification says so and carries Disconnect. It signs the server's nonce with the Keystore key, answers
 * each call on its own thread after the grant check, and turns notify frames into notifications.
 */
class DeviceService : Service() {
    private val client = OkHttpClient.Builder().pingInterval(30, TimeUnit.SECONDS).readTimeout(0, TimeUnit.MILLISECONDS).build()
    private val calls = Executors.newCachedThreadPool()
    private val main = Handler(Looper.getMainLooper())
    private lateinit var ops: Ops
    private var socket: WebSocket? = null
    private var backoffMs = 1_000L
    private var stopping = false

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        ops = Ops(this)
        channels(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_DISCONNECT) {
            Prefs(this).connected = false
            stopSelf()
            return START_NOT_STICKY
        }
        val prefs = Prefs(this)
        if (prefs.server == null || prefs.deviceId == null || !prefs.connected) {
            stopSelf()
            return START_NOT_STICKY
        }
        startInForeground("Connecting to aegis…")
        stopping = false
        if (socket == null) connect()
        return START_STICKY
    }

    override fun onDestroy() {
        stopping = true
        socket?.close(1000, "bye")
        socket = null
        calls.shutdownNow()
        super.onDestroy()
    }

    private fun startInForeground(text: String) {
        val n = ongoing(text)
        if (Build.VERSION.SDK_INT >= 34) {
            startForeground(ONGOING_ID, n, ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE)
        } else {
            startForeground(ONGOING_ID, n)
        }
    }

    private fun ongoing(text: String): Notification {
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(
            this,
            1,
            Intent(this, DeviceService::class.java).setAction(ACTION_DISCONNECT),
            PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL_ONGOING)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle("Agents can use this phone")
            .setContentText(text)
            .setContentIntent(open)
            .setOngoing(true)
            .addAction(Notification.Action.Builder(null, "Disconnect", stop).build())
            .build()
    }

    private fun status(text: String) {
        getSystemService(NotificationManager::class.java).notify(ONGOING_ID, ongoing(text))
    }

    private fun connect() {
        val prefs = Prefs(this)
        val base = prefs.server ?: return
        val id = prefs.deviceId ?: return
        val url = base.replaceFirst("http", "ws") + "device"
        socket = client.newWebSocket(Request.Builder().url(url).build(), object : WebSocketListener() {
            override fun onMessage(webSocket: WebSocket, text: String) {
                when (val f = Protocol.parse(text)) {
                    is Protocol.Frame.Challenge -> {
                        val sig = DeviceKey.sign(unb64url(f.nonce))
                        webSocket.send(Protocol.hello(id, sig, BuildConfig.VERSION_CODE, Build.VERSION.SDK_INT, "${Build.MANUFACTURER} ${Build.MODEL}", ops.names))
                    }
                    is Protocol.Frame.Welcome -> {
                        backoffMs = 1_000L
                        status("Connected as ${f.name}")
                    }
                    is Protocol.Frame.Call -> calls.execute { answer(webSocket, f) }
                    is Protocol.Frame.Notify -> notifyPerson(f)
                    Protocol.Frame.Unknown -> {}
                }
            }

            override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                webSocket.close(code, null)
                lost(webSocket, code)
            }

            override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = lost(webSocket, code)

            override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = lost(webSocket, null)
        })
    }

    private fun answer(ws: WebSocket, call: Protocol.Frame.Call) {
        val reply = try {
            if (!Grants.allowedWithoutAsking(call.op)) {
                if (Grants.ruleFor(call.op) == null) throw OpFailure("unknown_op", "this phone does not run ${call.op}")
                throw OpFailure("denied", "this phone does not allow ${call.op} yet")
            }
            Protocol.result(call.id, ops.run(call.op, call.args))
        } catch (e: OpFailure) {
            Protocol.error(call.id, e.code, e.message ?: "")
        } catch (e: Exception) {
            Protocol.error(call.id, "android_error", e.toString())
        }
        ws.send(reply)
    }

    private fun lost(ws: WebSocket, code: Int?) {
        if (ws != socket) return
        socket = null
        if (stopping) return
        if (code == 4401) {
            // The server no longer knows this key: unpaired from Settings, or the token was revoked.
            Prefs(this).apply {
                deviceId = null
                connected = false
            }
            status("Unpaired by the server")
            stopSelf()
            return
        }
        status("Offline, retrying…")
        main.postDelayed({ if (!stopping && socket == null) connect() }, backoffMs)
        backoffMs = (backoffMs * 2).coerceAtMost(60_000L)
    }

    private fun notifyPerson(f: Protocol.Frame.Notify) {
        val open = PendingIntent.getActivity(this, f.tag.hashCode(), Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val n = Notification.Builder(this, CHANNEL_ATTENTION)
            .setSmallIcon(R.drawable.ic_stat)
            .setContentTitle(f.title)
            .setContentText(f.body)
            .setStyle(Notification.BigTextStyle().bigText(f.body))
            .setContentIntent(open)
            .setAutoCancel(true)
            .build()
        getSystemService(NotificationManager::class.java).notify(f.tag, 0, n)
    }

    companion object {
        const val ACTION_DISCONNECT = "com.syalia.aegis.DISCONNECT"
        private const val CHANNEL_ONGOING = "device"
        private const val CHANNEL_ATTENTION = "attention"
        private const val ONGOING_ID = 1

        fun channels(context: Context) {
            val nm = context.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ONGOING, "Connection to aegis", NotificationManager.IMPORTANCE_MIN))
            nm.createNotificationChannel(NotificationChannel(CHANNEL_ATTENTION, "Sessions that need you", NotificationManager.IMPORTANCE_HIGH))
        }

        fun start(context: Context) {
            context.startForegroundService(Intent(context, DeviceService::class.java))
        }
    }
}
