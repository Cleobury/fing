import numpy as np

from jevharness import listen
from jevharness.listen import FRAME, RATE, Listener, SpeechGate, Utterance


def quiet(seconds):
    return [np.random.default_rng(0).normal(0, 0.0005, FRAME).astype(np.float32) for _ in range(round(seconds * RATE / FRAME))]


def loud(seconds):
    t = np.arange(FRAME) / RATE
    tone = (0.1 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    return [tone.copy() for _ in range(round(seconds * RATE / FRAME))]


def test_gate_tells_speech_from_quiet():
    gate = SpeechGate()
    assert not any(gate(f) for f in quiet(1))
    assert all(gate(f) for f in loud(0.5))


def test_utterance_starts_on_speech_and_ends_after_silence():
    gate, utt = SpeechGate(), Utterance(end_silence_s=0.6)
    events = [utt.push(f, gate(f)) for f in quiet(0.5) + loud(1.0) + quiet(1.0)]
    assert events.count("started") == 1 and events.count("ended") == 1
    # Kept: the pre-roll, the speech, and the quiet up to the end.
    assert 1.5 < utt.seconds < 2.0


class FakeTranscriber:
    def __init__(self, text):
        self.text = text
        self.calls = 0

    def heard(self, audio):
        self.calls += 1
        return self.text


def make(text, allowed=True):
    got = {"wake": [], "heard": []}

    def on_wake(token):
        got["wake"].append(token)
        return allowed

    lst = Listener(FakeTranscriber(text), lambda: allowed, on_wake, lambda token, audio: got["heard"].append((token, audio)))
    lst.wake_enabled = True
    return lst, got


def run(lst, frames):
    for f in frames:
        lst._frame(f)


def test_phrase_and_command_in_one_breath_is_one_command():
    lst, got = make("Hey Fing, open Steam.")
    run(lst, quiet(0.5) + loud(1.5) + quiet(1.0))
    assert len(got["wake"]) == 1 and len(got["heard"]) == 1
    token, audio = got["heard"][0]
    assert token == got["wake"][0] and audio is not None and len(audio) > RATE


def test_phrase_then_pause_waits_for_the_command():
    lst, got = make("Hey Fing.")
    run(lst, quiet(0.5) + loud(0.6) + quiet(1.0))
    assert len(got["wake"]) == 1 and not got["heard"]  # still waiting for the command
    lst.transcriber.text = "should not be asked again"
    run(lst, loud(1.0) + quiet(1.0))
    assert len(got["heard"]) == 1 and lst.transcriber.calls == 1
    _, audio = got["heard"][0]
    assert len(audio) > 1.6 * RATE  # the phrase, a gap, and the command


def test_other_speech_triggers_nothing():
    lst, got = make("What's the weather like?")
    run(lst, quiet(0.5) + loud(1.0) + quiet(1.0))
    assert not got["wake"] and not got["heard"]


def test_nothing_is_transcribed_while_not_allowed():
    lst, got = make("Hey Fing", allowed=False)
    run(lst, quiet(0.5) + loud(1.0) + quiet(1.0))
    assert lst.transcriber.calls == 0 and not got["wake"]


def test_auto_listen_gives_up_when_nobody_speaks(monkeypatch):
    monkeypatch.setattr(listen.time, "monotonic", _clock(0.0))
    lst, got = make("")
    lst.wake_enabled = False
    token = lst.capture(timeout_s=1.0)
    run(lst, quiet(2.0))
    assert got["heard"] == [(token, None)]


def test_auto_listen_returns_the_answer(monkeypatch):
    monkeypatch.setattr(listen.time, "monotonic", _clock(0.0))
    lst, got = make("")
    lst.wake_enabled = False
    token = lst.capture(timeout_s=8.0)
    run(lst, quiet(0.5) + loud(0.7) + quiet(1.0))
    assert len(got["heard"]) == 1 and got["heard"][0][0] == token and got["heard"][0][1] is not None


def test_cancelled_capture_reports_nothing():
    lst, got = make("")
    lst.capture(timeout_s=8.0)
    lst.cancel()
    run(lst, loud(0.7) + quiet(1.0))
    assert not got["heard"]


def test_test_mode_reports_without_triggering():
    lst, got = make("Hey Fing")
    lst.wake_enabled = False
    seen = []
    lst.test_phrase, lst.on_test = "hey fing", lambda heard, score: seen.append((heard, score))
    run(lst, quiet(0.5) + loud(1.0) + quiet(1.0))
    assert seen and seen[0][1] > 0.9 and not got["wake"]


def _clock(start):
    """A monotonic clock that advances one frame per reading, so timeouts run at frame speed."""
    t = [start]

    def now():
        t[0] += FRAME / RATE
        return t[0]
    return now
