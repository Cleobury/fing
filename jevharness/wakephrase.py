"""The wake phrase: spotting it in what Whisper heard, and rating how well a phrase will work.

Whisper has never heard of "Jev", so it writes what it thinks it heard ("hey Jeff", "hey Jeb", "hey Dev").
The match therefore compares both the letters and a rough sound code (Soundex without the padding) of the
first few words, and takes the better of the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

DEFAULT_PHRASE = "hey jev"

# Everyday words, including the greetings people put in front of a name. A phrase made only of these goes
# off from ordinary talk, TV and calls.
COMMON = set("""
a about after again all also am an and any are as at back be because been before being but by can come could
day did do does done down each even every first for from get give go going good got great had has have he
her here him his how i if in into is it its just know last let like little look made make man many may me
more most much must my need never new no not now of off oh old on one only or other our out over people
please put right said same say see she should show so some start still stop such take tell than thank thanks
that the their them then there these they thing think this those time to too two up us use very want was
way we well were what when where which who why will with work would yeah yes yet you your
hey hi hello okay ok ay yo hiya alright right listen wake up
computer phone assistant google alexa siri
""".split())

_SOUNDEX = {**dict.fromkeys("bfpv", "1"), **dict.fromkeys("cgjkqsxz", "2"), **dict.fromkeys("dt", "3"),
            "l": "4", **dict.fromkeys("mn", "5"), "r": "6"}


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower().replace("'", "").replace("’", ""))


def sound(word: str) -> str:
    """Soundex-style code: the first letter, then the consonant groups, without Soundex's zero padding (which
    would make every short word look alike)."""
    if not word:
        return ""
    out, last = [word[0].upper()], _SOUNDEX.get(word[0], "")
    for ch in word[1:]:
        code = _SOUNDEX.get(ch, "")
        if code and code != last:
            out.append(code)
        if ch not in "hw":
            last = code
    return "".join(out)


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio() if a and b else 0.0


@dataclass
class Match:
    score: float  # 0..1, how closely the start of what was heard matches the phrase
    rest: str  # what was said after the phrase ("open Steam" in "Hey Jev, open Steam")


def match(phrase: str, heard: str) -> Match:
    """Find the wake phrase at (or within a couple of words of) the start of `heard`."""
    target, said = words(phrase), words(heard)
    if not target or not said:
        return Match(0.0, "")
    n = len(target)
    t_text, t_sound = " ".join(target), " ".join(sound(w) for w in target)
    best, best_end = 0.0, 0
    for start in range(min(3, len(said))):  # allow a filler first ("so hey Jev", "um hey Jev")
        for size in (n - 1, n, n + 1):
            if size < 1 or start + size > len(said):
                continue
            window = said[start:start + size]
            score = max(_ratio(t_text, " ".join(window)), _ratio(t_sound, " ".join(sound(w) for w in window)))
            score -= 0.05 * start  # a phrase further in is a little less likely to be the wake word
            if score > best:
                best, best_end = score, start + size
    rest = _rest_of(heard, best_end)
    return Match(round(max(0.0, best), 3), rest)


def _rest_of(heard: str, n_words: int) -> str:
    """`heard` after its first `n_words` words, with the original spelling and punctuation trimmed."""
    seen = 0
    for m in re.finditer(r"[A-Za-z0-9'’]+", heard):
        if seen == n_words:
            return heard[m.start():].strip(" ,.!?;:-")
        seen += 1
    return ""


def threshold(sensitivity: float) -> float:
    """Score a match needs: sensitivity 0 = strict (fewer false triggers), 1 = loose (catches more)."""
    return 0.85 - 0.2 * min(1.0, max(0.0, sensitivity))


def is_wake(phrase: str, heard: str, sensitivity: float) -> Match | None:
    m = match(phrase, heard)
    return m if m.score >= threshold(sensitivity) else None


def strip(phrase: str, command: str, sensitivity: float) -> str:
    """The command without the wake phrase in front, if it starts with it."""
    m = is_wake(phrase, command, sensitivity)
    return m.rest if m else command


# ---- how good a phrase is ---------------------------------------------------------------------------------

def syllables(word: str) -> int:
    groups = re.findall(r"[aeiouy]+", word)
    n = len(groups)
    if n > 1 and word.endswith("e") and not word.endswith(("le", "ee", "ye")):
        n -= 1  # a silent final e ("game", "phone")
    return max(1, n)


@dataclass
class Strength:
    rating: str  # "weak", "ok" or "good"
    message: str  # what Settings says about it ("" when it's good)
    suggestions: list[str]  # stronger phrases to offer as buttons


def strength(phrase: str) -> Strength:
    said = words(phrase)
    if not said:
        return Strength("weak", "Type the phrase Jev should listen for.", ["Hey Jev", "Okay Jev"])
    rare = [w for w in said if w not in COMMON]
    name = (rare[-1] if rare else "jev").capitalize()
    suggestions = [s for s in (f"Hey {name}", f"Okay {name}", f"Hi {name}")
                   if words(s) != said]
    total = sum(syllables(w) for w in said)
    if len(said) == 1:
        return Strength("weak", "One word goes off easily in normal talk. Try two words:", suggestions)
    if not rare:
        return Strength("weak", "Everyday words go off easily from TV and calls. Put a name in it:",
                        ["Hey Jev", "Okay Jev"])
    if len(said) > 4:
        return Strength("ok", "Long phrases are hard to say the same way every time. Two or three words work best.", [])
    if total >= 4:
        return Strength("good", "", [])
    return Strength("ok", "Should work. A longer or rarer phrase goes off by mistake less often.", [])
