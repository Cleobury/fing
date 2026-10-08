from types import SimpleNamespace

import pytest

d = pytest.importorskip("fing.decide")  # needs typesafe-sdk


def el(id, text, left, top, w=120, h=30):
    """Stands in for perception.Element, which needs Windows OCR to import."""
    return SimpleNamespace(id=id, text=text, center=(left + w // 2, top + h // 2), rect=(left, top, left + w, top + h))


def answers(target):
    def ans(probs):
        choice = max(probs, key=probs.get)
        return SimpleNamespace(choice=choice, probabilities=probs, noul=0.0)
    blank = {"none": 1.0}
    return {"action": ans({"click": 0.95, "none": 0.05}), "target": ans(target), "key": ans(blank),
            "scroll": ans({"down": 1.0}), "submit": ans(blank), "doable": ans(blank), "ambiguous": ans(blank),
            "loading": ans(blank), "drop": ans(blank), "navigate": ans(blank)}


TITLE, BUTTON = el("e1", "WorkLaptop", 680, 495), el("e2", "Connect", 690, 550)


def test_close_call_between_a_card_title_and_its_button_clicks_the_button():
    p = d.plan(answers({"e1": 0.54, "e2": 0.45, "none": 0.01}), [TITLE, BUTTON], [], [], 0.5, 0.5, "connect to my work laptop")
    assert p.ok and p.target is BUTTON


def test_clear_pick_of_the_title_still_goes_to_the_button():
    p = d.plan(answers({"e1": 0.71, "e2": 0.27, "none": 0.02}), [TITLE, BUTTON], [], [], 0.5, 0.5, "connect to my work laptop")
    assert p.ok and p.target is BUTTON


def test_a_far_away_button_is_not_assumed_to_go_with_the_title():
    far = el("e2", "Connect", 1500, 1000)
    p = d.plan(answers({"e1": 0.54, "e2": 0.45, "none": 0.01}), [TITLE, far], [], [], 0.5, 0.5, "connect to my work laptop")
    assert not p.ok and p.question is not None


def test_without_a_matching_verb_it_still_asks():
    p = d.plan(answers({"e1": 0.54, "e2": 0.45, "none": 0.01}), [TITLE, BUTTON], [], [], 0.5, 0.5, "open my work laptop")
    assert not p.ok and p.question is not None
