import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Optional

from aiohttp import web

from realtime_audio import AudioFormat, RealtimeAudioSampler
from sauc_websocket_demo import RequestBuilder, ResponseParser  # loads .env via python-dotenv in that module

import aiohttp


def _env(key: str, default: str = "") -> str:
    v = os.getenv(key, "").strip()
    return v if v else default


@dataclass
class DaemonConfig:
    host: str = "127.0.0.1"
    port: int = 8765
    url: str = _env("VOLC_ASR_WS_URL", "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async")
    resource_id: str = _env("VOLC_ASR_RESOURCE_ID", "volc.bigasr.sauc.duration")
    chunk_ms: int = int(_env("VOLC_ASR_SEG_DURATION_MS", "200"))


class AsrSession:
    """
    One recording session: mic capture -> websocket -> wait final result.
    Phase 1 behavior: only return finalText once on stop.
    """

    def __init__(self, *, url: str, resource_id: str, chunk_ms: int):
        self.url = url
        self.resource_id = resource_id
        self.chunk_ms = chunk_ms

        self.fmt = AudioFormat(sample_rate=16000, channels=1, sample_width_bytes=2)

        self._seq = 1
        self._stop_event = asyncio.Event()
        self._final_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        self._session: Optional[aiohttp.ClientSession] = None
        self._ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._sender_task: Optional[asyncio.Task] = None
        self._recv_task: Optional[asyncio.Task] = None

    @property
    def final_future(self) -> asyncio.Future[str]:
        return self._final_future

    async def start(self) -> None:
        self._session = aiohttp.ClientSession()
        headers = RequestBuilder.new_auth_headers(self.resource_id)
        self._ws = await self._session.ws_connect(self.url, headers=headers)

        enable_nonstream = self.url.rstrip("/").endswith("bigmodel_nostream")
        full_req = RequestBuilder.new_full_client_request(
            self._seq,
            enable_nonstream=enable_nonstream,
            # Realtime mic capture sends raw PCM int16, not WAV container.
            audio_format="pcm",
            audio_codec="raw",
        )
        self._seq += 1
        await self._ws.send_bytes(full_req)

        # Consume initial response (not used, but keeps protocol aligned)
        msg = await self._ws.receive()
        if msg.type == aiohttp.WSMsgType.BINARY:
            _ = ResponseParser.parse_response(msg.data)

        self._recv_task = asyncio.create_task(self._recv_loop())
        self._sender_task = asyncio.create_task(self._send_loop())

    async def stop_and_wait_final(self, timeout_s: float = 8.0) -> str:
        self._stop_event.set()

        # wait final (recv loop will resolve it)
        try:
            return await asyncio.wait_for(self._final_future, timeout=timeout_s)
        finally:
            await self.close()

    async def close(self) -> None:
        for t in (self._sender_task, self._recv_task):
            if t and not t.done():
                t.cancel()
        for t in (self._sender_task, self._recv_task):
            if t:
                try:
                    await t
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass

        if self._ws and not self._ws.closed:
            await self._ws.close()
        if self._session and not self._session.closed:
            await self._session.close()

    async def _send_loop(self) -> None:
        assert self._ws is not None

        sampler = RealtimeAudioSampler(fmt=self.fmt, chunk_duration_ms=self.chunk_ms)
        async with sampler:
            async for chunk in sampler.chunks():
                if self._stop_event.is_set():
                    break
                req = RequestBuilder.new_audio_only_request(self._seq, chunk.pcm, is_last=False)
                await self._ws.send_bytes(req)
                self._seq += 1

        # Send last packet
        last_req = RequestBuilder.new_audio_only_request(self._seq, b"", is_last=True)
        await self._ws.send_bytes(last_req)

    async def _recv_loop(self) -> None:
        assert self._ws is not None

        async for msg in self._ws:
            if msg.type != aiohttp.WSMsgType.BINARY:
                if msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSED):
                    break
                continue

            resp = ResponseParser.parse_response(msg.data)

            # Error from server
            if resp.code != 0:
                if not self._final_future.done():
                    self._final_future.set_result("")
                break

            payload = resp.payload_msg or {}
            result = payload.get("result") or {}
            text = result.get("text") or ""

            if resp.is_last_package:
                if not self._final_future.done():
                    self._final_future.set_result(text)
                break

        if not self._final_future.done():
            self._final_future.set_result("")


class AsrDaemon:
    def __init__(self, cfg: DaemonConfig):
        self.cfg = cfg
        self._lock = asyncio.Lock()
        self._session: Optional[AsrSession] = None
        self._state: str = "idle"  # idle|recording|stopping
        self._last_final: str = ""
        self._started_at: float = 0.0

    async def start(self) -> dict:
        async with self._lock:
            if self._state == "recording":
                return {"ok": True, "state": self._state}

            self._session = AsrSession(url=self.cfg.url, resource_id=self.cfg.resource_id, chunk_ms=self.cfg.chunk_ms)
            self._state = "recording"
            self._started_at = time.time()
            await self._session.start()
            return {"ok": True, "state": self._state}

    async def stop(self) -> dict:
        async with self._lock:
            if self._state != "recording" or self._session is None:
                return {"ok": True, "state": "idle", "finalText": ""}
            self._state = "stopping"
            sess = self._session

        # wait final outside lock
        final = await sess.stop_and_wait_final(timeout_s=12.0)

        async with self._lock:
            self._last_final = final
            self._session = None
            self._state = "idle"
            return {"ok": True, "state": self._state, "finalText": final}

    async def status(self) -> dict:
        async with self._lock:
            return {
                "ok": True,
                "state": self._state,
                "url": self.cfg.url,
                "resourceId": self.cfg.resource_id,
                "chunkMs": self.cfg.chunk_ms,
                "lastFinal": self._last_final,
                "startedAt": self._started_at,
            }


def create_app(daemon: AsrDaemon) -> web.Application:
    app = web.Application()

    async def health(_req: web.Request) -> web.Response:
        return web.json_response({"ok": True})

    async def status(_req: web.Request) -> web.Response:
        return web.json_response(await daemon.status())

    async def start(_req: web.Request) -> web.Response:
        return web.json_response(await daemon.start())

    async def stop(_req: web.Request) -> web.Response:
        return web.json_response(await daemon.stop())

    app.add_routes(
        [
            web.get("/health", health),
            web.get("/status", status),
            web.post("/start", start),
            web.post("/stop", stop),
        ]
    )
    return app


def main() -> None:
    cfg = DaemonConfig()
    daemon = AsrDaemon(cfg)
    app = create_app(daemon)
    web.run_app(app, host=cfg.host, port=cfg.port)


if __name__ == "__main__":
    main()

