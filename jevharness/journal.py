"""The run journal: everything that has happened while carrying out one spoken request.

Each action is recorded with what changed on screen afterwards and whether it worked (Jev judges that from the
next screen). Failures, dead ends while looking around, the AI's plans, questions and done-checks go in too. The AI
planner and explorer get it as `history`, so in a long run they can build on what worked instead of forgetting
it and repeating what failed; Jev's "steps done" is marked where an action didn't seem to work.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .perception import Screen

WORKED, FAILED = 0.6, 0.4  # Jev's "did it work?" probability above/below which the verdict is clear


def describe_change(before: Screen | None, after: Screen | None) -> str:
    """What visibly changed between two screens, briefly: the window, and text that appeared or went."""
    if before is None or after is None:
        return "not checked"
    parts = []
    if after.window_title != before.window_title:
        parts.append(f'window "{before.window_title[:40]}" → "{after.window_title[:40]}"')
    was = {e.text for e in before.elements}
    now = {e.text for e in after.elements}
    appeared = [t for t in dict.fromkeys(e.text for e in after.elements) if t not in was][:4]
    if appeared:
        parts.append("now showing " + ", ".join(f'"{t[:30]}"' for t in appeared))
    if gone := len(was - now):
        parts.append(f"{gone} item{'s' * (gone != 1)} gone")
    return "; ".join(parts) or "no visible change"


def _verdict(worked: float | None) -> str:
    if worked is None:
        return "not checked yet"
    if worked >= WORKED:
        return "worked"
    if worked <= FAILED:
        return "didn't seem to work"
    return "unclear if it worked"


class Journal:
    def __init__(self):
        self.events: list[dict] = []

    def add(self, kind: str, **fields) -> dict:
        event = {"n": len(self.events) + 1, "kind": kind, **fields}
        self.events.append(event)
        return event

    def action(self, step: str, action: str, source: str) -> dict:
        """An action about to be carried out. Fill in "change" and "worked" once the next screen is seen."""
        return self.add("action", step=step, action=action, source=source, change=None, worked=None)

    def unverified_action(self) -> dict | None:
        """The latest action, if Jev hasn't judged yet whether it worked."""
        last = next((e for e in reversed(self.events) if e["kind"] == "action"), None)
        return last if last is not None and last["worked"] is None else None

    def done_for_jev(self) -> list[str]:
        """The actions done so far, marked where they didn't seem to work, for Jev's `steps_done`."""
        return [e["action"] + (" (didn't seem to work)" if e["worked"] is not None and e["worked"] <= FAILED else "")
                for e in self.events if e["kind"] == "action"]

    def for_ai(self, limit: int = 30) -> list[str]:
        """The journal as short lines for the AI, most recent last."""
        lines = []
        for e in self.events[-limit:]:
            n, kind = e["n"], e["kind"]
            if kind == "action":
                lines.append(f'{n}. {e["action"]} (for "{e["step"][:60]}", from {e["source"]}) → '
                             f'{e["change"] or "not checked"}; {_verdict(e["worked"])}')
            elif kind == "failed":
                lines.append(f'{n}. couldn\'t do "{e["step"][:60]}": {e["reason"][:160]}')
            elif kind == "look":
                lines.append(f'{n}. {e["what"][:80]} → {e["result"][:120]}')
            elif kind == "plan":
                lines.append(f'{n}. AI planned: {" → ".join(e["steps"])[:200]}' + (f' ({e["why"][:80]})' if e.get("why") else ""))
            elif kind == "question":
                lines.append(f'{n}. asked "{e["question"][:80]}" → {e["answer"]}')
            elif kind == "check":
                lines.append(f'{n}. checked whether the whole request is done: {e["p"]:.0%}')
            elif kind == "verify":
                lines.append(f'{n}. check "{e["condition"][:80]}": {"passed" if e["passed"] else "FAILED"} ({e["p"]:.0%})')
        return lines
