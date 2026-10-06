"""Speech to text.

Recording uses a command that writes raw 16 kHz mono S16LE audio to stdout
(arecord, parecord or pw-record; auto-detected). Recording stops when the user
stops it, after a pause in speech, or at max_seconds.

Transcription providers:
  openai          any OpenAI-compatible /audio/transcriptions API
                  (OpenAI, Groq, local faster-whisper-server, speaches)
  faster_whisper  local, offline (pip install faster-whisper)
  command         any command; "{wav}" is replaced by the file path, stdout is the text
"""

from __future__ import annotations

import array
import asyncio
import math
import shutil
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import httpx

from .secrets import resolve_secret

RATE = 16000
CANDIDATES = [
    ["arecord", "-q", "-f", "S16_LE", "-r", str(RATE), "-c", "1", "-t", "raw"],
    ["parecord", "--raw", f"--rate={RATE}", "--channels=1", "--format=s16le"],
    ["pw-record", "--rate", str(RATE), "--channels", "1", "--format", "s16", "-"],
]


def write_wav(path: Path, pcm: bytes) -> Path:
    """Save raw 16 kHz mono S16LE audio as a WAV file."""
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return path


def has_speech(pcm: bytes, threshold: float) -> bool:
    return any(rms(pcm[i : i + 3200]) > threshold for i in range(0, len(pcm), 3200))


def rms(chunk: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(chunk[: len(chunk) - len(chunk) % 2])
    if not samples:
        return 0.0
    return math.sqrt(sum(s * s for s in samples) / len(samples))


class Recorder:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._stop = asyncio.Event()
        self.active = False

    def command(self) -> list[str]:
        if self.cfg.get("record_command"):
            return list(self.cfg["record_command"])
        for cmd in CANDIDATES:
            if shutil.which(cmd[0]):
                return cmd
        raise RuntimeError("No audio recorder found. Install alsa-utils or pulseaudio-utils.")

    def stop(self) -> None:
        self._stop.set()

    async def record(self, out: Path) -> Path:
        self._stop = asyncio.Event()
        self.active = True
        proc = await asyncio.create_subprocess_exec(
            *self.command(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        frames = bytearray()
        threshold = float(self.cfg.get("silence_threshold", 600))
        silence_needed = float(self.cfg.get("silence_seconds", 1.5))
        max_seconds = float(self.cfg.get("max_seconds", 30))
        heard_speech = False
        last_voice = time.monotonic()
        start = time.monotonic()
        try:
            while not self._stop.is_set():
                try:
                    chunk = await asyncio.wait_for(proc.stdout.read(3200), timeout=0.5)
                except asyncio.TimeoutError:
                    continue
                if not chunk:
                    break
                frames.extend(chunk)
                now = time.monotonic()
                if rms(chunk) > threshold:
                    heard_speech = True
                    last_voice = now
                if heard_speech and now - last_voice > silence_needed:
                    break
                if now - start > max_seconds:
                    break
                if not heard_speech and now - start > 8:
                    break  # nothing said
        finally:
            self.active = False
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), 2)
                except asyncio.TimeoutError:
                    proc.kill()
        write_wav(out, bytes(frames))
        if not heard_speech:
            raise RuntimeError("No speech heard.")
        return out


class Transcriber:
    def __init__(self, cfg: dict[str, Any]):
        self.cfg = cfg
        self._local_model = None

    async def transcribe(self, wav: Path) -> str:
        kind = (self.cfg.get("provider") or "openai").lower()
        if kind == "none":
            raise RuntimeError("Speech to text is off ([stt] provider = \"none\").")
        if kind == "faster_whisper":
            return await asyncio.to_thread(self._faster_whisper, wav)
        if kind == "command":
            return await self._command(wav)
        return await self._openai(wav)

    async def _openai(self, wav: Path) -> str:
        base = (self.cfg.get("base_url") or "https://api.openai.com/v1").rstrip("/")
        key = resolve_secret(self.cfg.get("api_key"))
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        data = {"model": self.cfg.get("model") or "whisper-1"}
        if self.cfg.get("language"):
            data["language"] = self.cfg["language"]
        async with httpx.AsyncClient(timeout=120) as client:
            with open(wav, "rb") as f:
                r = await client.post(
                    f"{base}/audio/transcriptions",
                    headers=headers,
                    data=data,
                    files={"file": ("speech.wav", f, "audio/wav")},
                )
        if r.status_code >= 400:
            raise RuntimeError(f"Speech API error {r.status_code}: {r.text[:300]}")
        return (r.json().get("text") or "").strip()

    def _faster_whisper(self, wav: Path) -> str:
        if self._local_model is None:
            from faster_whisper import WhisperModel  # type: ignore

            self._local_model = WhisperModel(
                self.cfg.get("model") or "small", device="auto", compute_type="auto"
            )
        segments, _ = self._local_model.transcribe(
            str(wav), language=self.cfg.get("language") or None, vad_filter=True
        )
        return " ".join(s.text.strip() for s in segments).strip()

    async def _command(self, wav: Path) -> str:
        cmd = [p.replace("{wav}", str(wav)) for p in self.cfg.get("command") or []]
        if not cmd:
            raise RuntimeError("[stt] command is empty.")
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        out, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(f"STT command failed: {err.decode()[:300]}")
        return out.decode().strip()


class Listener:
    """Record, then transcribe."""

    def __init__(self, cfg: dict[str, Any]):
        self.recorder = Recorder(cfg)
        self.transcriber = Transcriber(cfg)

    async def listen(self) -> str:
        with tempfile.TemporaryDirectory(prefix="elen-") as tmp:
            wav = await self.recorder.record(Path(tmp) / "speech.wav")
            return await self.transcriber.transcribe(wav)
