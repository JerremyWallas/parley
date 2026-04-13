package com.antigravity.speechtotext

import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import org.json.JSONObject
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

        OkHttpClient.Builder()
            .sslSocketFactory(sslContext.socketFactory, trustManager)
            .hostnameVerifier { _, _ -> true }
            .connectTimeout(10, TimeUnit.SECONDS)
            .readTimeout(120, TimeUnit.SECONDS)
            .writeTimeout(60, TimeUnit.SECONDS)
            .build()
    }

    fun transcribe(serverUrl: String, audioData: ByteArray, mode: String): TranscriptionResult {
        val url = "${serverUrl.trimEnd('/')}/api/transcribe"

        val body = MultipartBody.Builder()
            .setType(MultipartBody.FORM)
            .addFormDataPart(
                "audio", "recording.wav",
                audioData.toRequestBody("audio/wav".toMediaType()),
            )
            .addFormDataPart("mode", mode)
            .build()

        val request = Request.Builder()
            .url(url)
            .post(body)
            .build()

        val response = client.newCall(request).execute()
        if (!response.isSuccessful) {
            throw RuntimeException("API error: ${response.code}")
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
