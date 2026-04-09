import argparse
import asyncio
import json
import time
import contextlib
from typing import Optional

from realtime_audio import AudioFormat, RealtimeAudioSampler, WavSink

# Reuse the Volcengine SAUC protocol implementation from existing demo
from sauc_websocket_demo import RequestBuilder, ResponseParser  # type: ignore

import aiohttp


class RealtimeAsrStream:
    """
    Capture microphone audio in realtime and stream to SAUC ASR websocket.

    Buffer strategy:
    - Primary: in-memory queue inside RealtimeAudioSampler (low-latency, avoids disk jitter)
    - Optional: write a "buffer wav" copy of what was streamed for debugging/replay
    """

    def __init__(
        self,
        *,
        url: str,
        resource_id: str,
        chunk_ms: int = 200,
        fmt: AudioFormat = AudioFormat(),
        device: Optional[int | str] = None,
        buffer_wav_path: str = "",
    ):
        self.url = url
        self.resource_id = resource_id
        self.chunk_ms = chunk_ms
        self.fmt = fmt
        self.device = device
        self.buffer_wav_path = buffer_wav_path

        self._seq = 1
        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None

    async def __aenter__(self) -> "RealtimeAsrStream":
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    async def connect(self) -> None:
        assert self._session is not None
        headers = RequestBuilder.new_auth_headers(self.resource_id)
        self._ws = await self._session.ws_connect(self.url, headers=headers)

        # Print handshake headers if available (useful for support / debugging)
        try:
            resp_headers = getattr(self._ws, "headers", {}) or {}
            logid = resp_headers.get("X-Tt-Logid") or resp_headers.get("x-tt-logid")
            connect_id = resp_headers.get("X-Api-Connect-Id") or resp_headers.get("x-api-connect-id")
            if connect_id:
                print(f"[handshake] X-Api-Connect-Id: {connect_id}")
            if logid:
                print(f"[handshake] X-Tt-Logid: {logid}")
        except Exception:
            pass

    async def send_full_request(self) -> None:
        assert self._ws is not None

        enable_nonstream = self.url.rstrip("/").endswith("bigmodel_nostream")
        # Realtime mic capture produces raw PCM (int16 little-endian), not a WAV container.
        # So we must declare a non-WAV audio format here (方案 A).
        req = RequestBuilder.new_full_client_request(
            self._seq,
            enable_nonstream=enable_nonstream,
            audio_format="pcm",
            audio_codec="raw",
        )
        self._seq += 1
        await self._ws.send_bytes(req)

        # Expect an initial response
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            resp = ResponseParser.parse_response(msg.data)
            print("[init]", json.dumps(resp.to_dict(), ensure_ascii=False))
        else:
            print(f"[init] unexpected message type: {msg.type}")

    async def _recv_until_last(self) -> None:
        assert self._ws is not None
        async for msg in self._ws:
            if msg.type == aiohttp.WSMsgType.BINARY:
                resp = ResponseParser.parse_response(msg.data)
                payload = resp.to_dict()
                # Print only meaningful result changes for async mode
                print(json.dumps(payload, ensure_ascii=False))
                if resp.is_last_package or resp.code != 0:
                    break
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

    async def run(self, *, seconds: float) -> None:
        assert self._ws is not None

        sampler = RealtimeAudioSampler(
            fmt=self.fmt,
            chunk_duration_ms=self.chunk_ms,
            device=self.device,
        )

        wav_sink = None
        if self.buffer_wav_path:
            wav_sink = WavSink(self.buffer_wav_path, self.fmt)
            wav_sink.open()

        deadline = time.monotonic() + seconds

        recv_task = asyncio.create_task(self._recv_until_last())

        try:
            async with sampler:
                async for chunk in sampler.chunks():
                    # Stop condition
                    if time.monotonic() >= deadline:
                        break

                    if wav_sink is not None:
                        wav_sink.write(chunk.pcm)

                    # Send audio chunk
                    req = RequestBuilder.new_audio_only_request(self._seq, chunk.pcm, is_last=False)
                    await self._ws.send_bytes(req)
                    self._seq += 1

            # Send last packet (negative seq)
            last_req = RequestBuilder.new_audio_only_request(self._seq, b"", is_last=True)
            await self._ws.send_bytes(last_req)

            # Wait server final response
            await recv_task
        finally:
            if wav_sink is not None:
                wav_sink.close()
            if not recv_task.done():
                recv_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await recv_task


def _env_default(key: str, fallback: str) -> str:
    import os

    v = os.getenv(key, "").strip()
    return v or fallback


async def main() -> int:
    parser = argparse.ArgumentParser(description="Realtime microphone -> SAUC websocket streaming demo")
    parser.add_argument("--url", type=str, default=_env_default("VOLC_ASR_WS_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"))
    parser.add_argument("--resource-id", type=str, default=_env_default("VOLC_ASR_RESOURCE_ID", "volc.bigasr.sauc.duration"))
    parser.add_argument("--seconds", type=float, default=5.0, help="How long to capture from mic")
    parser.add_argument("--chunk-ms", type=int, default=int(_env_default("VOLC_ASR_SEG_DURATION_MS", "200")))
    parser.add_argument("--device", type=str, default="", help="sounddevice input device (index or substring)")
    parser.add_argument("--buffer-wav", type=str, default="", help="Optional: write streamed audio to a WAV file")
    args = parser.parse_args()

    device = None
    if args.device.strip():
        try:
            device = int(args.device)
        except ValueError:
            device = args.device

    fmt = AudioFormat(sample_rate=16000, channels=1, sample_width_bytes=2)

    async with RealtimeAsrStream(
        url=args.url,
        resource_id=args.resource_id,
        chunk_ms=args.chunk_ms,
        fmt=fmt,
        device=device,
        buffer_wav_path=args.buffer_wav,
    ) as rt:
        await rt.connect()
        await rt.send_full_request()
        await rt.run(seconds=args.seconds)

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

