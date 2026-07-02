import collections
import io
import logging
import sys
import threading
import wave
import sounddevice as sd
import numpy as np
import soundfile as sf

logger = logging.getLogger(__name__)

# Find WASAPI input device for shared-mode mic access on Windows.
# WASAPI shared mode allows Parley to use the mic simultaneously
# with other apps (Teams, Discord, browsers etc.)
# On Linux/macOS this is skipped — PulseAudio/PipeWire/CoreAudio share by default.
_wasapi_input_device = None
_wasapi_settings = None
if sys.platform == "win32":
    try:
        for i, api in enumerate(sd.query_hostapis()):
            if "WASAPI" in api["name"]:
                _wasapi_input_device = api["default_input_device"]
                _wasapi_settings = sd.WasapiSettings(exclusive=False)
                dev_name = sd.query_devices(_wasapi_input_device)["name"]
                logger.info(f"WASAPI shared mode: device {_wasapi_input_device} ({dev_name})")
                break
    except Exception as e:
        logger.warning(f"Could not set up WASAPI shared mode: {e}")


class AudioRecorder:
    """Records audio from the default microphone."""

    def __init__(self, sample_rate: int = 16000, channels: int = 1):
        self.sample_rate = sample_rate
        self.channels = channels
        self._frames: collections.deque[np.ndarray] = collections.deque()
        self._stream = None
        self._recording = False
        self._lock = threading.Lock()

    def start(self):
        """Start recording."""
        with self._lock:
            self._frames = collections.deque()
            self._recording = True

            # Try APIs in order: WASAPI shared (best) → DirectSound → MME (fallback)
            attempts = []
            if _wasapi_input_device is not None:
                attempts.append(("WASAPI shared", {
                    "device": _wasapi_input_device,
                    "extra_settings": _wasapi_settings,
                }))

            # Fallback: default device without special settings (usually MME)
            attempts.append(("Default", {}))

            for api_name, extra_kwargs in attempts:
                try:
                    self._stream = sd.InputStream(
                        samplerate=self.sample_rate,
                        channels=self.channels,
                        dtype="int16",
                        callback=self._callback,
                        **extra_kwargs,
                    )
                    self._stream.start()
                    logger.info(f"Mic opened via {api_name}")
                    return
                except sd.PortAudioError as e:
                    logger.warning(f"Mic via {api_name} failed: {e}")

            logger.error("Could not access microphone on any API")
            self._recording = False

    def stop_frames(self) -> list:
        """Stop the mic stream and hand back the captured frames.

        Fast (no encoding) — safe to call on the hotkey thread. Encode the
        frames afterwards on a worker thread via encode_opus().
        """
        with self._lock:
            self._recording = False
            if self._stream:
                self._stream.stop()
                self._stream.close()
                self._stream = None
            frames = list(self._frames)
            self._frames.clear()  # don't keep the last recording resident
        return frames

    def encode_opus(self, frames: list) -> bytes:
        """Encode captured frames to Ogg/Opus bytes.

        Opus VBR @ 24 kbps shrinks a 10s recording from ~320 KB (WAV) to ~30 KB
        — roughly a 10× reduction. The server (server/main.py) accepts Ogg/Opus
        directly via ffmpeg/Whisper, no preprocessing needed.

        Returns empty bytes if no audio was captured.
        """
        if not frames:
            return b""
        return self._frames_to_opus(frames)

    def stop(self) -> bytes:
        """Stop recording and return audio as Ogg/Opus bytes (blocking encode)."""
        return self.encode_opus(self.stop_frames())

    def _callback(self, indata, frames, time, status):
        if not self._recording:
            return
        self._frames.append(indata.copy())

    def _frames_to_wav(self, frames: list[np.ndarray]) -> bytes:
        audio_data = np.concatenate(frames, axis=0)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(self.channels)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(audio_data.tobytes())
        return buf.getvalue()

    def _frames_to_opus(self, frames: list[np.ndarray]) -> bytes:
        """Encode int16 PCM frames to Ogg/Opus via libsndfile.

        Falls back to WAV if libsndfile is too old to support Opus
        (libsndfile < 1.0.30) — server still accepts WAV, the user just
        misses the bandwidth win.
        """
        audio_data = np.concatenate(frames, axis=0)
        try:
            buf = io.BytesIO()
            sf.write(
                buf,
                audio_data,
                self.sample_rate,
                format="OGG",
                subtype="OPUS",
            )
            data = buf.getvalue()
            duration_s = len(audio_data) / self.sample_rate
            wav_size = len(audio_data) * 2  # int16 = 2 bytes/sample
            ratio = wav_size / max(len(data), 1)
            logger.info(
                f"Opus encode: {len(data)} bytes for {duration_s:.1f}s "
                f"(WAV would be ~{wav_size} bytes, {ratio:.1f}× smaller)"
            )
            return data
        except Exception as e:
            logger.warning(f"Opus encoding failed ({e}); falling back to WAV")
            return self._frames_to_wav(frames)

    @property
    def is_recording(self) -> bool:
        return self._recording
