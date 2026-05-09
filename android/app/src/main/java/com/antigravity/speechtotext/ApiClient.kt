package com.antigravity.speechtotext

import android.util.Log
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
import java.io.IOException
import java.security.SecureRandom
import java.security.cert.X509Certificate
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLContext
import javax.net.ssl.TrustManager
import javax.net.ssl.X509TrustManager

data class TranscriptionResult(
    val rawText: String,
    val processedText: String,
    val mode: String,
    val language: String,
    val durationMs: Int,
)

object ApiClient {

    private const val TAG = "ApiClient"

    // SECURITY: SSL-Verifikation ist deaktiviert, da der Heimserver Self-Signed Certs nutzt.
    // Das ist im lokalen Netzwerk akzeptabel, aber kein Muster fuer Produktions-Apps.
    // Fuer den Einsatz mit richtigen Zertifikaten: TrustManager und HostnameVerifier entfernen.
    private val client: OkHttpClient by lazy {
        val trustManager = object : X509TrustManager {
            override fun checkClientTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun checkServerTrusted(chain: Array<X509Certificate>, authType: String) {}
            override fun getAcceptedIssuers(): Array<X509Certificate> = arrayOf()
        }

        val sslContext = SSLContext.getInstance("TLS").apply {
            init(null, arrayOf<TrustManager>(trustManager), SecureRandom())
        }

        // Timeouts mobilfunk-tauglich:
        //   connect 30s — toleriert TLS-Handshake-Aussetzer.
        //   write   240s — Opus-Aufnahmen sind ~10× kleiner als WAV, aber wir
        //                  lassen Luft für Edge/2G-artige Verbindungen.
        //   read    180s — LLM-Verarbeitung kann auf großen Modellen Sekunden dauern.
        // OkHttp.retryOnConnectionFailure ist by default true (single Connection-Retry
        // bei IO-Fehler) — wir ergänzen die fachliche Retry-Schleife oben drauf.
        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(30, TimeUnit.SECONDS)
            .readTimeout(180, TimeUnit.SECONDS)
            .writeTimeout(240, TimeUnit.SECONDS)
            .retryOnConnectionFailure(true)
            .build()
    }

    data class Preset(val id: String, val name: String)

    fun fetchPresets(serverUrl: String): List<Preset> {
        val url = "${serverUrl.trimEnd('/')}/api/presets"
        val request = Request.Builder().url(url).get().build()
        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) return emptyList()
            val json = JSONObject(response.body!!.string())
            val presets = mutableListOf<Preset>()
            val arr = json.optJSONArray("presets") ?: return emptyList()
            for (i in 0 until arr.length()) {
                val p = arr.getJSONObject(i)
                presets.add(Preset(p.optString("id", ""), p.optString("name", "")))
            }
            return presets
        }
    }

    fun setActivePreset(serverUrl: String, presetId: String) {
        val url = "${serverUrl.trimEnd('/')}/api/presets/active"
        val body = JSONObject().put("id", presetId).toString()
            .toRequestBody("application/json".toMediaType())
        val request = Request.Builder().url(url).put(body).build()
        client.newCall(request).execute().close()
    }

    fun saveHistory(
        serverUrl: String,
        rawText: String,
        processedText: String,
        mode: String,
        language: String,
    ) {
        val url = "${serverUrl.trimEnd('/')}/api/history"
        val body = JSONObject()
            .put("raw_text", rawText)
            .put("processed_text", processedText)
            .put("mode", mode)
            .put("language", language)
            .toString()
            .toRequestBody("application/json".toMediaType())
        val request = Request.Builder().url(url).post(body).build()
        client.newCall(request).execute().close()
    }

    /**
     * Custom exception that preserves the HTTP status — lets the retry layer
     * decide if it's worth retrying (5xx yes, 4xx no).
     */
    class HttpStatusException(val status: Int, message: String) : IOException(message)

    /**
     * Single transcription attempt. Throws IOException on network failure
     * or HttpStatusException on non-2xx response.
     */
    fun transcribe(serverUrl: String, audioData: ByteArray, mode: String): TranscriptionResult {
        val url = "${serverUrl.trimEnd('/')}/api/transcribe"

        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "audio", "recording.ogg",
                audioData.toRequestBody("audio/ogg".toMediaType()),
            )
            .addFormDataPart("mode", mode)
            .build()

        val request = Request.Builder()
            .url(url)
            .post(body)
            .build()

        client.newCall(request).execute().use { response ->
            if (!response.isSuccessful) {
                throw HttpStatusException(response.code, "API error: ${response.code}")
            }
            val json = JSONObject(response.body!!.string())
            return TranscriptionResult(
                rawText = json.optString("raw_text", ""),
                processedText = json.optString("processed_text", ""),
                mode = json.optString("mode", mode),
                language = json.optString("language", ""),
                durationMs = json.optInt("duration_ms", 0),
            )
        }
    }

    /**
     * Transcribe with exponential-backoff retry on transient failures.
     *
     * Retried: IOException (DNS, connect, read/write timeout, conn reset),
     *          HttpStatusException with status >= 500 (server overload).
     * Not retried: HttpStatusException 4xx (client error — won't fix itself).
     *
     * @param onRetry  optional callback: (nextAttempt, totalAttempts), both
     *                 1-indexed. Fires after a failure, just before the
     *                 backoff sleep, so callers can show "Erneuter Versuch
     *                 (2/4)…" while the next attempt is queuing.
     */
    fun transcribeWithRetry(
        serverUrl: String,
        audioData: ByteArray,
        mode: String,
        maxRetries: Int = 3,
        onRetry: ((nextAttempt: Int, totalAttempts: Int) -> Unit)? = null,
    ): TranscriptionResult {
        val backoffMs = longArrayOf(2000L, 4000L, 8000L)
        val totalAttempts = maxRetries + 1
        var lastError: Exception? = null

        for (attempt in 0..maxRetries) {
            try {
                return transcribe(serverUrl, audioData, mode)
            } catch (e: HttpStatusException) {
                if (e.status < 500) throw e  // 4xx — don't retry
                lastError = e
                Log.w(TAG, "Attempt ${attempt + 1}/$totalAttempts failed: HTTP ${e.status}")
            } catch (e: IOException) {
                lastError = e
                Log.w(TAG, "Attempt ${attempt + 1}/$totalAttempts failed: ${e.message}")
            }

            if (attempt < maxRetries) {
                // attempt is 0-indexed and just failed → the *next* attempt is attempt+2 (1-indexed).
                onRetry?.invoke(attempt + 2, totalAttempts)
                val delay = backoffMs.getOrElse(attempt) { backoffMs.last() }
                try {
                    Thread.sleep(delay)
                } catch (ie: InterruptedException) {
                    Thread.currentThread().interrupt()
                    throw IOException("Retry interrupted", ie)
                }
            }
        }

        throw lastError ?: IOException("Transcription failed after $totalAttempts attempts")
    }
}
