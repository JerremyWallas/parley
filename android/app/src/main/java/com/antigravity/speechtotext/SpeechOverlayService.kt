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
    @Volatile private var retryingPending = false
    // Throttle für prewarmConnection: das Overlay erscheint mehrmals pro Stunde,
    // ein Health-Roundtrip pro Show wäre Strom-/Daten-Verschwendung. Alle 5 min
    // reicht — der Sinn ist nur, dass der TLS-Tunnel beim nächsten Record-Tap steht.
    @Volatile private var lastPrewarmMs = 0L
    private val prewarmIntervalMs = 5 * 60_000L

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
            warmConnectionAndRetryPending()
        } catch (e: Exception) {
            Log.e(TAG, "Failed to show overlay", e)
        }
    }

    /**
     * Beim Overlay-Show: TLS-Connection vorwärmen und ggf. zurückgestellte
     * Aufnahmen nachreichen. Beides asynchron, beides best-effort.
     */
    private fun warmConnectionAndRetryPending() {
        val prefs = getSharedPreferences(PREFS_KEY, MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "") ?: ""
        if (serverUrl.isBlank()) return

        val now = android.os.SystemClock.elapsedRealtime()
        if (now - lastPrewarmMs >= prewarmIntervalMs) {
            ApiClient.prewarmConnection(serverUrl)
            lastPrewarmMs = now
        }
        retryPendingRecordings(serverUrl)
    }

    private fun retryPendingRecordings(serverUrl: String) {
        if (retryingPending) return
        val items = PendingQueue.list(this)
        if (items.isEmpty()) return

        retryingPending = true
        thread {
            var transcribed = 0
            try {
                for (item in items) {
                    try {
                        val audioData = item.file.readBytes()
                        val result = ApiClient.transcribeWithRetry(
                            serverUrl = serverUrl,
                            audioData = audioData,
                            mode = item.mode,
                        )
                        ApiClient.saveHistory(
                            serverUrl = serverUrl,
                            rawText = result.rawText,
                            processedText = result.processedText,
                            mode = result.mode,
                            language = result.language,
                        )
                        PendingQueue.delete(item)
                        transcribed++
                    } catch (e: Exception) {
                        // Lassen wir liegen — beim nächsten Overlay-Open neuer Versuch.
                        Log.w(TAG, "Pending retry failed for ${item.file.name}: ${e.message}")
                    }
                }
            } finally {
                retryingPending = false
            }
            if (transcribed > 0) {
                handler.post {
                    val msg = if (transcribed == 1) {
                        "1 alte Aufnahme nachträglich transkribiert"
                    } else {
                        "$transcribed alte Aufnahmen nachträglich transkribiert"
                    }
                    Toast.makeText(this, msg, Toast.LENGTH_SHORT).show()
                }
            }
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
        // Bewusst nur TYPE_WINDOW_STATE_CHANGED — TYPE_WINDOW_CONTENT_CHANGED
        // feuert bei jedem Scroll, jeder Animation, jedem Caret-Blink. Filter
        // läuft zwar schon in accessibility_config.xml, hier zusätzliche
        // Defense-in-Depth, falls die Manifest-Config mal verstellt wird.
        if (event?.eventType == AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) {
            checkKeyboardState()
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
                val result = ApiClient.transcribeWithRetry(
                    serverUrl = serverUrl,
                    audioData = audioData,
                    mode = mode,
                    onRetry = { nextAttempt, totalAttempts ->
                        // Marshalling auf den Main-Thread — Toast erfordert es,
                        // und ohne diese Notification merkt der User nicht, dass
                        // unter schlechter Verbindung im Hintergrund noch gearbeitet wird.
                        overlayBtn.post {
                            Toast.makeText(
                                this,
                                "Erneuter Versuch ($nextAttempt/$totalAttempts)…",
                                Toast.LENGTH_SHORT,
                            ).show()
                        }
                    },
                )
                val text = result.processedText.ifBlank { result.rawText }

                // Mirror the entry into the server-side history so the Web-UI shows
                // overlay recordings too. Best-effort: a network glitch here must not
                // block the actual text insertion below.
                try {
                    ApiClient.saveHistory(
                        serverUrl = serverUrl,
                        rawText = result.rawText,
                        processedText = result.processedText,
                        mode = result.mode,
                        language = result.language,
                    )
                } catch (e: Exception) {
                    Log.w(TAG, "History save failed", e)
                }

                // Insert text into focused field
                val focused = findFocusedEditText(rootInActiveWindow)
                if (focused != null) {
                    insertAtCursor(focused, text)
                    Log.i(TAG, "Text inserted: ${text.take(80)}...")

                    if (prefs.getBoolean("auto_send", false)) {
                        scheduleAutoSend(focused)
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
                // 4xx → permanenter Client-Fehler, Queueing hilft nichts.
                // Alles andere (Netz weg, 5xx, Timeout) → speichern und beim
                // nächsten Overlay-Open erneut versuchen.
                val isClientError = (e as? ApiClient.HttpStatusException)
                    ?.let { it.status in 400..499 } == true
                if (isClientError) {
                    Log.e(TAG, "Transcription failed (client error)", e)
                    overlayBtn.post {
                        Toast.makeText(this, "Fehler: ${e.message}", Toast.LENGTH_SHORT).show()
                    }
                } else {
                    Log.e(TAG, "Transcription failed, queueing for retry", e)
                    val queued = PendingQueue.enqueue(this, audioData, mode)
                    overlayBtn.post {
                        val msg = if (queued) {
                            "Aufnahme gespeichert, wird später hochgeladen"
                        } else {
                            "Fehler: ${e.message}"
                        }
                        Toast.makeText(this, msg, Toast.LENGTH_LONG).show()
                    }
                }
            } finally {
                overlayBtn.post {
                    overlayBtn.alpha = 1.0f
                }
            }
        }
    }

    // --- Auto-Send ---
    //
    // WhatsApp/Telegram/Signal haben multi-line Eingabefelder: ACTION_IME_ENTER
    // fuegt dort nur einen Zeilenumbruch ein statt zu senden. Richtige Loesung:
    // separaten Send-Button per Accessibility-Traversal finden und klicken.
    // IME_ENTER bleibt Fallback fuer single-line Felder (Suchleisten etc.).
    private fun scheduleAutoSend(focused: AccessibilityNodeInfo) {
        handler.postDelayed({
            val root = rootInActiveWindow
            val sendBtn = root?.let { findSendButton(it) }
            if (sendBtn != null) {
                val clicked = sendBtn.performAction(AccessibilityNodeInfo.ACTION_CLICK)
                Log.i(
                    TAG,
                    "Auto-send via click on '${sendBtn.contentDescription ?: sendBtn.viewIdResourceName}' ok=$clicked",
                )
                return@postDelayed
            }
            val multiLine = try { focused.isMultiLine } catch (_: Throwable) { false }
            if (!multiLine && android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
                val ok = focused.performAction(
                    AccessibilityNodeInfo.AccessibilityAction.ACTION_IME_ENTER.id,
                )
                Log.i(TAG, "Auto-send via IME_ENTER ok=$ok")
            } else {
                Log.w(TAG, "Auto-send: kein Send-Button gefunden und Multi-Line-Feld (kein IME_ENTER)")
            }
        }, 150L)
    }

    private fun findSendButton(node: AccessibilityNodeInfo): AccessibilityNodeInfo? {
        if (matchesSendButton(node)) return node
        for (i in 0 until node.childCount) {
            val child = node.getChild(i) ?: continue
            val hit = findSendButton(child)
            if (hit != null) return hit
        }
        return null
    }

    private fun matchesSendButton(node: AccessibilityNodeInfo): Boolean {
        if (!node.isClickable || !node.isEnabled || !node.isVisibleToUser) return false
        val desc = node.contentDescription?.toString()?.trim()?.lowercase().orEmpty()
        val text = node.text?.toString()?.trim()?.lowercase().orEmpty()
        val idSuffix = node.viewIdResourceName?.substringAfterLast('/')?.lowercase().orEmpty()

        val descKeywords = setOf(
            "send", "senden", "nachricht senden", "schicken",
            "envoyer", "enviar", "invia", "inviare",
        )
        val idKeywords = setOf(
            "send", "send_button", "sendbutton", "btnsend", "btn_send",
            "action_send", "sendicon", "send_icon",
        )
        val textKeywords = setOf("send", "senden")

        return desc in descKeywords ||
            idSuffix in idKeywords ||
            text in textKeywords
    }

    /**
     * Insert [newText] into [focused].
     *
     * Behaviour:
     *   - No active selection (caret only)  → splice newText in at the caret,
     *     keeping everything that was already in the field.
     *   - Active selection (start != end)   → replace the selected range with
     *     newText, treating the selection as the user's explicit "overwrite
     *     this part" intent.
     *
     * Why this exists: bare ACTION_SET_TEXT replaces the entire field, so
     * apps that auto-select all text on focus (some chat composers) would
     * silently nuke the user's previous input. This implementation matches
     * native typing behaviour — type with selection = replace selection;
     * type without selection = insert at caret.
     */
    private fun insertAtCursor(focused: AccessibilityNodeInfo, newText: String) {
        val rawStart = focused.textSelectionStart
        val rawEnd = focused.textSelectionEnd

        // Custom-Composer-Falle (WhatsApp & Co.): das Feld gibt seinen Placeholder
        // ("Nachricht") als node.text zurück und meldet selStart/selEnd = -1.
        // Ohne Cursor-Info können wir nicht verlässlich splicen — und an "Nachricht"
        // appenden ergibt genau den Bug, den wir vermeiden wollen. Der -1-Zustand
        // tritt im Test nur bei leerem Feld auf (echter Inhalt kommt mit gültigen
        // Selection-Indizes), also schreiben wir hier einfach den neuen Text.
        if (rawStart < 0 || rawEnd < 0) {
            val args = Bundle().apply {
                putCharSequence(
                    AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                    newText,
                )
            }
            if (!focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)) {
                Log.w(TAG, "ACTION_SET_TEXT returned false — field may not support edits")
            }
            return
        }

        val existing = focused.text?.toString() ?: ""
        val from = minOf(rawStart, rawEnd).coerceIn(0, existing.length)
        val to = maxOf(rawStart, rawEnd).coerceIn(0, existing.length)
        val combined = existing.substring(0, from) + newText + existing.substring(to)

        val setArgs = Bundle().apply {
            putCharSequence(
                AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE,
                combined,
            )
        }
        val ok = focused.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, setArgs)
        if (!ok) {
            Log.w(TAG, "ACTION_SET_TEXT returned false — field may not support edits")
            return
        }

        // Move caret to just behind the freshly inserted text. Best-effort —
        // some fields ignore ACTION_SET_SELECTION; we accept that silently.
        val newCaret = from + newText.length
        val selectionArgs = Bundle().apply {
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_START_INT, newCaret)
            putInt(AccessibilityNodeInfo.ACTION_ARGUMENT_SELECTION_END_INT, newCaret)
        }
        focused.performAction(AccessibilityNodeInfo.ACTION_SET_SELECTION, selectionArgs)
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
