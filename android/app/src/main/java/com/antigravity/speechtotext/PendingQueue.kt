package com.antigravity.speechtotext

import android.content.Context
import android.util.Log
import java.io.File

/**
 * Persistent queue für Aufnahmen, deren Transkription endgültig fehlgeschlagen
 * ist (z. B. weil offline). Beim nächsten Overlay-Open versucht der Caller
 * jede Pending-Aufnahme erneut zu transkribieren und ins Server-Verlauf zu
 * schieben.
 *
 * Storage: filesDir/pending/pending_<timestamp>_<mode>.ogg
 *   filesDir, nicht cacheDir — Android darf cacheDir jederzeit leeren.
 *   timestamp dient als FIFO-Ordnung; mode steckt im Dateinamen, weil wir
 *   beim Retry den ursprünglichen Modus wiederherstellen müssen.
 */
object PendingQueue {

    private const val TAG = "PendingQueue"
    private const val DIR = "pending"

    data class PendingItem(val file: File, val mode: String, val timestampMs: Long)

    private fun dir(context: Context): File =
        File(context.filesDir, DIR).apply { if (!exists()) mkdirs() }

    /**
     * Persistiert ein Audio-Blob. Gibt true zurück, wenn das Schreiben geklappt hat.
     */
    fun enqueue(context: Context, audioData: ByteArray, mode: String): Boolean {
        return try {
            val safeMode = mode.replace(Regex("[^a-zA-Z0-9_-]"), "_").ifBlank { "raw" }
            // Zwei Enqueues in derselben Millisekunde dürfen sich nicht überschreiben —
            // Timestamp hochzählen statt Format ändern (hält Parser + FIFO intakt).
            var ts = System.currentTimeMillis()
            var file = File(dir(context), "pending_${ts}_$safeMode.ogg")
            while (file.exists()) {
                ts++
                file = File(dir(context), "pending_${ts}_$safeMode.ogg")
            }
            file.writeBytes(audioData)
            Log.i(TAG, "Enqueued ${audioData.size} bytes → ${file.name}")
            true
        } catch (e: Exception) {
            Log.e(TAG, "Failed to enqueue audio", e)
            false
        }
    }

    /**
     * Liefert die aktuell gespeicherten Pending-Items, FIFO-sortiert.
     */
    fun list(context: Context): List<PendingItem> {
        val files = dir(context).listFiles { f -> f.isFile && f.name.endsWith(".ogg") }
            ?: return emptyList()
        return files.mapNotNull { f ->
            // pending_<ts>_<mode>
            val parts = f.nameWithoutExtension.split('_')
            if (parts.size < 3 || parts[0] != "pending") return@mapNotNull null
            val ts = parts[1].toLongOrNull() ?: return@mapNotNull null
            val mode = parts.drop(2).joinToString("_")
            PendingItem(f, mode, ts)
        }.sortedBy { it.timestampMs }
    }

    fun delete(item: PendingItem) {
        try { item.file.delete() } catch (_: Exception) { /* ignore */ }
    }
}
