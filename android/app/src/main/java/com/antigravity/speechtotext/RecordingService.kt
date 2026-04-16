package com.antigravity.speechtotext

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.Service
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Binder
import android.os.Build
import android.os.IBinder
import android.util.Log
import androidx.core.app.NotificationCompat

/**
 * Foreground service that owns the microphone recording lifecycle.
 *
 * Why this exists separately from SpeechOverlayService:
 *   Android 14+ requires foregroundServiceType="microphone" for any background mic capture.
 *   AccessibilityServices are a grey area — moving recording into a dedicated FGS makes the
 *   app forward-compatible with Android 16+ behavior changes.
 */
class RecordingService : Service() {

    companion object {
        private const val TAG = "RecordingService"
        const val ACTION_START = "com.antigravity.speechtotext.action.START_RECORDING"
        private const val NOTIFICATION_ID = 1001
        private const val CHANNEL_ID = "parley_recording"
    }

    private val binder = LocalBinder()
    private var recorder: AudioRecorder? = null
    private var foregroundActive = false

    inner class LocalBinder : Binder() {
        fun getService(): RecordingService = this@RecordingService
    }

    override fun onBind(intent: Intent?): IBinder = binder

    override fun onCreate() {
        super.onCreate()
        ensureChannel()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_START) {
            promoteToForeground()
            startCapture()
        }
        return START_NOT_STICKY
    }

    /** Called via binder by SpeechOverlayService when the user releases / taps to stop. */
    fun stopAndGetAudio(): ByteArray? {
        val data = try {
            recorder?.stop()
        } catch (e: Exception) {
            Log.e(TAG, "Recorder stop failed", e)
            null
        }
        recorder = null
        demoteForeground()
        return data
    }

    /** Called via binder when the user cancels (drag, swipe-away). */
    fun cancel() {
        try {
            recorder?.stop()
        } catch (e: Exception) {
            Log.w(TAG, "Recorder stop on cancel failed", e)
        }
        recorder = null
        demoteForeground()
    }

    fun isCapturing(): Boolean = recorder != null

    private fun startCapture() {
        if (recorder != null) {
            Log.w(TAG, "startCapture called while already recording — ignoring")
            return
        }
        try {
            val r = AudioRecorder()
            r.start()
            recorder = r
            Log.i(TAG, "Recording started")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start recorder", e)
            recorder = null
            demoteForeground()
        }
    }

    private fun promoteToForeground() {
        if (foregroundActive) return
        val notification = buildNotification()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_MICROPHONE,
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        foregroundActive = true
    }

    private fun demoteForeground() {
        if (!foregroundActive) return
        stopForeground(STOP_FOREGROUND_REMOVE)
        foregroundActive = false
    }

    private fun ensureChannel() {
        val nm = getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CHANNEL_ID) != null) return
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Parley Aufnahme",
            NotificationManager.IMPORTANCE_LOW,
        ).apply {
            description = "Wird angezeigt, solange Parley das Mikrofon nutzt."
            setShowBadge(false)
        }
        nm.createNotificationChannel(channel)
    }

    private fun buildNotification(): Notification {
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("Parley nimmt auf")
            .setContentText("Sprich jetzt — loslassen oder erneut tippen zum Stoppen.")
            .setOngoing(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    override fun onDestroy() {
        cancel()
        super.onDestroy()
    }
}
