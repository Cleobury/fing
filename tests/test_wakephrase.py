from jevharness import wakephrase as w


def test_whisper_misspellings_of_the_name_still_match():
    for heard in ("Hey Fing.", "Hey thing.", "Hey, Finn.", "So, hey Fing."):
        assert w.is_wake("hey fing", heard, 0.5), heard


def test_a_phrase_saved_before_the_rename_still_works():
    for heard in ("Hey Jev.", "Hey Jeff.", "Hey, Jeb.", "So, hey Jev."):
        assert w.is_wake("hey jev", heard, 0.5), heard


def test_talk_about_things_does_not_match_hey_fing():
    for heard in ("The thing is", "I found a thing", "Hey you", "Hey everything"):
        assert not w.is_wake("hey fing", heard, 0.5), heard


def test_renaming_the_assistant_renames_its_wake_phrase():
    assert w.renamed("hey fing", "Fing", "Pointer") == "hey pointer"
    assert w.renamed("okay fing", "Fing", "Max Power") == "okay max power"
    assert w.renamed("", "Fing", "Pointer") == "hey pointer"


def test_renaming_leaves_a_phrase_without_the_old_name_alone():
    assert w.renamed("hey jarvis", "Fing", "Pointer") == "hey jarvis"


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
    assert st.rating == "weak" and "Hey Fing" in st.suggestions
    assert "Hey Pointer" in w.strength("hey you", "Pointer").suggestions


def test_ratings_for_good_phrases():
    assert w.strength("hey jev").rating == "ok"
    assert w.strength("hello jarvis").rating == "good"
    assert w.strength("").rating == "weak"
