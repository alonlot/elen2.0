import array
import asyncio
import os
import shutil
import subprocess
import wave
from pathlib import Path

import pytest

from conftest import ScriptedProvider, say
from elen.wake import WakeListener


class FakeEngine:
    """Hears the wake phrase in the 3rd chunk; the request came in the same breath."""

    def __init__(self, tail=b""):
        self.n = 0
        self.tail = tail

    def process(self, chunk):
        self.n += 1
        return self.n == 3

    def reset(self):
        self.n = 0


def loud(seconds):
    return array.array("h", [8000, -8000] * int(8000 * seconds)).tobytes()


async def test_wake_runs_the_voice_flow_with_same_breath_audio(make_core, tmp_path):
    raw = tmp_path / "mic.raw"
    raw.write_bytes(loud(1))
    core = await make_core([say("It is noon.")])

    heard = []

    class FakeTranscriber:
        async def transcribe(self, wav):
            with wave.open(str(wav)) as w:
                heard.append(w.getnframes())
            return "what time is it"

    core.listener.transcriber = FakeTranscriber()
    events = []
    core.subscribe(lambda k, p: events.append(k))
    listener = WakeListener(FakeEngine(tail=loud(1.5)), ["cat", str(raw)], core._on_wake)
    core.wake = listener
    listener.start()
    for _ in range(100):
        if any(m["role"] == "assistant" for m in core.history.display):
            break
        await asyncio.sleep(0.05)
    await listener.stop()
    assert "wake" in events
    assert heard == [24000]  # the 1.5 s tail was transcribed, no new recording
    assert core.history.display[0]["text"] == "what time is it"
    assert core.history.display[0]["meta"]["source"] == "voice"
    assert core.history.display[-1]["text"] == "It is noon."


async def test_toggle_wake_without_model_reports_error(make_core):
    core = await make_core([], wake={"enabled": False, "model": "/nonexistent"})
    state = core.toggle_wake()
    assert state["enabled"] is False and state["error"]


MODEL = os.environ.get("ELEN_TEST_VOSK_MODEL", "/tmp/claude-0/vosk/vosk-model-small-en-us-0.15")


@pytest.mark.skipif(not (Path(MODEL).exists() and shutil.which("espeak-ng")), reason="needs a Vosk model and espeak-ng")
def test_real_vosk_engine(tmp_path):
    np = pytest.importorskip("numpy")
    from elen.wake import VoskEngine

    eng = VoskEngine({"model": MODEL}, Path("."))

    def speak(text):
        wav = tmp_path / "s.wav"
        subprocess.run(["espeak-ng", "-v", "en-us", "-w", str(wav), text], check=True)
        with wave.open(str(wav)) as w:
            x = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32)
            rate = w.getframerate()
        t = np.arange(0, len(x) / rate, 1 / 16000)
        y = np.interp(t, np.arange(len(x)) / rate, x)
        return np.concatenate([np.zeros(8000), y, np.zeros(16000)]).astype(np.int16).tobytes()

    def triggers(text):
        eng.reset()
        data = speak(text)
        return any(eng.process(data[i:i + 3200]) for i in range(0, len(data), 3200))

    assert triggers("Hey Ellen")
    assert triggers("Okay Ellen, open my calendar") and len(eng.tail) > 16000
    assert not triggers("Hey Jarvis")
    assert not triggers("I talked with Helen and Allen about the yellow bus")
