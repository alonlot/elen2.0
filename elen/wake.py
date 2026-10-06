"""Wake word: say "Hey Ellen" and Elen starts listening, no shortcut needed.

Engines:
  vosk          (default) offline speech recognition limited to your wake phrases.
                Any phrase works without training. Needs a Vosk model folder:
                `elen download-wake-model` gets the small English one (40 MB).
  openwakeword  small neural wake-word models (.onnx), for example "hey_jarvis",
                or one you trained for "hey elen". Install: see docs.

The microphone is read continuously but only on this computer. Nothing is sent
anywhere until the wake phrase is heard.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import zipfile
from pathlib import Path
from typing import Any, Awaitable, Callable, Protocol

log = logging.getLogger("elen.wake")

RATE = 16000
CHUNK_BYTES = 3200  # 100 ms of 16 kHz mono S16LE
VOSK_SMALL_EN = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEFAULT_PHRASES = ["hey ellen", "hi ellen", "okay ellen"]


class WakeEngine(Protocol):
    def process(self, chunk: bytes) -> bool: ...
    def reset(self) -> None: ...


class VoskEngine:
    """Keyword spotting with a grammar: the recognizer can only hear the phrases or [unk]."""

    def __init__(self, cfg: dict[str, Any], default_model_dir: Path):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        path = Path(cfg.get("model") or default_model_dir).expanduser()
        if not path.exists():
            raise RuntimeError(f"Vosk model not found at {path}. Run: elen download-wake-model")
        self.model = Model(str(path))
        phrases = [p.lower().strip() for p in (cfg.get("phrases") or DEFAULT_PHRASES) if p.strip()]
        known = []
        for phrase in phrases:
            missing = [w for w in phrase.split() if self.model.vosk_model_find_word(w) < 0]
            if missing:
                log.warning("wake phrase '%s' skipped: the model does not know %s", phrase, missing)
            else:
                known.append(phrase)
        if not known:
            raise RuntimeError("None of the wake phrases use words the Vosk model knows.")
        self.phrases = known
        self.min_conf = float(cfg.get("threshold", 0.5))
        # fast = react on partial results: quicker, but more false triggers. Off by default.
        self.use_partial = bool(cfg.get("fast", False))
        self._make = lambda: KaldiRecognizer(self.model, RATE, json.dumps(known + ["[unk]"]))
        self.rec = self._make()
        self.rec.SetWords(True)
        self._audio = bytearray()  # audio of the current utterance
        self.tail = b""  # audio said right after the wake phrase, in the same breath

    def _first_phrase(self, words: list[dict]) -> list[dict] | None:
        """The words of the first wake phrase in the result, if they are confident enough."""
        tokens = [w.get("word") for w in words]
        best = None
        for phrase in self.phrases:
            parts = phrase.split()
            for i in range(len(tokens) - len(parts) + 1):
                if tokens[i : i + len(parts)] == parts:
                    if best is None or i < best[0]:
                        best = (i, words[i : i + len(parts)])
                    break
        if best is None or min(w.get("conf", 1.0) for w in best[1]) < self.min_conf:
            return None
        return best[1]

    def _hit(self, text: str) -> bool:
        return any(p in text for p in self.phrases)

    def process(self, chunk: bytes) -> bool:
        self._audio.extend(chunk)
        if len(self._audio) > RATE * 2 * 30:  # keep at most 30 s
            del self._audio[: len(self._audio) - RATE * 2 * 30]
        if self.rec.AcceptWaveform(chunk):
            result = json.loads(self.rec.Result())
            audio, self._audio = bytes(self._audio), bytearray()
            if not self._hit(result.get("text", "")):
                return False
            words = result.get("result") or []
            match = self._first_phrase(words)
            if match is None:
                return False
            # "Hey Ellen, open my calendar" in one breath: keep the audio after the phrase.
            end = match[-1].get("end", 0)
            self.tail = audio[int(end * RATE) * 2:] if words[-1].get("end", 0) - end > 0.3 else b""
            return True
        if self.use_partial:
            # Faster: react while the user is still speaking. A little less strict.
            return self._hit(json.loads(self.rec.PartialResult()).get("partial", ""))
        return False

    def reset(self) -> None:
        self.rec = self._make()
        self.rec.SetWords(True)
        self._audio = bytearray()
        self.tail = b""


class OpenWakeWordEngine:
    tail = b""

    def __init__(self, cfg: dict[str, Any]):
        import numpy as np
        from openwakeword.model import Model

        self.np = np
        models = cfg.get("model") or ["hey_jarvis"]
        models = [models] if isinstance(models, str) else list(models)
        try:
            self.model = Model(wakeword_models=models, inference_framework="onnx")
        except TypeError:  # openwakeword < 0.5
            self.model = Model(wakeword_model_paths=models)
        self.threshold = float(cfg.get("threshold", 0.5))
        self._buf = b""

    def process(self, chunk: bytes) -> bool:
        self._buf += chunk
        hit = False
        while len(self._buf) >= 2560:  # 80 ms frames
            frame, self._buf = self._buf[:2560], self._buf[2560:]
            scores = self.model.predict(self.np.frombuffer(frame, dtype=self.np.int16))
            hit = hit or any(v >= self.threshold for v in scores.values())
        return hit

    def reset(self) -> None:
        self._buf = b""
        if hasattr(self.model, "reset"):
            self.model.reset()


def make_engine(cfg: dict[str, Any], data_dir: Path) -> WakeEngine:
    engine = (cfg.get("engine") or "vosk").lower()
    if engine == "vosk":
        return VoskEngine(cfg, data_dir / "vosk-model-small-en-us-0.15")
    if engine == "openwakeword":
        return OpenWakeWordEngine(cfg)
    raise RuntimeError(f"Unknown wake engine '{engine}'. Use 'vosk' or 'openwakeword'.")


class WakeListener:
    """Reads the microphone and calls on_wake when the phrase is heard.

    suspend() frees the microphone (for example while Elen records your request);
    resume() starts listening again.
    """

    def __init__(
        self,
        engine: WakeEngine,
        record_command: list[str],
        on_wake: Callable[[bytes], Awaitable[None]],
        cooldown: float = 1.5,
    ):
        self.engine = engine
        self.record_command = record_command
        self.on_wake = on_wake
        self.cooldown = cooldown
        self.enabled = True
        self._resume = asyncio.Event()
        self._resume.set()
        self._task: asyncio.Task | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._last = 0.0

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._kill()

    def suspend(self) -> None:
        self._resume.clear()
        self._kill()

    def resume(self) -> None:
        self.engine.reset()
        self._resume.set()

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = enabled
        if enabled:
            self.resume()
        else:
            self.suspend()

    def _kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.kill()
        self._proc = None

    async def _chunks(self):
        """Yield 100 ms audio chunks from the record command."""
        self._proc = await asyncio.create_subprocess_exec(
            *self.record_command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL
        )
        proc = self._proc
        while True:
            chunk = await proc.stdout.read(CHUNK_BYTES)
            if not chunk:
                return
            yield chunk

    async def _run(self) -> None:
        while True:
            await self._resume.wait()
            if not self.enabled:
                self._resume.clear()
                continue
            try:
                async for chunk in self._chunks():
                    if not self._resume.is_set():
                        break
                    if await asyncio.to_thread(self.engine.process, chunk):
                        now = time.monotonic()
                        if now - self._last < self.cooldown:
                            continue
                        self._last = now
                        self.suspend()
                        try:
                            await self.on_wake(getattr(self.engine, "tail", b""))
                        except Exception:  # noqa: BLE001
                            log.exception("wake handler failed")
                        if self.enabled:
                            self.resume()
                        break
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("wake listener failed; retrying in 5 s")
                await asyncio.sleep(5)
            finally:
                self._kill()
            if self._resume.is_set():
                await asyncio.sleep(0.2)  # the recorder ended by itself: restart it


def download_vosk_model(data_dir: Path, url: str = VOSK_SMALL_EN) -> Path:
    import urllib.request

    data_dir.mkdir(parents=True, exist_ok=True)
    zpath = data_dir / Path(url).name
    print(f"Downloading {url} …")
    urllib.request.urlretrieve(url, zpath)
    with zipfile.ZipFile(zpath) as z:
        z.extractall(data_dir)
    zpath.unlink()
    target = data_dir / Path(url).stem
    print(f"Wake-word model ready: {target}")
    return target
