# Policy JSON

Use the collection-wide **Edit as JSON…** command to view or edit the current
collection's complete Card Janitor policy configuration.

**Review copied or shared policies before saving them.** A shared policy can
contain destructive actions or automatic triggers. Check its scope, conditions,
actions, and triggers rather than changing only its deck names. When in doubt,
remove `triggers`, save the policy, and inspect its matches and preview before
running it.

Policies are stored in the current Anki collection and sync with it.

## Example

The top-level document contains only a `policies` array:

    {
      "policies": [
        {
          "id": "2f87a1d4-956b-4f3c-a80e-d53792a4761b",
          "name": "Retire Mature Cards",
          "scope": {
            "decks": [
              {"deck": "Mining", "include_subdecks": true}
            ]
          },
          "match": "all",
          "conditions": [
            {"type": "interval", "days": 365, "operator": "gte"}
          ],
          "actions": [
            {"type": "add_tags", "tags": ["retired"]},
            {"type": "suspend"},
            {"type": "move", "deck": "Retired"}
          ]
        }
      ]
    }

In JSON, every policy requires:

- `id` — a non-empty identifier unique within the collection; the ordinary
  editor generates UUIDs automatically
- `name` — the policy's display name
- `actions` — one or more actions

Optional fields describe only active configuration:

- omit `triggers` for a manual-only policy
- omit `scope` for all decks and all note/card types
- omit both `match` and `conditions` to match all cards within the scope

Do not use empty optional arrays as substitutes for omitted fields.

## Triggers

The optional `triggers` array controls automatic execution. Omit it to keep a
policy manual-only.

Each trigger is an object containing a `type`:

    "triggers": [
      {"type": "on_open"},
      {"type": "on_sync"}
    ]

Supported types are:

- `daily` — once per Anki day
- `on_open` — each time the profile is opened, including profile switches
- `on_sync` — after opening or manually initiated collection sync attempts

Each trigger type may appear at most once in a policy.

All policies can still be run manually regardless of their triggers.

`daily` is checked when the profile opens and when Anki's day changes; Card
Janitor does not periodically poll for it throughout the day. A successful
automatic cleanup satisfies that policy's Daily trigger for the current Anki
day, even when the run makes no changes or encounters only conflicts. Manual
cleanup does not satisfy the Daily trigger.

Opening cleanup waits for Anki's opening collection sync attempt when automatic
sync is enabled. Opening and On sync events are combined into one cleanup when
they occur together.

On sync runs after an opening or manually initiated collection sync attempt,
including a failed or cancelled attempt. Media-only and closing syncs do not
trigger cleanup.

Automatic cleanup never starts another sync. Changes made after a sync reach
other devices on a later sync.

Automatic triggers run without confirmation. Keep `triggers` omitted while
testing a new or shared policy.

## Scope

Omit `scope` to include every current and future normal deck and every current
and future note/card type.

When `scope` is present, it must contain at least one real restriction.

### Decks

Use `decks` to restrict a policy to particular decks:

    "scope": {
      "decks": [
        {"deck": "Mining", "include_subdecks": true}
      ]
    }

Each selector requires:

- `deck` — the deck name
- `include_subdecks` — whether descendants are included

With `include_subdecks: true`, the selector includes the named deck and all of
its current and future subdecks.

With `include_subdecks: false`, only the named deck is included.

Multiple deck selectors are combined as a union.

Omitting `decks` leaves the deck dimension unrestricted. This is different from
explicitly selecting all decks that currently exist: an unrestricted dimension
also includes future decks.

A missing or renamed deck is a policy error.

Filtered-deck cards cannot trigger policies. Suspended and buried cards remain
eligible unless conditions exclude them.

### Note and card types

Use `note_types` to restrict note or card types:

    "scope": {
      "note_types": [
        {"name": "Basic"},
        {
          "name": "Basic (and reversed card)",
          "card_types": ["Card 2"]
        }
      ]
    }

Each entry requires `name`, the note-type name.

Omitting `card_types` from an entry includes all current and future card types
of that note type.

Providing `card_types` restricts the entry to the named card types:

    {
      "name": "Basic (and reversed card)",
      "card_types": ["Card 2"]
    }

An explicit `card_types` array must be non-empty. Future card types are not
included when exact card types are listed.

