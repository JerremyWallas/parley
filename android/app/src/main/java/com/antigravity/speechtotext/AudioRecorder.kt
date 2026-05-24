package com.antigravity.speechtotext

import android.content.Context
import android.media.MediaRecorder
import android.os.Build
import android.util.Log
import java.io.File

/**
 * Records audio from the microphone as Ogg/Opus.
 *
 * Why Opus instead of WAV PCM:
 *   WAV PCM @ 16 kHz mono = ~32 KB/s. A 10-second recording = 320 KB.
 *   On flaky mobile networks (Edge, weak HSDPA, foreign roaming) that's
 *   borderline unsendable. Opus VBR @ 32 kbps = ~4 KB/s, the same recording
 *   shrinks to ~40 KB — still ~8× reduction with comfortable headroom for
 *   noisy environments. Server (server/main.py) accepts Ogg/Opus directly
 *   via ffmpeg/Whisper.
 *
 * Implementation note: MediaRecorder writes to a file descriptor, not a
 * ByteArray. We write to a temp file in cacheDir, then read it back in stop()
 * and delete it. Slightly wasteful, but MediaRecorder is dramatically simpler
 * than the alternative (AudioRecord → MediaCodec → manual Ogg muxing).
 */
class AudioRecorder(private val context: Context) {

    companion object {
        private const val TAG = "AudioRecorder"
        private const val SAMPLE_RATE = 16000
        private const val BIT_RATE = 32000  // 32 kbps VBR — Puffer für laute Umgebungen
    }

    private var recorder: MediaRecorder? = null
    private var outputFile: File? = null
    @Volatile private var recording = false

    fun start() {
        if (recording) {
            Log.w(TAG, "start() called while already recording — ignoring")
            return
        }

        val file = File.createTempFile("parley_rec_", ".ogg", context.cacheDir)
        val r = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
        } else {
            @Suppress("DEPRECATION")
            MediaRecorder()
        }

        try {
            // VOICE_RECOGNITION statt MIC: umgeht die OEM-DSP-Pipeline (AGC,
            // Noise-Suppression, Echo-Cancellation), die für Telefonie getuned
            // ist und bei Whisper leise Konsonanten wegfiltern kann.
            r.setAudioSource(MediaRecorder.AudioSource.VOICE_RECOGNITION)
            r.setOutputFormat(MediaRecorder.OutputFormat.OGG)
            r.setAudioEncoder(MediaRecorder.AudioEncoder.OPUS)
            r.setAudioSamplingRate(SAMPLE_RATE)
            r.setAudioChannels(1)
            r.setAudioEncodingBitRate(BIT_RATE)
            r.setOutputFile(file.absolutePath)
            r.prepare()
            r.start()
        } catch (e: Exception) {
            Log.e(TAG, "MediaRecorder setup failed", e)
            try { r.release() } catch (_: Exception) { /* ignore */ }
            file.delete()
            throw IllegalStateException("Failed to start Opus recorder: ${e.message}", e)
        }

        recorder = r
        outputFile = file
        recording = true
        Log.i(TAG, "Recording started → ${file.absolutePath}")
    }

    /**
     * Stop recording and return the encoded Ogg/Opus bytes. The temp file is
     * always deleted, even on failure. Returns an empty array if recording
     * never started or produced no audio.
     */
    fun stop(): ByteArray {
        val r = recorder
        val file = outputFile
        recorder = null
        outputFile = null
        recording = false

        if (r == null || file == null) return ByteArray(0)

        try {
            r.stop()
        } catch (e: RuntimeException) {
            // MediaRecorder.stop() throws if no valid audio was captured
            // (e.g. tap was too short). Swallow and continue — we'll return
            // whatever bytes are on disk.
            Log.w(TAG, "MediaRecorder.stop() threw — likely no audio captured: ${e.message}")
        } finally {
            try { r.reset() } catch (_: Exception) { /* ignore */ }
            try { r.release() } catch (_: Exception) { /* ignore */ }
        }

        return try {
            if (file.exists() && file.length() > 0) file.readBytes() else ByteArray(0)
        } catch (e: Exception) {
            Log.e(TAG, "Failed to read recorded audio", e)
            ByteArray(0)
        } finally {
            try { file.delete() } catch (_: Exception) { /* ignore */ }
        }
    }

    fun isRecording(): Boolean = recording
}
