package com.antigravity.speechtotext

import android.net.http.SslError
import android.os.Bundle
import android.view.View
import android.webkit.SslErrorHandler
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.ProgressBar
import android.widget.Toast
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

/**
 * Loads the existing web UI in a WebView so the user gets the same settings
 * experience as the browser version (1:1 identical, always in sync with the server).
 *
 * Auto-opens the settings modal after page load via injected JS.
 */
class WebSettingsActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private lateinit var progress: ProgressBar

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_web_settings)

        webView = findViewById(R.id.webView)
        progress = findViewById(R.id.webProgress)

        val prefs = getSharedPreferences("stt_prefs", MODE_PRIVATE)
        val serverUrl = prefs.getString("server_url", "")?.trim()?.trimEnd('/') ?: ""
        if (serverUrl.isBlank()) {
            Toast.makeText(this, "Bitte erst Server-URL speichern", Toast.LENGTH_LONG).show()
            finish()
            return
        }

        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            mediaPlaybackRequiresUserGesture = false
            mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_ALWAYS_ALLOW
        }

        webView.webViewClient = object : WebViewClient() {
            override fun onReceivedSslError(view: WebView, handler: SslErrorHandler, error: SslError) {
                // Heimserver nutzt Self-Signed Cert — bewusst akzeptieren.
                handler.proceed()
            }

            override fun onPageFinished(view: WebView, url: String) {
                progress.visibility = View.GONE
                // Auto-open the settings modal so the user lands directly in the settings panel.
                view.evaluateJavascript(
                    """
                    (function() {
                        var btn = document.getElementById('settingsBtn');
                        if (btn) btn.click();
                    })();
                    """.trimIndent(),
                    null,
                )
            }
        }

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) webView.goBack() else finish()
            }
        })

        webView.loadUrl(serverUrl)
    }

    override fun onDestroy() {
        webView.stopLoading()
        webView.destroy()
        super.onDestroy()
    }
}
