package com.antigravity.speechtotext

import android.accessibilityservice.AccessibilityService
import android.content.Context
import android.graphics.PixelFormat
import android.os.Bundle
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
    }

    private lateinit var windowManager: WindowManager
    private lateinit var overlayView: View
    private lateinit var overlayBtn: ImageView
    private var audioRecorder: AudioRecorder? = null
    private var isRecording = false
    private var initialX = 0
    private var initialY = 0
    private var initialTouchX = 0f
    private var initialTouchY = 0f
    private var hasMoved = false

    override fun onServiceConnected() {
        super.onServiceConnected()
        Log.i(TAG, "Service connected")
        createOverlay()
    }

    private fun createOverlay() {
        windowManager = getSystemService(Context.WINDOW_SERVICE) as WindowManager

        val layoutParams = WindowManager.LayoutParams(
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.WRAP_CONTENT,
            WindowManager.LayoutParams.TYPE_ACCESSIBILITY_OVERLAY,
            WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
                    WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
            PixelFormat.TRANSLUCENT,
        )
        layoutParams.gravity = Gravity.TOP or Gravity.START
        layoutParams.x = 0
        layoutParams.y = 300

        overlayView = LayoutInflater.from(this).inflate(R.layout.overlay_button, null)
        overlayBtn = overlayView.findViewById(R.id.overlayBtn)

        overlayBtn.setOnTouchListener { _, event ->
            handleTouch(event, layoutParams)
            true
        }

        windowManager.addView(overlayView, layoutParams)
        Log.i(TAG, "Overlay created")
    }

    private fun handleTouch(event: MotionEvent, params: WindowManager.LayoutParams) {
        when (event.action) {
            MotionEvent.ACTION_DOWN -> {
                initialX = params.x
                initialY = params.y
                initialTouchX = event.rawX
                initialTouchY = event.rawY
                hasMoved = false
                startRecording()
            }

            MotionEvent.ACTION_MOVE -> {
                val dx = event.rawX - initialTouchX
                val dy = event.rawY - initialTouchY
                if (dx * dx + dy * dy > 100) { // Moved more than 10px
                    hasMoved = true
                    params.x = initialX + dx.toInt()
                    params.y = initialY + dy.toInt()
                    windowManager.updateViewLayout(overlayView, params)

                    // Cancel recording if dragging
                    if (isRecording) {
                        cancelRecording()
                    }
                }
            }

            MotionEvent.ACTION_UP -> {
                if (!hasMoved && isRecording) {
                    stopRecordingAndTranscribe()
                }
            }

            MotionEvent.ACTION_CANCEL -> {
                cancelRecording()
            }
        }
    }

    private fun startRecording() {
        try {
            audioRecorder = AudioRecorder()
            audioRecorder?.start()
            isRecording = true
            overlayBtn.setBackgroundResource(R.drawable.overlay_bg_recording)
            Log.i(TAG, "Recording started")
        } catch (e: SecurityException) {
            Log.e(TAG, "Microphone permission denied", e)
            Toast.makeText(this, "Mikrofon-Berechtigung fehlt", Toast.LENGTH_SHORT).show()
        }
    }

    private fun cancelRecording() {
        if (isRecording) {
            audioRecorder?.stop()
            isRecording = false
            overlayBtn.setBackgroundResource(R.drawable.overlay_bg)
            Log.i(TAG, "Recording cancelled")
        }
    }

    private fun stopRecordingAndTranscribe() {
        if (!isRecording) return
        isRecording = false

        val audioData = audioRecorder?.stop() ?: return
        overlayBtn.setBackgroundResource(R.drawable.overlay_bg)

        if (audioData.size < 100) {
            Log.w(TAG, "Audio too short, ignoring")
            return
        }

        val prefs = getSharedPreferences("stt_prefs", MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "") ?: ""
        val mode = prefs.getString("mode", "raw") ?: "raw"

        if (serverUrl.isBlank()) {
            Toast.makeText(this, "Server-URL nicht konfiguriert", Toast.LENGTH_SHORT).show()
            return
        }

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

    override fun onAccessibilityEvent(event: AccessibilityEvent?) {
        // Not needed, we use the overlay
    }

    override fun onInterrupt() {
        Log.w(TAG, "Service interrupted")
    }

    override fun onDestroy() {
        super.onDestroy()
        if (::overlayView.isInitialized) {
            windowManager.removeView(overlayView)
        }
        cancelRecording()
    }
}
