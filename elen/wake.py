"""Wake word: say "Hey Ellen" and Elen starts listening, no shortcut needed.

Engines:
  vosk          (default) offline speech recognition limited to a small grammar: the
                name, names that sound like it, and decoy words. A fuzzy match by
                sound then decides (wakematch.py). Needs a Vosk model folder:
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

from .wakematch import DECOYS, GREETINGS, SOUND_ALIKES, WakeMatcher

log = logging.getLogger("elen.wake")

RATE = 16000
CHUNK_BYTES = 3200  # 100 ms of 16 kHz mono S16LE
VOSK_SMALL_EN = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
DEFAULT_PHRASES = ["hey ellen", "hi ellen", "okay ellen"]
END_PAUSE = 0.15  # seconds of silence before a name at the end of a sentence


class WakeEngine(Protocol):
    def process(self, chunk: bytes) -> bool: ...
    def reset(self) -> None: ...


class VoskEngine:
    """Keyword spotting with a Vosk grammar: the recognizer can only hear the grammar words.

    match = "fuzzy" (default): the grammar holds the name, names that sound like it
    (Helen, Allen, Alon ...), common words that sound near it (decoys) and greetings.
    WakeMatcher then decides by sound and by position: "Helen, open my calendar" wakes,
    "I talked with Helen" does not. See wakematch.py.
    match = "exact": the grammar holds only `phrases` (the first version of this feature).
    """

    def __init__(self, cfg: dict[str, Any], default_model_dir: Path):
        from vosk import KaldiRecognizer, Model, SetLogLevel

        SetLogLevel(-1)
        path = Path(cfg.get("model") or default_model_dir).expanduser()
        if not path.exists():
            raise RuntimeError(f"Vosk model not found at {path}. Run: elen download-wake-model")
        self.model = Model(str(path))
        self.fuzzy = str(cfg.get("match") or "fuzzy").lower() != "exact"
        # Lowest recognizer confidence for the name. In fuzzy mode similar names in the
        # grammar share the confidence, so its limit is lower.
        self.min_conf = float(cfg.get("confidence", 0.25)) if self.fuzzy else float(cfg.get("threshold", 0.5))
        # fast = react on partial results: quicker, but more false triggers. Off by default.
        self.use_partial = bool(cfg.get("fast", False))
        if self.fuzzy:
            self.matcher = WakeMatcher(cfg)
            names = self._known(SOUND_ALIKES + self.matcher.names)
            self.variants = self.matcher.variants(names)
            if not self.variants:
                raise RuntimeError(f"No word in the Vosk model sounds like {self.matcher.names}.")
            grammar = self.variants + self._known(sorted(GREETINGS) + DECOYS)
            log.info("wake name variants: %s", ", ".join(self.variants))
        else:
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
            self.phrases = grammar = known
        grammar = list(dict.fromkeys(grammar)) + ["[unk]"]
        self._make = lambda: KaldiRecognizer(self.model, RATE, json.dumps(grammar))
        self.rec = self._make()
        self.rec.SetWords(True)
        self._audio = bytearray()  # audio of the current utterance
        self.tail = b""  # the request said in the same breath as the wake word

    def _known(self, words: list[str]) -> list[str]:
        return [w for w in dict.fromkeys(words) if self.model.vosk_model_find_word(w) >= 0]

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
        if self.fuzzy:
            found = self.matcher.find(text.split())
            return found is not None and found.where == "start"
        return any(p in text for p in self.phrases)

    def _fuzzy_hit(self, words: list[dict], audio: bytes) -> bool:
        found = self.matcher.find(words)
        if found is None:
            return False
        name = words[found.start:found.end]
        if min(w.get("conf", 1.0) for w in name) < self.min_conf:
            log.debug("wake: '%s' sounds right but conf is low", found.heard)
            return False
        start = name[0].get("start", 0)
        if found.where == "end" and start - words[found.start - 1].get("end", start) < END_PAUSE:
            # "What time is it, Elen" has a short pause before the name, "lunch with Helen" not.
            return False
        log.info("wake: heard '%s' (sound %.2f, %s)", found.heard, found.score, found.where)
        if found.where == "end":
            # "What time is it, Elen": the request is the audio before the name.
            self.tail = audio[: int(start * RATE) * 2] if start > 0.5 else b""
        else:
            # "Elen, open my calendar" in one breath: keep the audio after the name.
            end = name[-1].get("end", 0)
            self.tail = audio[int(end * RATE) * 2:] if words[-1].get("end", 0) - end > 0.3 else b""
        return True

    def process(self, chunk: bytes) -> bool:
        self._audio.extend(chunk)
        if len(self._audio) > RATE * 2 * 30:  # keep at most 30 s
            del self._audio[: len(self._audio) - RATE * 2 * 30]
        if self.rec.AcceptWaveform(chunk):
            result = json.loads(self.rec.Result())
            audio, self._audio = bytes(self._audio), bytearray()
            words = result.get("result") or []
            if self.fuzzy:
                return self._fuzzy_hit(words, audio)
            if not self._hit(result.get("text", "")):
                return False
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
