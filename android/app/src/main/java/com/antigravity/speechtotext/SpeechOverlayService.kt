package com.antigravity.speechtotext

import android.accessibilityservice.AccessibilityService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.graphics.PixelFormat
import android.graphics.Rect
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.util.DisplayMetrics
import android.util.Log
import android.view.Gravity
import android.view.LayoutInflater
import android.view.MotionEvent
import android.view.View
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import android.widget.ImageView
import android.widget.Toast
import kotlin.concurrent.thread

class SpeechOverlayService : AccessibilityService() {

    companion object {
        private const val TAG = "SpeechOverlay"
        private const val PREFS_KEY = "stt_prefs"
        private const val PREF_BUTTON_X = "overlay_btn_x"
        private const val PREF_BUTTON_Y = "overlay_btn_y"
        private const val PREF_HAS_CUSTOM_POS = "overlay_has_custom_pos"
        private const val LONG_PRESS_MS = 300L
        private const val TOUCH_SLOP_SQ_PX = 100 // ~10px²
    }

    private enum class TouchState { IDLE, PENDING, DRAGGING, RECORDING_PTT, RECORDING_HANDS_FREE }

    private lateinit var windowManager: WindowManager
    private lateinit var overlayView: View
    private lateinit var overlayBtn: ImageView
    private lateinit var layoutParams: WindowManager.LayoutParams
    private var recordingService: RecordingService? = null
    private var isRecording = false
    private var isOverlayVisible = false
    private var isKeyboardVisible = false

