import pytest

from elen.wakematch import SOUND_ALIKES, WakeMatcher, similarity, sounds


def test_sounds_merge_spelling_differences():
    assert sounds("Ellen") == sounds("elen") == ["e", "l", "e", "n"]
    assert sounds("Helen") == ["h", "e", "l", "e", "n"]
    assert sounds("alone") == ["a", "l", "o", "n"]  # silent final e
    assert sounds("yellow")[0] == "j"  # y before a vowel is a consonant


@pytest.mark.parametrize("word", ["ellen", "helen", "allen", "elan", "elon", "eileen", "alon", "alan"])
def test_names_that_sound_like_elen(word):
    assert similarity("elen", word) >= 0.8


@pytest.mark.parametrize("word", ["hello", "eleven", "yellow", "lemon", "melon", "evan", "then", "open"])
def test_words_that_do_not(word):
    assert similarity("elen", word) < 0.7


def test_where_the_name_is_decides():
    m = WakeMatcher()
    assert m.find("helen open my calendar".split()).where == "start"
    assert m.find("um okay allen what time is it".split()).greeting
    assert m.find("el len read my mail".split()).heard == "el len"  # a split word
    assert m.find("i talked with helen yesterday".split()) is None
    assert m.find("what time is it ellen".split()) is None  # "start" only by default


def test_greeting_allows_a_weaker_match():
    m = WakeMatcher()
    assert m.find(["len"]) is None
    assert m.find(["hey", "len"]) is not None


def test_end_position_skips_a_name_about_a_person():
    m = WakeMatcher({"where": "start_end"})
    assert m.find("what time is it ellen".split()).where == "end"
    assert m.find("i had lunch with helen".split()) is None


def test_decoys_and_never_words_do_not_wake():
    assert WakeMatcher().find("alone at last".split()) is None
    m = WakeMatcher({"never": ["alon"]})
    assert m.find("alon dinner is ready".split()) is None
    assert m.find("ellen dinner is ready".split()) is not None


def test_vosk_word_dicts():
    words = [{"word": "hey", "conf": 0.5}, {"word": "elon", "conf": 0.9}, {"word": "[unk]", "conf": 1.0}]
    found = WakeMatcher().find(words)
    assert (found.start, found.end, found.heard) == (1, 2, "elon")


def test_other_names():
    m = WakeMatcher({"names": ["jarvis"]})
    assert m.find("jarvis open the door".split())
    assert m.find("helen open the door".split()) is None


def test_variants_keep_one_word_per_sound():
    v = WakeMatcher().variants(SOUND_ALIKES)
    assert v[0] == "ellen"
    assert "hellen" not in v  # same sound as helen
    assert "lynn" not in v and "ellie" not in v  # too far


@pytest.mark.parametrize(
    "text, expected",
    [
        ("Helen, open my calendar.", "Open my calendar."),
        ("Hey Allen, what time is it?", "What time is it?"),
        ("What time is it, Ellen?", "What time is it?"),
        ("Helen is coming at five.", "Helen is coming at five."),
        ("Send this to Ellen.", "Send this to Ellen."),
        ("Ellen.", ""),
    ],
)
def test_strip_from_transcript(text, expected):
    assert WakeMatcher().strip(text) == expected
