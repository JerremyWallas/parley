import io
import threading
import wave
import sounddevice as sd
import numpy as np


class AudioRecorder:
    """Records audio from the default microphone with optional chunk streaming."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: list[np.ndarray] = []
        self._stream = None
        self._recording = False
        self._lock = threading.Lock()
        self._on_chunk = None
        self._chunk_frames: list[np.ndarray] = []
        self._chunk_size = 0  # frames per chunk (0 = no chunking)

    def start(self, on_chunk=None, chunk_interval_ms: int = 500):
        """Start recording. If on_chunk is provided, calls it with raw PCM bytes every chunk_interval_ms."""
        with self._lock:
            self._frames = []
            self._chunk_frames = []
            self._recording = True
            self._on_chunk = on_chunk
            self._chunk_size = int(self.sample_rate * chunk_interval_ms / 1000) if on_chunk else 0

            self._stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=self.channels,
                dtype="int16",
                callback=self._callback,
            )
            self._stream.start()

    def stop(self) -> bytes:
        with self._lock:
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None

            # Send any remaining chunk frames as raw PCM
            if self._on_chunk and self._chunk_frames:
                pcm = np.concatenate(self._chunk_frames, axis=0).tobytes()
                self._on_chunk(pcm)
                self._chunk_frames = []

            self._on_chunk = None

        if not self._frames:
            return b""

        return self._frames_to_wav(self._frames)

    def _callback(self, indata, frames, time, status):
        if not self._recording:
            return
        frame = indata.copy()
        self._frames.append(frame)

        # Chunked streaming — send raw PCM bytes (no WAV header)
        if self._on_chunk and self._chunk_size > 0:
            self._chunk_frames.append(frame)
            total_samples = sum(f.shape[0] for f in self._chunk_frames)
            if total_samples >= self._chunk_size:
                pcm = np.concatenate(self._chunk_frames, axis=0).tobytes()
                self._chunk_frames = []
                self._on_chunk(pcm)

    def _frames_to_wav(self, frames: list[np.ndarray]) -> bytes:
        audio_data = np.concatenate(frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())
        return buf.getvalue()

    def record_for(self, seconds: float) -> bytes:
        """Record for a fixed duration and return audio bytes."""
        self.start()
        threading.Event().wait(seconds)
        return self.stop()

    @property
    def is_recording(self) -> bool:
        return self._recording
