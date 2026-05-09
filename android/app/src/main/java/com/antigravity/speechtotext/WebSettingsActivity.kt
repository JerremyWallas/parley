package com.antigravity.speechtotext

import android.Manifest
import android.content.Intent
import android.content.pm.PackageManager
import android.net.http.SslError
import android.os.Bundle
import android.provider.Settings
import android.view.View
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.SslErrorHandler
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.ProgressBar
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat

/**
 * Einziger Bildschirm der App: laedt die Web-UI (Aufnahme + Verlauf + Einstellungen)
 * in einer WebView. Die Web-UI schreibt via JavaScript-Bridge auch in die nativen
 * SharedPreferences, damit der Overlay-AccessibilityService in anderen Apps weiter
 * den korrekten Server/Mode/Auto-Send-Zustand kennt.
 */
class WebSettingsActivity : AppCompatActivity() {

    companion object {
        private const val REQ_RECORD_AUDIO = 1001
    }

    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar
    private var pendingServerUrl: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_web_settings)

        val root = findViewById<FrameLayout>(R.id.webRoot)
        webView = findViewById(R.id.webView)
        progress = findViewById(R.id.webProgress)

        // Status-Bar respektieren.
        ViewCompat.setOnApplyWindowInsetsListener(root) { v, insets ->
            val bars = insets.getInsets(WindowInsetsCompat.Type.systemBars())
            v.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }

        val prefs = getSharedPreferences("stt_prefs", MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "")?.trim()?.trimEnd('/') ?: ""
        if (serverUrl.isBlank()) {
            promptForServerUrl()
            return
        }

        ensureMicPermissionAndConfigure(serverUrl)
    }

    private fun ensureMicPermissionAndConfigure(serverUrl: String) {
        if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
            == PackageManager.PERMISSION_GRANTED) {
            configureWebView(serverUrl)
        } else {
            pendingServerUrl = serverUrl
            ActivityCompat.requestPermissions(
                this,
                arrayOf(Manifest.permission.RECORD_AUDIO),
                REQ_RECORD_AUDIO,
            )
        }
    }

    override fun onRequestPermissionsResult(
        requestCode: Int,
        permissions: Array<out String>,
        grantResults: IntArray,
    ) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        if (requestCode == REQ_RECORD_AUDIO) {
            // Egal ob erteilt oder verweigert — Web-UI laden. Wenn verweigert,
            // sieht der User weiter "Mikrofon-Zugriff verweigert" beim Aufnahme-Versuch.
            pendingServerUrl?.let { configureWebView(it) }
            pendingServerUrl = null
        }
    }

    private fun configureWebView(serverUrl: String) {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }

        webView.addJavascriptInterface(AndroidBridge(), "AndroidBridge")

        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                // Heimserver nutzt Self-Signed Cert — bewusst akzeptieren.
                handler.proceed()
            }

            override fun onPageFinished(view: WebView, url: String) {
                progress.visibility = View.GONE
            }
        }

        // WebView lehnt getUserMedia() per Default ab, selbst wenn der App-Prozess
        // RECORD_AUDIO hat. Ohne diesen WebChromeClient sieht der User in der
        // Web-UI die Meldung "Mikrofon-Zugriff verweigert".
        webView.webChromeClient = object : WebChromeClient() {
            override fun onPermissionRequest(request: PermissionRequest) {
                val wanted = request.resources
                    .filter { it == PermissionRequest.RESOURCE_AUDIO_CAPTURE }
                    .toTypedArray()
                val sysGranted = ContextCompat.checkSelfPermission(
                    this@WebSettingsActivity, Manifest.permission.RECORD_AUDIO,
                ) == PackageManager.PERMISSION_GRANTED
                if (wanted.isNotEmpty() && sysGranted) {
                    request.grant(wanted)
                } else {
                    request.deny()
                }
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        progress.visibility = View.VISIBLE
        webView.loadUrl(serverUrl)
    }

    private fun promptForServerUrl() {
        val input = EditText(this).apply {
            hint = "https://192.168.1.100:7443"
            inputType = android.text.InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
        }
        AlertDialog.Builder(this)
            .setTitle("Server-URL")
            .setMessage("Gib die URL deines Parley-Servers ein.")
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("Weiter") { _, _ ->
                val url = input.text.toString().trim().trimEnd('/')
                if (url.isBlank()) {
                    finish()
                } else {
                    getSharedPreferences("stt_prefs", MODE_PRIVATE).edit()
                        .putString("server_url", url)
                        .apply()
                    ensureMicPermissionAndConfigure(url)
                }
            }
            .setNegativeButton("Abbrechen") { _, _ -> finish() }
            .show()
    }

    override fun onDestroy() {
        if (::webView.isInitialized) {
            webView.stopLoading()
            webView.destroy()
        }
        super.onDestroy()
    }

    /**
     * Von der Web-UI via `window.AndroidBridge.*` aufrufbar.
     * Jede Methode laeuft auf einem JS-Thread — SharedPreferences ist thread-safe.
     */
    private inner class AndroidBridge {
        private val prefs = getSharedPreferences("stt_prefs", MODE_PRIVATE)

        @JavascriptInterface
        fun setMode(mode: String) {
            val normalized = when (mode) {
                "cleanup", "rephrase" -> mode
                else -> "raw"
            }
            prefs.edit().putString("mode", normalized).apply()
        }

        @JavascriptInterface
        fun setServerUrl(url: String) {
            val clean = url.trim().trimEnd('/')
            if (clean.isBlank()) return
            prefs.edit().putString("server_url", clean).apply()
            runOnUiThread {
                progress.visibility = View.VISIBLE
                webView.loadUrl(clean)
            }
        }

        @JavascriptInterface
        fun setAutoSend(enabled: Boolean) {
            prefs.edit().putBoolean("auto_send", enabled).apply()
        }

        @JavascriptInterface
        fun openAccessibilitySettings() {
            runOnUiThread {
                startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }
        }
    }
}
