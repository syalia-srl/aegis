package com.syalia.aegis

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.hardware.Sensor
import android.hardware.SensorEvent
import android.hardware.SensorEventListener
import android.hardware.SensorManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.BatteryManager
import android.os.Build
import android.os.Handler
import android.os.HandlerThread
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit

/**
 * The operations the phone runs for its owner's agents (help: devices). Each one blocks its caller's thread until done;
 * DeviceService runs each call on its own thread. Slice 1 is the sense class only.
 */
class Ops(private val context: Context) {
    private val sensors = context.getSystemService(Context.SENSOR_SERVICE) as SensorManager

    val names: List<String> = listOf("device.info", "sensor.list", "sensor.read", "compass.read")

    fun run(op: String, args: JSONObject): Any = when (op) {
        "device.info" -> info()
        "sensor.list" -> sensorList()
        "sensor.read" -> sensorRead(args)
        "compass.read" -> compassRead(args)
        else -> throw OpFailure("unknown_op", "this phone does not run $op")
    }

    private fun info(): JSONObject {
        val battery = context.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = battery?.let { b ->
            val l = b.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
            val s = b.getIntExtra(BatteryManager.EXTRA_SCALE, -1)
            if (l >= 0 && s > 0) l * 100 / s else null
        }
        val charging = battery?.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0)?.let { it != 0 }
        val cm = context.getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val caps = cm.getNetworkCapabilities(cm.activeNetwork)
        val network = when {
            caps == null -> "none"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> "wifi"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> "cellular"
            caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> "ethernet"
            else -> "other"
        }
        return JSONObject()
            .put("manufacturer", Build.MANUFACTURER)
            .put("model", Build.MODEL)
            .put("android", Build.VERSION.RELEASE)
            .put("api", Build.VERSION.SDK_INT)
            .put("battery", level ?: JSONObject.NULL)
            .put("charging", charging ?: JSONObject.NULL)
            .put("network", network)
            .put("app", BuildConfig.VERSION_CODE)
            .put("ops", JSONArray(names))
    }

    private fun typeName(s: Sensor): String = s.stringType.removePrefix("android.sensor.")

    private fun sensorList(): JSONArray {
        val out = JSONArray()
        for (s in sensors.getSensorList(Sensor.TYPE_ALL)) {
            out.put(
                JSONObject()
                    .put("type", typeName(s))
                    .put("name", s.name)
                    .put("vendor", s.vendor)
                    .put("range", s.maximumRange.toDouble())
                    .put("resolution", s.resolution.toDouble())
                    .put("min_delay_us", s.minDelay)
                    .put("wake_up", s.isWakeUpSensor),
            )
        }
        return out
    }

    /** The sensor named by its type, such as accelerometer or magnetic_field (sensor.list's type). */
    private fun sensorOf(type: String): Sensor =
        sensors.getSensorList(Sensor.TYPE_ALL).firstOrNull { typeName(it) == type }
            ?: throw OpFailure("no_sensor", "this phone has no $type sensor; sensor.list names those it has")

    /** Samples from one sensor for ``seconds`` at about ``rate`` per second, capped at MAX_SAMPLES. */
    private fun collect(sensor: Sensor, seconds: Double, rate: Int, each: (SensorEvent) -> JSONArray): JSONArray {
        val out = JSONArray()
        val done = CountDownLatch(1)
        val thread = HandlerThread("aegis-sensor").apply { start() }
        val listener = object : SensorEventListener {
            override fun onSensorChanged(e: SensorEvent) {
                synchronized(out) {
                    if (out.length() >= MAX_SAMPLES) {
                        done.countDown()
                        return
                    }
                    out.put(each(e))
                }
            }

            override fun onAccuracyChanged(s: Sensor, accuracy: Int) {}
        }
        val periodUs = 1_000_000 / rate.coerceIn(1, 200)
        sensors.registerListener(listener, sensor, periodUs, Handler(thread.looper))
        try {
            done.await((seconds * 1000).toLong(), TimeUnit.MILLISECONDS)
        } finally {
            sensors.unregisterListener(listener)
            thread.quitSafely()
        }
        return synchronized(out) { JSONArray(out.toString()) }
    }

    private fun seconds(args: JSONObject): Double = args.optDouble("seconds", 2.0).let { if (it.isNaN()) 2.0 else it.coerceIn(0.1, 60.0) }

    private fun sensorRead(args: JSONObject): JSONObject {
        val type = args.optString("type").ifEmpty { throw OpFailure("bad_args", "sensor.read needs type, such as accelerometer") }
        val sensor = sensorOf(type)
        val start = System.nanoTime()
        val samples = collect(sensor, seconds(args), args.optInt("rate", 20)) { e ->
            JSONArray().put((e.timestamp - start) / 1_000_000).apply { for (v in e.values) put(v.toDouble()) }
        }
        return JSONObject()
            .put("type", type)
            .put("name", sensor.name)
            .put("columns", "t_ms, then the sensor's values in Android's order")
            .put("count", samples.length())
            .put("samples", samples)
    }

    /** Azimuth, pitch and roll in degrees from the rotation vector, and their mean azimuth. */
    private fun compassRead(args: JSONObject): JSONObject {
        val sensor = sensors.getDefaultSensor(Sensor.TYPE_ROTATION_VECTOR)
            ?: sensors.getDefaultSensor(Sensor.TYPE_GEOMAGNETIC_ROTATION_VECTOR)
            ?: throw OpFailure("no_sensor", "this phone has no rotation vector sensor")
        val start = System.nanoTime()
        val m = FloatArray(9)
        val o = FloatArray(3)
        val samples = collect(sensor, seconds(args), args.optInt("rate", 10)) { e ->
            SensorManager.getRotationMatrixFromVector(m, e.values)
            SensorManager.getOrientation(m, o)
            val az = (Math.toDegrees(o[0].toDouble()) + 360) % 360
            JSONArray()
                .put((e.timestamp - start) / 1_000_000)
                .put(round1(az))
                .put(round1(Math.toDegrees(o[1].toDouble())))
                .put(round1(Math.toDegrees(o[2].toDouble())))
        }
        var sx = 0.0
        var sy = 0.0
        for (i in 0 until samples.length()) {
            val a = Math.toRadians(samples.getJSONArray(i).getDouble(1))
            sx += Math.cos(a)
            sy += Math.sin(a)
        }
        val mean = if (samples.length() == 0) JSONObject.NULL else round1((Math.toDegrees(Math.atan2(sy, sx)) + 360) % 360)
        return JSONObject()
            .put("sensor", sensor.name)
            .put("columns", "t_ms, azimuth, pitch, roll (degrees; azimuth from magnetic north)")
            .put("azimuth", mean)
            .put("count", samples.length())
            .put("samples", samples)
    }

    private fun round1(v: Double): Double = Math.round(v * 10) / 10.0

    companion object {
        const val MAX_SAMPLES = 2000
    }
}