Omitting `note_types` leaves the note/card-type dimension unrestricted.

When both deck and note-type restrictions are present, a card must satisfy both.

Missing or renamed note or card types are policy errors.

Scope identifies which cards can trigger a policy. Note-wide actions can still
affect sibling cards outside that scope.

## Conditions

Conditions determine which cards within the scope match a policy.

Use:

    "match": "all"

for AND, or:

    "match": "any"

for OR.

`match` and `conditions` must either both be present or both be omitted.
`conditions` must be non-empty when present.

Omit both fields to match every card allowed by the scope. The obsolete
`{"type": "all_cards"}` pseudo-condition is invalid.

A policy's condition list may contain simple conditions and one level of
condition groups. A group uses the same `match` and `conditions` fields, must
contain at least two simple conditions, and cannot contain another group.

Numeric conditions generally support:

- `gt` — greater than
- `gte` — greater than or equal to
- `eq` — equal to
- `lte` — less than or equal to
- `lt` — less than

All numeric thresholds are whole numbers. Day fields accept 0 through 100,000;
answer and lapse counts accept 0 through 1,000,000; ordinary and FSRS
percentage fields accept 0 through 100; and SM-2 ease accepts 0 through 1,000.

### Review-derived data

Review-derived conditions use scheduling-relevant answers: normal Learning,
Review, and Relearning answers, along with early or filtered-deck answers that
update the card's normal scheduling state. Answers in a filtered deck with
rescheduling disabled (preview/cram) do not count. Manually setting a due date,
resetting, or otherwise rescheduling a card without answering it does not count.

Lapse count is the exception: it uses Anki's cumulative per-card lapse value
instead of reconstructing lapses from review-log answer buttons.

### Age

    {"type": "age", "days": 365, "source": "first_review", "operator": "gte"}

Supported sources are:

- `card_created`
- `first_review`
- `last_review`

Age is measured in completed 24-hour periods.

`first_review` uses the earliest scheduling-relevant answer in Anki's review
log. `last_review` uses the latest scheduling-relevant answer. Cards without the
required review history do not match those sources.

`card_created` uses the creation timestamp embedded in the card ID.

> **Warning:** `card_created` does not mean "imported into this collection."
> Imported cards commonly retain the original creator's card IDs and timestamps.
> A newly imported premade deck may therefore appear to be years old and
> immediately match a creation-age policy. Use this condition only when you
> understand the cards' provenance, and inspect its matches before running it.

Age supports whole-number day values from 0 through 100,000.

### Current interval

    {"type": "interval", "days": 180, "operator": "gte"}

Compares the card's current Anki interval, in days, with `days`.

New cards normally have an interval of zero.

Interval supports whole-number day values from 0 through 100,000.

### Overdue

    {"type": "overdue", "days": 30, "operator": "gte"}

Matches due Review cards and compares their whole overdue days. Cards due today
have a value of 0.

### Card state

    {"type": "card_state", "states": ["new", "learning"]}

`states` must contain one or more of:

- `new`
- `learning`
- `review`
- `relearning`

A card matches when its current state is one of the selected values. This
describes its current Anki state, not whether it has ever been reviewed.

### Card flag

    {"type": "card_flag", "flags": ["none", "red", "purple"]}

`flags` must contain one or more of:

- `none`
- `red`
- `orange`
- `green`
- `blue`
- `pink`
- `turquoise`
- `purple`

### Answer count

    {"type": "answer_count", "count": 100, "operator": "gte"}

Counts scheduling-relevant review-log answers with ratings 1 through 4.

### Correct answer count

    {"type": "correct_answer_count", "count": 80, "operator": "gte"}

A correct answer is any scheduling-relevant answer other than Again: ratings 2
through 4.

### Correct answer rate

    {"type": "correct_answer_rate", "percent": 70, "operator": "lt"}

The rate is:

    correct scheduling-relevant answers / all scheduling-relevant answers * 100

Cards with no scheduling-relevant answers do not match this condition.

### Lapse count

    {"type": "lapse_count", "count": 8, "operator": "gte"}

Uses Anki's cumulative per-card lapse count.

### Review history

    {"type": "review_history", "operator": "not_exists"}

Supported operators are:

- `exists`
- `not_exists`

Review history means that the card has at least one scheduling-relevant answer
in Anki's review log. A previously reviewed card that was later reset to New
still has review history.

