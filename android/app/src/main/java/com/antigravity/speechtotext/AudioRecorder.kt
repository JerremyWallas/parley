package com.antigravity.speechtotext

import android.media.AudioFormat
import android.media.AudioRecord
import android.media.MediaRecorder
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

class AudioRecorder(
    private val sampleRate: Int = 16000,
    private val channelConfig: Int = AudioFormat.CHANNEL_IN_MONO,
    private val audioFormat: Int = AudioFormat.ENCODING_PCM_16BIT,
) {
    private var audioRecord: AudioRecord? = null
    private var isRecording = false
    private var recordingThread: Thread? = null
    private val audioData = ByteArrayOutputStream()

    fun start() {
        val bufferSize = AudioRecord.getMinBufferSize(sampleRate, channelConfig, audioFormat)

        audioRecord = AudioRecord(
            MediaRecorder.AudioSource.MIC,
            sampleRate,
            channelConfig,
            audioFormat,
            bufferSize * 2,
        )

        audioData.reset()
        isRecording = true
        audioRecord?.startRecording()

        recordingThread = Thread {
            val buffer = ByteArray(bufferSize)
            while (isRecording) {
                val read = audioRecord?.read(buffer, 0, buffer.size) ?: -1
                if (read > 0) {
                    synchronized(audioData) {
                        audioData.write(buffer, 0, read)
                    }
                }
            }
        }.also { it.start() }
    }

    fun stop(): ByteArray {
        isRecording = false
        recordingThread?.join(1000)
        audioRecord?.stop()
        audioRecord?.release()
        audioRecord = null

        val pcmData = synchronized(audioData) { audioData.toByteArray() }
        return createWav(pcmData)
    }

    private fun createWav(pcmData: ByteArray): ByteArray {
        val channels = if (channelConfig == AudioFormat.CHANNEL_IN_MONO) 1 else 2
        val bitsPerSample = 16
        val byteRate = sampleRate * channels * bitsPerSample / 8
        val blockAlign = channels * bitsPerSample / 8
        val dataSize = pcmData.size
        val totalSize = 36 + dataSize

        val buffer = ByteBuffer.allocate(44 + dataSize).order(ByteOrder.LITTLE_ENDIAN)

        // RIFF header
        buffer.put("RIFF".toByteArray())
        buffer.putInt(totalSize)
        buffer.put("WAVE".toByteArray())

        // fmt chunk
        buffer.put("fmt ".toByteArray())
        buffer.putInt(16) // chunk size
        buffer.putShort(1) // PCM format
        buffer.putShort(channels.toShort())
        buffer.putInt(sampleRate)
        buffer.putInt(byteRate)
        buffer.putShort(blockAlign.toShort())
        buffer.putShort(bitsPerSample.toShort())

        // data chunk
        buffer.put("data".toByteArray())
        buffer.putInt(dataSize)
        buffer.put(pcmData)

        return buffer.array()
    }
}
