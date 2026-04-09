import argparse
import asyncio
import sys

from realtime_audio import AudioFormat, RealtimeAudioSampler, record_wav


async def main() -> int:
    parser = argparse.ArgumentParser(description="Realtime microphone sampler (PCM chunks / WAV record)")
    parser.add_argument("--out-wav", type=str, default="", help="Write captured audio to WAV file")
    parser.add_argument("--seconds", type=float, default=5.0, help="Record duration when --out-wav is set")
    parser.add_argument("--chunk-ms", type=int, default=200, help="Chunk duration ms (100~200 recommended)")
    parser.add_argument("--sample-rate", type=int, default=16000, help="Sample rate (Hz), default 16000")
    parser.add_argument("--device", type=str, default="", help="sounddevice input device (index or substring)")
    args = parser.parse_args()

    device = None
    if args.device.strip():
        # sounddevice allows int index or device name; we pass through as string
        # (if it looks like an int, convert for convenience)
        try:
            device = int(args.device)
        except ValueError:
            device = args.device

    fmt = AudioFormat(sample_rate=args.sample_rate, channels=1, sample_width_bytes=2)

    if args.out_wav:
        await record_wav(
            out_path=args.out_wav,
            seconds=args.seconds,
            fmt=fmt,
            chunk_duration_ms=args.chunk_ms,
            device=device,
        )
        print(f"Wrote {args.out_wav}")
        return 0

    # Otherwise: just print chunk info (useful for verifying realtime capture works)
    async with RealtimeAudioSampler(fmt=fmt, chunk_duration_ms=args.chunk_ms, device=device) as sampler:
        i = 0
        async for chunk in sampler.chunks():
            i += 1
            print(f"chunk {i}: {len(chunk.pcm)} bytes, {chunk.duration_ms:.1f} ms")
            if i >= 20:
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