### Note tags

    {"type": "tags", "operator": "contains_any", "tags": ["leech"]}

Supported operators are:

- `contains_any`
- `contains_all`
- `contains_none`

Tag matching is case-insensitive. Each array entry must be one tag without
whitespace or commas inside its name.

Tags belong to notes, so sibling cards generated from the same note see the
same tags.

### Suspension state

    {"type": "suspension", "operator": "is_suspended"}

Supported operators are:

- `is_suspended`
- `is_not_suspended`

Suspension is independent of the card's New, Learning, Review, or Relearning
state. Without a suspension condition, both suspended and non-suspended cards
can match.

### Sibling suspension

    {"type": "sibling_suspension", "operator": "all"}

Supported operators are:

- `all`
- `any`
- `none`

This checks the suspension state of every card generated from the note,
including the triggering card itself and siblings outside the policy's scope.

### Sibling review history

    {"type": "sibling_review_history", "operator": "none"}

Supported operators are:

- `all`
- `any`
- `none`

This checks every card generated from the note for scheduling-relevant review
history, including the triggering card itself and siblings outside the policy's
scope.

Scope still determines which cards can trigger the policy. Sibling conditions
can inspect cards that are outside scope, suspended, buried, or in filtered
decks.

For example, to delete only completely unstudied notes, use
`sibling_review_history` with `none` together with `delete_note`. A card-level
`review_history: not_exists` condition alone does not establish that its
siblings are also unstudied.

### FSRS stability

    {"type": "fsrs_stability", "days": 90, "operator": "gte"}

Requires FSRS to be enabled. Stability is measured in days.

### FSRS difficulty

    {"type": "fsrs_difficulty", "percent": 70, "operator": "lte"}

Requires FSRS to be enabled. Difficulty is normalized from FSRS's 1–10 scale to
0–100%.

### FSRS retrievability

    {"type": "fsrs_retrievability", "percent": 60, "operator": "lt"}

Requires FSRS to be enabled. Retrievability uses Anki's current estimate on a
0–100% scale.

FSRS stability, difficulty, and retrievability are floating-point metrics and
support `gt`, `gte`, `lte`, and `lt`, but not exact equality.

Cards without the requested FSRS memory-state value do not match that
condition.

### SM-2 ease

    {"type": "sm2_ease", "percent": 250, "operator": "gte"}

Uses the card's ease factor as a percentage, such as 250%.

Cards without a nonzero ease factor do not match this condition.

SM-2 ease is available only while FSRS is disabled. FSRS and SM-2 conditions
cannot be combined in one policy.

A policy whose scheduler requirements do not match the current collection is
not applied until the mismatch is repaired.

### Combining conditions

For example, a stale-new-card policy can require both creation age and no review
history:

    "match": "all",
    "conditions": [
      {
        "type": "age",
        "days": 30,
        "source": "card_created",
        "operator": "gte"
      },
      {
        "type": "review_history",
        "operator": "not_exists"
      }
    ]

To match when either of two conditions is true, use `match: "any"`:

    "match": "any",
    "conditions": [
      {
        "type": "age",
        "days": 365,
        "source": "first_review",
        "operator": "gte"
      },
      {
        "type": "interval",
        "days": 180,
        "operator": "gte"
      }
    ]

To require one condition together with either of two alternatives, place the
alternatives in a group:

    "match": "all",
    "conditions": [
      {
        "type": "age",
        "days": 365,
        "source": "first_review",
        "operator": "gte"
      },
      {
        "match": "any",
        "conditions": [
          {
            "type": "interval",
            "days": 180,
            "operator": "gte"
          },
          {
            "type": "suspension",
            "operator": "is_suspended"
          }
        ]
      }
    ]

The ordinary editor presents groups visually. It supports multiple groups at
the policy level while keeping simple policies as a flat list. Remove and
recreate a condition to place it in a different group or at the policy level.

## Actions

Every policy requires at least one action.

### Tags

Add tags:

    {"type": "add_tags", "tags": ["retired"]}

Remove tags:

    {"type": "remove_tags", "tags": ["leech"]}

Replace the note's complete tag set:

    {"type": "replace_tags", "tags": ["reviewed"]}

An empty `tags` array with `replace_tags` clears all tags.

