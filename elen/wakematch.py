"""Fuzzy match for the wake name: "Elen" also when speech to text writes Helen, Allen or Alon.

Speech to text does not know the name "Elen", so it writes the nearest word it knows.
This module compares words by how they sound, not by how they are spelled:

1. sounds(): spelling -> a short list of sounds ("helen" -> h e l e n, "allen" -> a l e n).
   Double letters merge, a silent final e goes, vowel groups become one vowel sound.
2. distance(): a weighted edit distance on those sounds. Changes that speech to text
   makes often are cheap (one vowel for another, a lost "h", n/m), others cost 1.
3. WakeMatcher: decides if the name is said TO Elen, from where it is in the sentence.
   "Helen, open my calendar" wakes. "I talked with Helen" does not.

No dependencies, no model, no network. Less than a millisecond for a sentence.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Iterable

VOWELS = frozenset("aeiouy")
FRONT = frozenset("aeiy")  # front vowels: "ellen", "allen", "ellyn" sound very near
# Consonants that speech to text often swaps: cost of a swap inside one group.
GROUPS = [("mn", 0.3), ("dt", 0.4), ("bp", 0.4), ("gkq", 0.4), ("sz", 0.4), ("fv", 0.4), ("SC", 0.4), ("lr", 0.6)]
GREETINGS = frozenset("hey hay hi hello hallo okay ok yo ey ay oi".split())
FILLERS = frozenset("um uh er erm ah oh so well and please".split())
# A name after these words is about a person ("I talked with Helen"), not a call to Elen.
ABOUT_A_PERSON = frozenset(
    "to with and or for from of at by about than met saw see call called tell told ask asked "
    "love like is was were the my your his her our their dear".split()
)
# Names in the Vosk English model that can sound like a short "El-en". The engine puts the
# ones that match the configured name into its grammar.
SOUND_ALIKES = (
    "ellen helen hellen allen alan allan allyn alon elon elan ellyn elin alen aileen eileen "
    "ilene elaine alain ilan alleyne ellie olin len lynn glen"
).split()
# Common words near the name. In the grammar they catch sounds that are not the name, and
# they never wake Elen, also when they sound near enough ("leave me alone").
DECOYS = (
    "hello yellow eleven alone alien lean line lane lemon melon fallen gallon balloon "
    "million hell hill open"
).split()


def sounds(word: str) -> list[str]:
    """Spelling to sounds. Vowel groups stay one item ("ei"); consonants are one letter."""
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return []
    for a, b in (("sch", "S"), ("sh", "S"), ("ch", "C"), ("th", "T"), ("ph", "f"), ("ck", "k"),
                 ("gh", ""), ("wh", "w"), ("qu", "kw"), ("x", "ks")):
        w = w.replace(a, b)
    w = re.sub(r"c(?=[eiy])", "s", w).replace("c", "k")
    if w.startswith("y") and len(w) > 1 and w[1] in VOWELS:
        w = "j" + w[1:]  # "yellow": y is a consonant here
    # Silent final e ("alone", "elaine"): only when an earlier vowel carries the word.
    if len(w) > 3 and w.endswith("e") and w[-2] not in VOWELS and any(c in VOWELS for c in w[:-2]):
        w = w[:-1]
    out: list[str] = []
    i = 0
    while i < len(w):
        if w[i] in VOWELS:
            j = i
            while j < len(w) and w[j] in VOWELS:
                j += 1
            out.append(w[i:j])
            i = j
        else:
            if not out or out[-1] != w[i]:
                out.append(w[i])
            i += 1
    return out


def _vowel(s: str) -> bool:
    return s[0] in VOWELS


def _sub(a: str, b: str) -> float:
    if a == b:
        return 0.0
    if _vowel(a) and _vowel(b):
        if set(a) & set(b):
            return 0.15
        return 0.25 if a[0] in FRONT and b[0] in FRONT else 0.35
    if _vowel(a) or _vowel(b):
        return 1.0
    for group, cost in GROUPS:
        if a in group and b in group:
            return cost
    return 1.0


def _indel(s: str, at_start: bool, at_end: bool) -> float:
    if _vowel(s):
        return 0.9 if at_start else 0.5  # a lost first vowel ("len") is a weaker match
    if s == "h":
        return 0.25 if at_start else 0.5
    if s in "jw":
        return 0.6
    if s == "s" and at_end:
        return 0.5  # "ellen's"
    return 1.0


def distance(a: list[str], b: list[str]) -> float:
    """Weighted edit distance between two sound lists."""
    n, m = len(a), len(b)
    row = [0.0] * (m + 1)
    for j in range(1, m + 1):
        row[j] = row[j - 1] + _indel(b[j - 1], j == 1, j == m)
    for i in range(1, n + 1):
        prev, row[0] = row[0], row[0] + _indel(a[i - 1], i == 1, i == n)
        for j in range(1, m + 1):
            cur = min(
                prev + _sub(a[i - 1], b[j - 1]),
                row[j] + _indel(a[i - 1], i == 1, i == n),
                row[j - 1] + _indel(b[j - 1], j == 1, j == m),
            )
            prev, row[j] = row[j], cur
    return row[m]


def similarity(name: str, heard: str) -> float:
    """1.0 = sounds the same, 0 = nothing alike. Scaled by the length of the name."""
    target, got = sounds(name), sounds(heard)
    if not target or not got:
        return 0.0
    return max(0.0, 1.0 - distance(target, got) / len(target))


def _word(w: Any) -> str:
    return re.sub(r"[^a-z']", "", (w.get("word", "") if isinstance(w, dict) else str(w)).lower())


@dataclass
class Match:
    start: int  # index of the first word of the name
    end: int  # index after the last word of the name
    heard: str  # what speech to text wrote ("helen", "el len")
    score: float
    where: str  # "start" or "end" or "middle"
    greeting: bool  # "hey" / "okay" before the name


class WakeMatcher:
    """Finds the wake name in recognized words.

    names       the names to answer to (default ["elen"])
    similarity  0..1, how near a word must sound (default 0.8). After a greeting
                ("hey helen") the limit is 0.1 lower.
    never       words that must never wake Elen, for example a person in your home
    where       "start" (default): only "Elen, ..." or "Hey Elen ..."
                "start_end": also "..., Elen" as the last word
                "anywhere": any position (more false wakes)
    """

    def __init__(self, cfg: dict[str, Any] | None = None):
        cfg = cfg or {}
        names = cfg.get("names") or ["elen"]
        self.names = [n.lower().strip() for n in ([names] if isinstance(names, str) else names) if n.strip()]
        self.similarity = float(cfg.get("similarity", 0.8))
        self.never = {_word(w) for w in cfg.get("never") or []} | set(DECOYS)
        self.where = str(cfg.get("where") or "start").lower()

    def score(self, heard: str) -> float:
        heard = heard.lower().strip()
        if not heard or heard.replace(" ", "") in self.never or heard in self.never:
            return 0.0
        best = max(similarity(n, heard.replace(" ", "")) for n in self.names)
        return best - 0.05 if " " in heard else best  # "el len": a split word is a little less sure

    def variants(self, vocabulary: Iterable[str]) -> list[str]:
        """Words from the list that sound like a name, for a speech grammar.

        Words that sound almost the same (helen, hellen, ellyn) split the recognizer's
        confidence between them, so a word is kept only when it sounds clearly different
        from the words already kept. Best matches come first.
        """
        floor = self.similarity - 0.05
        ranked = sorted((w for w in dict.fromkeys(vocabulary) if self.score(w) >= floor),
                        key=self.score, reverse=True)
        out: list[str] = []
        for w in ranked:
            if all(similarity(k, w) < 0.95 for k in out):
                out.append(w)
        return out

    def find(self, words: list[Any]) -> Match | None:
        """The first call to Elen in a list of words (strings or Vosk word dicts), or None."""
        tokens = [_word(w) for w in words]
        for i in range(len(tokens)):
            for size in (1, 2):
                if i + size > len(tokens) or not all(tokens[i:i + size]):
                    continue
                heard = " ".join(tokens[i:i + size])
                before = [t for t in tokens[:i] if t]
                greeting = bool(before) and before[-1] in GREETINGS
                if all(t in GREETINGS or t in FILLERS for t in before):
                    where = "start"
                elif i + size == len(tokens):
                    where = "end"
                else:
                    where = "middle"
                if not self._allowed(where, before):
                    continue
                score = self.score(heard)
                if score >= self.similarity - (0.1 if greeting else 0.0):
                    return Match(i, i + size, heard, score, where, greeting)
        return None

    def _allowed(self, where: str, before: list[str]) -> bool:
        if self.where == "anywhere" or where == "start":
            return True
        if where == "end" and self.where == "start_end":
            # "unk": Vosk heard words outside its grammar; the engine checks the pause instead.
            return (len(before) >= 2 or before == ["unk"]) and before[-1] not in ABOUT_A_PERSON
        return False

    def strip(self, text: str) -> str:
        """Remove the name from a transcript when it is clearly a call to Elen.

        "Helen, open my calendar." -> "open my calendar."
        "What time is it, Ellen?"  -> "What time is it?"
        "Helen is coming at five." stays as it is: no comma, so it is about a person.
        """
        spans = [(m.start(), m.end(), m.group().lower()) for m in re.finditer(r"[A-Za-z']+", text)]
        words = [s[2] for s in spans]
        # At the start: greetings/fillers, the name, then a punctuation mark.
        k = 0
        while k < len(words) and (words[k] in GREETINGS or words[k] in FILLERS):
            k += 1
        for size in (1, 2):
            if k + size > len(words):
                continue
            end = spans[k + size - 1][1]
            rest = text[end:]
            if re.match(r"\s*[,.!?:;]", rest) and self.score(" ".join(words[k:k + size])) >= self.similarity - 0.1:
                cleaned = re.sub(r"^\s*[,.!?:;]+\s*", "", rest)
                return cleaned[:1].upper() + cleaned[1:] if cleaned else ""
        # At the end: ", Ellen?" -> "?"
        for size in (1, 2):
            if len(words) <= size:
                continue
            first = spans[-size][0]
            head = text[:first]
            tail = text[spans[-1][1]:]
            if re.search(r",\s*$", head) and re.fullmatch(r"\s*[.!?]*\s*", tail) \
                    and self.score(" ".join(words[-size:])) >= self.similarity:
                return re.sub(r",\s*$", "", head) + tail.strip()
        return text
