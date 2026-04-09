import asyncio
import contextlib
import time
import wave
from dataclasses import dataclass
from typing import AsyncIterator, Optional, Callable


@dataclass(frozen=True)
class AudioFormat:
    sample_rate: int = 16000
    channels: int = 1
    sample_width_bytes: int = 2  # 16-bit PCM (s16le)

    @property
    def bits_per_sample(self) -> int:
        return self.sample_width_bytes * 8


@dataclass(frozen=True)
class AudioChunk:
    pcm: bytes
    format: AudioFormat
    # monotonic seconds when this chunk was produced (useful for latency)
    t_monotonic: float

    @property
    def duration_ms(self) -> float:
        bytes_per_frame = self.format.channels * self.format.sample_width_bytes
        if bytes_per_frame <= 0:
            return 0.0
        frames = len(self.pcm) / bytes_per_frame
        return 1000.0 * frames / self.format.sample_rate


class WavSink:
    """
    Minimal WAV writer for captured PCM chunks (16-bit mono @ 16k by default).
    """

    def __init__(self, path: str, fmt: AudioFormat):
        self._path = path
        self._fmt = fmt
        self._wf: Optional[wave.Wave_write] = None

    def open(self) -> None:
        wf = wave.open(self._path, "wb")
        wf.setnchannels(self._fmt.channels)
        wf.setsampwidth(self._fmt.sample_width_bytes)
        wf.setframerate(self._fmt.sample_rate)
        self._wf = wf

    def write(self, pcm: bytes) -> None:
        if self._wf is None:
            raise RuntimeError("WavSink not opened")
        self._wf.writeframes(pcm)

    def close(self) -> None:
        if self._wf is not None:
            self._wf.close()
            self._wf = None

    def __enter__(self) -> "WavSink":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


class RealtimeAudioSampler:
    """
    Reusable real-time microphone sampler.

    Produces fixed-duration PCM chunks (default 200ms) suitable for WebSocket streaming ASR.

    Default format matches Volcengine SAUC demo recommendations:
    - 16kHz sample rate
    - mono
    - 16-bit signed little-endian PCM (s16le)
    - chunk duration: 100~200ms (200ms recommended for bi-directional streaming)
    """

    def __init__(
        self,
        *,
        fmt: AudioFormat = AudioFormat(),
        chunk_duration_ms: int = 200,
        device: Optional[int | str] = None,
        queue_max_chunks: int = 50,
        on_overflow: Optional[Callable[[], None]] = None,
    ):
        if chunk_duration_ms <= 0:
            raise ValueError("chunk_duration_ms must be > 0")
        self.fmt = fmt
        self.chunk_duration_ms = chunk_duration_ms
        self.device = device
        self.queue_max_chunks = queue_max_chunks
        self.on_overflow = on_overflow

        self._queue: asyncio.Queue[AudioChunk] = asyncio.Queue(maxsize=queue_max_chunks)
        self._stream = None
        self._running = False

        # derived
        self._frames_per_chunk = int(self.fmt.sample_rate * self.chunk_duration_ms / 1000)
        if self._frames_per_chunk <= 0:
            raise ValueError("chunk_duration_ms too small for given sample_rate")

    async def __aenter__(self) -> "RealtimeAudioSampler":
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.stop()

    async def start(self) -> None:
        if self._running:
            return

        # Import lazily so the rest of the project can run without audio deps.
        import sounddevice as sd  # type: ignore
        import numpy as np  # type: ignore

        loop = asyncio.get_running_loop()
        self._running = True

        def callback(indata, frames, time_info, status):  # runs in sounddevice thread
            if status:
                # status can include over/underflow warnings
                pass

            if not self._running:
                return

            # indata: float32 by default unless dtype specified; we request int16.
            # Ensure contiguous bytes for PCM.
            try:
                pcm = np.asarray(indata).tobytes()
            except Exception:
                pcm = bytes(indata)

            chunk = AudioChunk(pcm=pcm, format=self.fmt, t_monotonic=time.monotonic())
            try:
                loop.call_soon_threadsafe(self._queue.put_nowait, chunk)
            except asyncio.QueueFull:
                if self.on_overflow is not None:
                    try:
                        loop.call_soon_threadsafe(self.on_overflow)
                    except Exception:
                        pass

        # Build an InputStream that already chunks by blocksize (frames_per_chunk)
        self._stream = sd.InputStream(
            samplerate=self.fmt.sample_rate,
            channels=self.fmt.channels,
            dtype="int16",
            blocksize=self._frames_per_chunk,
            device=self.device,
            callback=callback,
        )
        self._stream.start()

    async def stop(self) -> None:
        if not self._running:
            return
        self._running = False

        with contextlib.suppress(Exception):
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
        self._stream = None

        # Drain queue so awaiting consumers can exit if they want
        # (leave it as-is; caller can simply stop iteration)

    async def chunks(self) -> AsyncIterator[AudioChunk]:
        """
        Async iterator of AudioChunk until stop() is called.
        """
        while self._running:
            chunk = await self._queue.get()
            yield chunk


async def record_wav(
    *,
    out_path: str,
    seconds: float,
    fmt: AudioFormat = AudioFormat(),
    chunk_duration_ms: int = 200,
    device: Optional[int | str] = None,
) -> None:
    """
    Convenience utility to record microphone audio into a WAV file using the sampler.
    """
    deadline = time.monotonic() + seconds
    sampler = RealtimeAudioSampler(fmt=fmt, chunk_duration_ms=chunk_duration_ms, device=device)
    async with sampler:
        with WavSink(out_path, fmt) as sink:
            async for chunk in sampler.chunks():
                sink.write(chunk.pcm)
                if time.monotonic() >= deadline:
                    break