All tag actions affect notes, so their results are shared by sibling cards.
`replace_tags` is destructive because tags not listed in the action are removed.
Preview describes a tag change on the triggering card rather than repeating
every sibling of the same note as a separate row.

### Suspension

Suspend the matching card:

    {"type": "suspend"}

Unsuspend the matching card:

    {"type": "unsuspend"}

Suspend every card generated from the matching note:

    {"type": "suspend_note"}

Unsuspend every card generated from the matching note:

    {"type": "unsuspend_note"}

### Move

Move the matching card:

    {"type": "move", "deck": "Retired"}

Move every card generated from the matching note:

    {"type": "move_note", "deck": "Retired"}

The destination must be an existing normal deck. A missing or filtered
destination deck is a policy error.

### Flags

Set a flag:

    {"type": "set_flag", "flag": "purple"}

Supported flags are:

- `red`
- `orange`
- `green`
- `blue`
- `pink`
- `turquoise`
- `purple`

Clear the matching card's flag:

    {"type": "clear_flag"}

### Delete card

    {"type": "delete_card"}

Deletes the matching card. Its note is also removed if no cards remain.

`delete_card` must be the policy's only action.

### Delete note

    {"type": "delete_note"}

Deletes the matching card's complete note and every card generated from it,
including sibling cards outside the policy's scope.

`delete_note` must be the policy's only action.

### Note-wide actions

`suspend_note`, `unsuspend_note`, `move_note`, and `delete_note` operate on the
whole note after scope and conditions identify a triggering card.

They can therefore affect sibling cards outside the selected deck scope,
including siblings that could not themselves trigger the policy.

Preview shows these expanded effects. If a sibling conflicts with another
policy, the note-wide operation is skipped for the whole note.

Manager Browse includes out-of-scope siblings that need one of these note-wide
actions. Browse inside an individual policy editor instead shows only the
in-scope cards whose scope and conditions trigger the draft policy. Individual
Preview shows the expanded planned effects and should be used to inspect those
siblings before saving or running the policy.

Within one policy, all move actions must use the same destination. Suspend and
unsuspend actions cannot be combined, `replace_tags` cannot be combined with
other tag actions, the same tag cannot be both added and removed, and flag
actions cannot request different results.

## Deletion safety

Deletion policies may use automatic triggers, and automatic deletion happens
without confirmation.

Clicking **Clean Up** also applies the checked policies without another
confirmation. Inspect destructive policies with Browse and Preview first.

A collection-wide deletion policy with neither a genuine scope restriction nor
matching conditions is invalid. `delete_card` and `delete_note` cannot be used
as an otherwise unrestricted All cards policy. A scope or condition restriction
makes the policy valid even if it still matches a large part of the collection;
Card Janitor does not impose a card-count or percentage limit.

Card Janitor does not ship with any policies by default.

## Validation

Card Janitor distinguishes between the policy's JSON structure and references
that depend on the current collection.

Structurally invalid JSON cannot be used for cleanup. Examples include unknown
types or fields, invalid combinations, empty required arrays, duplicate policy
IDs, or missing required fields.

A structurally valid policy can still be unusable in the current collection.
Examples include:

- a missing or renamed deck
- a missing or renamed note or card type
- a missing or filtered move destination
- an FSRS condition while FSRS is disabled
- an SM-2 condition while FSRS is enabled

Such policies remain visible so they can be repaired, but they are not applied
until their errors are resolved.

The ordinary policy editor validates collection references when saving.
Advanced JSON editing can represent structurally valid policies whose
collection-specific references are currently invalid.

Save reports persistent form and collection errors inline. The editor also
identifies incompatible FSRS or SM-2 selections when they are chosen. Browse
and Preview validate the inputs they need and show transient messages without
adding new persistent form errors.

## Overlapping policies

All selected policies are matched against the same pre-cleanup collection
state. Actions from one policy do not make another policy newly match during
that cleanup.

Compatible actions from overlapping policies are merged and deduplicated.

A card is skipped when policies require incompatible changes, such as:

- different destination decks
- suspend and unsuspend
- different flags
- contradictory or incompatible tag changes
- card or note deletion combined with another action

Every conflict is also an overlap, but overlapping policies do not necessarily
conflict.

Already-satisfied intentions can still participate in conflict detection. For
example, a card that is already suspended can still conflict with another
policy intending to unsuspend it.

