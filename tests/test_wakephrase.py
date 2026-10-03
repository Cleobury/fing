from jevharness import wakephrase as w


def test_whisper_misspellings_of_the_name_still_match():
    for heard in ("Hey Jev.", "Hey Jeff.", "Hey, Jeb.", "So, hey Jev."):
        assert w.is_wake("hey jev", heard, 0.5), heard


def test_ordinary_talk_does_not_match():
    for heard in ("Hey you", "They have got it", "The weather today", "I said hey to Jeff", "Hey."):
        assert not w.is_wake("hey jev", heard, 0.5), heard


def test_rest_is_the_command_after_the_phrase():
    assert w.match("hey jev", "Hey, Jeb, open Steam.").rest == "open Steam"
    assert w.strip("hey jev", "Hey Jev. Open Spotify and play jazz.", 0.5) == "Open Spotify and play jazz"


def test_strip_leaves_commands_without_the_phrase_alone():
    assert w.strip("hey jev", "open Steam", 0.5) == "open Steam"


def test_sensitivity_moves_the_threshold():
    assert w.threshold(0.0) > w.threshold(0.5) > w.threshold(1.0)


def test_one_word_is_weak_and_suggests_two_word_phrases():
    st = w.strength("Jev")
    assert st.rating == "weak"
    assert st.suggestions[:2] == ["Hey Jev", "Okay Jev"]


def test_suggestions_use_the_name_typed():
    assert w.strength("nova").suggestions[0] == "Hey Nova"


def test_everyday_words_are_weak():
    st = w.strength("hey you")
    assert st.rating == "weak" and "Hey Jev" in st.suggestions


def test_ratings_for_good_phrases():
    assert w.strength("hey jev").rating == "ok"
    assert w.strength("hello jarvis").rating == "good"
    assert w.strength("").rating == "weak"