    private val recordingConnection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            recordingService = (binder as? RecordingService.LocalBinder)?.getService()
            Log.i(TAG, "RecordingService bound")
        }
        override fun onServiceDisconnected(name: ComponentName?) {
            recordingService = null
            Log.w(TAG, "RecordingService disconnected")
        }
    }

    // Touch state
    private var touchState = TouchState.IDLE
    private var initialX = 0
    private var initialY = 0
    private var initialTouchX = 0f
    private var initialTouchY = 0f
    private val handler = Handler(Looper.getMainLooper())
    private val longPressRunnable = Runnable {
        if (touchState == TouchState.PENDING) {
            touchState = TouchState.RECORDING_PTT
            startRecording()
        }
    }

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i(TAG, "Service connected")
        bindService(
            Intent(this, RecordingService::class.java),
            recordingConnection,
            Context.BIND_AUTO_CREATE,
        )
        initOverlay()
    }

    private fun initOverlay() {
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager

        layoutParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        )
        layoutParams.gravity = Gravity.TOP or Gravity.START

        // Load saved position or use default (right side, middle)
        val prefs = getSharedPreferences(PREFS_KEY, MODE_PRIVATE)
        if (prefs.getBoolean(PREF_HAS_CUSTOM_POS, false)) {
            layoutParams.x = prefs.getInt(PREF_BUTTON_X, 0)
            layoutParams.y = prefs.getInt(PREF_BUTTON_Y, 300)
        } else {
            // Default: right edge, middle of screen
            val metrics = DisplayMetrics()
            @Suppress("DEPRECATION")
            windowManager.defaultDisplay.getMetrics(metrics)
            layoutParams.x = metrics.widthPixels - 80
            layoutParams.y = metrics.heightPixels / 2
        }

        overlayView = LayoutInflater.from(this).inflate(R.layout.overlay_button, null)
        overlayBtn = overlayView.findViewById(R.id.overlayBtn)

        overlayBtn.setOnTouchListener { _, event ->
            handleTouch(event)
            true
        }

        // Don't add view yet — wait for keyboard
        Log.i(TAG, "Overlay initialized, waiting for keyboard")
    }

    private fun showOverlay() {
        if (isOverlayVisible) return
        try {
            windowManager.addView(overlayView, layoutParams)
            isOverlayVisible = true
            Log.i(TAG, "Overlay shown (keyboard visible)")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show overlay", e)
        }
    }

    private fun hideOverlay() {
        if (!isOverlayVisible) return
        // Cancel any ongoing recording or pending long-press
        handler.removeCallbacks(longPressRunnable)
        if (isRecording) cancelRecording()
        touchState = TouchState.IDLE
        try {
            windowManager.removeView(overlayView)
            isOverlayVisible = false
            Log.i(TAG, "Overlay hidden (keyboard hidden)")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to hide overlay", e)
        }
    }

    private fun saveButtonPosition() {
        getSharedPreferences(PREFS_KEY, MODE_PRIVATE).edit()
            .putInt(PREF_BUTTON_X, layoutParams.x)
            .putInt(PREF_BUTTON_Y, layoutParams.y)
            .putBoolean(PREF_HAS_CUSTOM_POS, true)
            .apply()
    }

    // --- Keyboard Detection ---

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        if (event == null) return

        when (event.eventType) {
            AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED -> {
                checkKeyboardState()
            }
            AccessibilityEvent.TYPE_WINDOW_CONTENT_CHANGED -> {
                // Some keyboards trigger content changes
                checkKeyboardState()
            }
        }
    }

    private fun checkKeyboardState() {
        val wasVisible = isKeyboardVisible
        isKeyboardVisible = isInputMethodVisible()

        if (isKeyboardVisible && !wasVisible) {
            showOverlay()
        } else if (!isKeyboardVisible && wasVisible) {
            hideOverlay()
        }
    }

    private fun isInputMethodVisible(): Boolean {
        // Check all windows for an input method window
        try {
            for (window in windows) {
                if (window.type == android.view.accessibility.AccessibilityWindowInfo.TYPE_INPUT_METHOD) {
                    return true
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Could not check windows: ${e.message}")
        }
        return false
    }

    // --- Touch Handling ---

    private fun handleTouch(event: MotionEvent) {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> onTouchDown(event)
            MotionEvent.ACTION_MOVE -> onTouchMove(event)
            MotionEvent.ACTION_UP -> onTouchUp()
            MotionEvent.ACTION_CANCEL -> onTouchCancel()
        }
    }

    private fun onTouchDown(event: MotionEvent) {
        when (touchState) {
            TouchState.RECORDING_HANDS_FREE -> {
                // Second tap ends hands-free recording
                touchState = TouchState.IDLE
                stopRecordingAndTranscribe()
            }
            TouchState.IDLE -> {
                initialX = layoutParams.x
                initialY = layoutParams.y
                initialTouchX = event.rawX
                initialTouchY = event.rawY
                touchState = TouchState.PENDING
                handler.postDelayed(longPressRunnable, LONG_PRESS_MS)
            }
            else -> {
                // Stray DOWN during PENDING/DRAGGING/RECORDING_PTT — ignore
            }
        }
    }

    private fun onTouchMove(event: MotionEvent) {
        val dx = event.rawX - initialTouchX
        val dy = event.rawY - initialTouchY
        val movedBeyondSlop = dx * dx + dy * dy > TOUCH_SLOP_SQ_PX

        when (touchState) {
            TouchState.PENDING -> {
                if (movedBeyondSlop) {
                    handler.removeCallbacks(longPressRunnable)
                    touchState = TouchState.DRAGGING
                    updateOverlayPosition(dx, dy)
                }
            }
            TouchState.DRAGGING -> updateOverlayPosition(dx, dy)
            else -> {
                // Ignore movement during recording — button is pinned
            }
        }
    }

    private fun onTouchUp() {
        when (touchState) {
            TouchState.PENDING -> {
                // Quick tap → start hands-free recording
                handler.removeCallbacks(longPressRunnable)
                touchState = TouchState.RECORDING_HANDS_FREE
                startRecording()
            }
            TouchState.DRAGGING -> {
                touchState = TouchState.IDLE
                saveButtonPosition()
            }
            TouchState.RECORDING_PTT -> {
                touchState = TouchState.IDLE
                stopRecordingAndTranscribe()
            }
            TouchState.RECORDING_HANDS_FREE, TouchState.IDLE -> {
                // Hands-free: stays recording after the start-tap lifts.
                // Idle: nothing to do.
            }
        }
    }

    private fun onTouchCancel() {
        handler.removeCallbacks(longPressRunnable)
        when (touchState) {
            TouchState.DRAGGING -> saveButtonPosition()
            TouchState.RECORDING_PTT, TouchState.RECORDING_HANDS_FREE -> cancelRecording()
            else -> { /* no-op */ }
        }
        touchState = TouchState.IDLE
    }

    private fun updateOverlayPosition(dx: Float, dy: Float) {
        layoutParams.x = initialX + dx.toInt()
        layoutParams.y = initialY + dy.toInt()
        if (isOverlayVisible) {
            windowManager.updateViewLayout(overlayView, layoutParams)
        }
    }

    // --- Recording ---

    private fun startRecording() {
        // Promote RecordingService to foreground (needed for mic capture on API 34+).
        // The service receives ACTION_START in onStartCommand and starts the AudioRecorder.
        try {
            startForegroundService(
                Intent(this, RecordingService::class.java).setAction(RecordingService.ACTION_START),
            )
            isRecording = true
            overlayBtn.setBackgroundResource(R.drawable.overlay_bg_recording)
            Log.i(TAG, "Recording started")
        } catch (e: Exception) {
            Log.e(TAG, "Failed to start recording", e)
            Toast.makeText(this, "Aufnahme konnte nicht gestartet werden", Toast.LENGTH_SHORT).show()
        }
    }

    private fun cancelRecording() {
        if (isRecording) {
            recordingService?.cancel()
            isRecording = false
            overlayBtn.setBackgroundResource(R.drawable.overlay_bg)
            Log.i(TAG, "Recording cancelled")
        }
    }

    private fun stopRecordingAndTranscribe() {
        if (!isRecording) return
        isRecording = false

        val audioData = recordingService?.stopAndGetAudio()
        overlayBtn.setBackgroundResource(R.drawable.overlay_bg)
        if (audioData == null) {
            Log.w(TAG, "No audio data — recording service unavailable")
            return
        }

        if (audioData.size < 100) {
            Log.w(TAG, "Audio too short, ignoring")
            return
        }

        val prefs = getSharedPreferences(PREFS_KEY, MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "") ?: ""
        val mode = prefs.getString("mode", "raw") ?: "raw"

        if (serverUrl.isBlank()) {
            Toast.makeText(this, "Server-URL nicht konfiguriert", Toast.LENGTH_SHORT).show()
            return
        }

        // Show processing indicator
        overlayBtn.alpha = 0.5f
        Toast.makeText(this, "Verarbeite...", Toast.LENGTH_SHORT).show()

        thread {
            try {
                val result = ApiClient.transcribe(serverUrl, audioData, mode)
                val text = result.processedText.ifBlank { result.rawText }

                // Insert text into focused field
                val focused = findFocusedEditText(rootInActiveWindow)
                if (focused != null) {
                    val args = Bundle().apply {
                        putCharSequence(
                            AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                            text,
                        )
                    }
                    focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
                    Log.i(TAG, "Text inserted: ${text.take(80)}...")

                    // Auto-Send: loest die IME-Enter-Aktion im Zielfeld aus (API 30+).
                    if (prefs.getBoolean("auto_send", false)) {
                        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
                            val sent = focused.performAction(
                                AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id,
                            )
                            Log.i(TAG, "Auto-send IME_ENTER performed=$sent")
                        } else {
                            Log.w(TAG, "Auto-send needs Android 11+ (API 30)")
                        }
                    }
                } else {
                    // Fallback: copy to clipboard
                    val clipboard = getSystemService(Context.CLIPBOARD_SERVICE) as android.content.ClipboardManager
                    clipboard.setPrimaryClip(android.content.ClipData.newPlainText("STT", text))

                    overlayBtn.post {
                        Toast.makeText(this, "In Zwischenablage kopiert", Toast.LENGTH_SHORT).show()
                    }
                    Log.i(TAG, "No focused field, copied to clipboard")
                }
            } catch (e: Exception) {
                Log.e(TAG, "Transcription failed", e)
                overlayBtn.post {
                    Toast.makeText(this, "Fehler: ${e.message}", Toast.LENGTH_SHORT).show()
                }
            } finally {
                overlayBtn.post {
                    overlayBtn.alpha = 1.0f
                }
            }
        }
    }

    private fun findFocusedEditText(node: AccessibilityNodeInfo?): AccessibilityNodeInfo? {
        if (node == null) return null

        if (node.isFocused && node.isEditable) return node

        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val result = findFocusedEditText(child)
            if (result != null) return result
        }
        return null
    }

    override fun onInterrupt() {
        Log.w(TAG, "Service interrupted")
    }

    override fun onDestroy() {
        super.onDestroy()
        handler.removeCallbacks(longPressRunnable)
        hideOverlay()
        cancelRecording()
        try {
            unbindService(recordingConnection)
        } catch (_: IllegalArgumentException) {
            // Not bound — ignore.
        }
    }
}