**Cleanup Preview** shows merged planned changes, overlaps, conflicts, skipped
cards, and siblings included by note-wide actions.

Preview reflects the collection state when it is calculated. Before mutation,
Card Janitor re-evaluates current collection state within the initially
evaluated card boundary. It cancels cleanup if a participating saved policy
definition changed, rather than applying a stale or silently substituted
definition. All participating policies in one evaluation pass use the same
captured time for time-dependent conditions; the pre-execution re-evaluation is
a new pass with a newly captured time.

## Undo

Card Janitor participates in Anki's normal collection Undo system.

A cleanup that changes the collection is incrementally grouped into one Card
Janitor Undo entry as backend operations complete.

If an unexpected operation fails after earlier changes have been applied, Card
Janitor reports whether it can verify that the available Undo entry contains
all successfully completed operations from that cleanup. If it cannot verify
complete recovery, it does not claim that Undo will necessarily restore every
change.

Undo availability follows Anki's normal history and is not a persistent
rollback mechanism.

Undoing an automatic cleanup does not reset its Daily completion record. To
repeat an undone cleanup on the same day, run it manually.

Policy configuration changes themselves do not create collection Undo entries.

## Sharing and policy IDs

Policy IDs are non-empty strings independent of policy names. The ordinary
editor generates UUIDs for them automatically. Renaming a policy does not
change its identity, and different policies may have the same display name.

The collection-wide JSON editor preserves policy IDs and rejects duplicates
case-insensitively.

When JSON is applied through an individual policy editor, an existing policy
keeps its existing ID and a new policy receives a fresh ID. This makes the
individual editor suitable for importing a shared policy without also importing
the source policy's identity.

Review shared policies before saving them, especially their `actions` and
`triggers`.

## Example policies

### Retire mature cards

Tag, suspend, and move cards once their interval reaches 365 days:

    {
      "id": "2f87a1d4-956b-4f3c-a80e-d53792a4761b",
      "name": "Retire Mature Cards",
      "scope": {
        "decks": [
          {"deck": "Mining", "include_subdecks": true}
        ]
      },
      "match": "all",
      "conditions": [
        {"type": "interval", "days": 365, "operator": "gte"}
      ],
      "actions": [
        {"type": "add_tags", "tags": ["retired"]},
        {"type": "suspend"},
        {"type": "move", "deck": "Retired"}
      ]
    }

Because `triggers` is omitted, this policy is manual-only.

### Delete stale unstudied cards

    {
      "id": "8dd43ef6-8188-4b21-a4b1-909c7ebc8eb5",
      "name": "Stale New",
      "scope": {
        "decks": [
          {"deck": "Mining", "include_subdecks": true}
        ]
      },
      "match": "all",
      "conditions": [
        {
          "type": "age",
          "days": 30,
          "source": "card_created",
          "operator": "gte"
        },
        {
          "type": "review_history",
          "operator": "not_exists"
        }
      ],
      "actions": [
        {"type": "delete_card"}
      ]
    }

Creation age uses the timestamp embedded in the card ID. Inspect the matches
carefully before using a policy like this, particularly with imported cards.

### Delete suspended leeches

Anki's `leech` tag belongs to the note, while suspension can apply to an
individual card. Combining both conditions avoids deleting active siblings that
share the tag:

    {
      "id": "72f78238-12b1-437d-a755-55dff842baf7",
      "name": "Suspended Leeches",
      "scope": {
        "decks": [
          {"deck": "Mining", "include_subdecks": true}
        ]
      },
      "match": "all",
      "conditions": [
        {
          "type": "tags",
          "operator": "contains_any",
          "tags": ["leech"]
        },
        {
          "type": "suspension",
          "operator": "is_suspended"
        }
      ],
      "actions": [
        {"type": "delete_card"}
      ]
    }

Use `delete_note` instead only when you intend to delete the complete note and
all of its sibling cards.

## Profiles and storage

Policy definitions are stored in the current Anki collection. Each profile
therefore has its own policies, and those policies follow the collection
through AnkiWeb sync.

Add-on settings such as automatic cleanup, notifications, and debug logging are
local to the Anki installation.

Daily completion and the latest cleanup result are tracked locally per profile
and are not part of the policy JSON.
