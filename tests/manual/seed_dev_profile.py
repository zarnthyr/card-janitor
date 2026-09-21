# Copyright (C) 2026 Zarnthyr
# License: GNU AGPL v3 or later

from __future__ import annotations

import json
import time
import urllib.request

URL = "http://127.0.0.1:8765"
TAG = "card_janitor_test_fixture"
SOURCE = "Card Janitor Test::Source"
CHILD = "Card Janitor Test::Source::Child"
OUTSIDE = "Card Janitor Test::Outside Scope"
RETIRED = "Card Janitor Retired"
DELETE = "Card Janitor Test::Delete"
TAG_ACTIONS = "Card Janitor Test::Tag Actions"
NOTE_ACTIONS = "Card Janitor Test::Note Actions"
SIBLING_MODEL = "Card Janitor Test Siblings"


def invoke(action: str, **params: object) -> object:
    payload = json.dumps({"action": action, "version": 6, "params": params}).encode()
    request = urllib.request.Request(
        URL,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310
        result = json.load(response)
    if result["error"] is not None:
        message = f"{action}: {result['error']}"
        raise RuntimeError(message)
    return result["result"]


active = invoke("getActiveProfile")
if active != "dev":
    message = f"Refusing to seed active profile {active!r}; switch to 'dev'"
    raise SystemExit(message)

existing_notes = invoke("findNotes", query=f"tag:{TAG}")
if existing_notes:
    invoke("deleteNotes", notes=existing_notes)

for deck in (SOURCE, CHILD, OUTSIDE, RETIRED, DELETE, TAG_ACTIONS, NOTE_ACTIONS):
    invoke("createDeck", deck=deck)

fixtures = (
    ("New — no review history", SOURCE, None, ()),
    ("New — no review history (suspended)", SOURCE, "new_suspended", ()),
    ("Review — first reviewed 400 days ago, interval 400", SOURCE, "old_review", ()),
    ("Review — first reviewed 5 days ago, interval 400", SOURCE, "recent_long", ()),
    ("Review — first reviewed 5 days ago, interval 10", SOURCE, "recent_short", ()),
    ("Learning — review history exists", SOURCE, "learning", ()),
    ("Relearning — review history exists", SOURCE, "relearning", ()),
    ("Child deck — first reviewed 400 days ago", CHILD, "old_review", ()),
    ("Outside scope — interval 400", OUTSIDE, "recent_long", ()),
    (
        "Tag actions — suspended leech",
        TAG_ACTIONS,
        "tagged_suspended",
        ("leech", "cj_test_remove"),
    ),
    (
        "Tag actions — replace all tags",
        TAG_ACTIONS,
        None,
        ("cj_test_replace", "old_tag"),
    ),
    ("Delete — isolated undo test", DELETE, None, ()),
)
notes = [
    {
        "deckName": deck,
        "modelName": "Basic",
        "fields": {
            "Front": label,
            "Back": "Card Janitor development fixture",
        },
        "options": {"allowDuplicate": False},
        "tags": [TAG, *extra_tags],
    }
    for label, deck, _kind, extra_tags in fixtures
]
note_ids = invoke("addNotes", notes=notes)
if not all(isinstance(note_id, int) for note_id in note_ids):
    message = f"Could not add every fixture: {note_ids!r}"
    raise RuntimeError(message)

now_ms = int(time.time() * 1000)
review_rows = []
for offset, ((label, _deck, kind, _extra_tags), note_id) in enumerate(
    zip(fixtures, note_ids, strict=True),
    start=1,
):
    card_ids = invoke("findCards", query=f"nid:{note_id}")
    if len(card_ids) != 1:
        message = f"Expected one card for {label!r}, got {card_ids!r}"
        raise RuntimeError(message)
    card_id = card_ids[0]
    if kind is None:
        continue
    if kind in {"new_suspended", "tagged_suspended"}:
        invoke(
            "setSpecificValueOfCard",
            card=card_id,
            keys=["type", "queue", "ivl"],
            newValues=[0, -1, 0],
            warning_check=True,
        )
        continue

    old = kind == "old_review"
    review_ms = now_ms - (400 if old else 5) * 86_400_000 + offset
    interval = 400 if kind in {"old_review", "recent_long"} else 10
    card_type = 1 if kind == "learning" else 3 if kind == "relearning" else 2
    queue = 1 if card_type in {1, 3} else 2
    invoke(
        "setSpecificValueOfCard",
        card=card_id,
        keys=["type", "queue", "ivl", "reps"],
        newValues=[card_type, queue, interval, 1],
        warning_check=True,
    )
    review_rows.append([review_ms, card_id, -1, 3, interval, 0, 2500, 1000, 1])

invoke("insertReviews", reviews=review_rows)

if SIBLING_MODEL not in invoke("modelNames"):
    invoke(
        "createModel",
        modelName=SIBLING_MODEL,
        inOrderFields=["Front", "Back"],
        cardTemplates=[
            {"Name": "Forward", "Front": "{{Front}}", "Back": "{{FrontSide}}<hr>{{Back}}"},
            {"Name": "Reverse", "Front": "{{Back}}", "Back": "{{FrontSide}}<hr>{{Front}}"},
        ],
    )
for operation in ("suspend", "unsuspend", "move"):
    note_id = invoke(
        "addNote",
        note={
            "deckName": NOTE_ACTIONS,
            "modelName": SIBLING_MODEL,
            "fields": {
                "Front": f"Note actions — {operation} siblings",
                "Back": "Outside-scope sibling",
            },
            "tags": [TAG, f"cj_test_note_{operation}"],
        },
    )
    cards = invoke("findCards", query=f"nid:{note_id}")
    if len(cards) != 2:
        message = f"Expected two cards for note action {operation!r}, got {cards!r}"
        raise RuntimeError(message)
    first, sibling = sorted(cards)
    invoke("changeDeck", cards=[sibling], deck=OUTSIDE)
    if operation != "move":
        invoke(
            "setSpecificValueOfCard",
            card=first if operation == "suspend" else sibling,
            keys=["type", "queue", "ivl"],
            newValues=[0, -1, 0],
            warning_check=True,
        )
invoke("reloadCollection")
print(f"Seeded {len(fixtures) + 6} cards in the dev profile")
print("Load tests/manual/dev-profile-config.json: 13 planned cards, 7 overlapping cards")
print("Use Preview to inspect merged changes and note-action siblings before cleanup")
