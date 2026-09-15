# Configuration

Add and edit policies in the Card Janitor window. Use **Settings** to configure
notifications and debug logging. Choose **Edit JSON** to edit the underlying
settings and the current collection's policies together.

Policies are stored in the current Anki collection and sync with it. Notification
and debug settings are add-on-wide and shared by profiles on this installation.

If any setting or policy is invalid, Card Janitor will not run cleanup until
the problem is fixed. Invalid policies remain visible in Card Janitor and can
be repaired with **Edit**. Invalid add-on-wide settings can be repaired through
**Settings** when the field is available there, or through **Edit JSON**.

Deck names are resolved when a policy is evaluated. A missing or filtered move
destination is an error.

## Safety and undo

Card Janitor can permanently delete cards. Back up your collection before use,
begin in On demand mode, and inspect matching cards with **Browse** before
running cleanup. Automatic policies run without confirmation.

Each cleanup run is recorded as one entry in Anki's collection undo history.
Policy configuration changes do not create collection undo entries. Removing a
policy therefore requires confirmation but cannot be undone with Anki's Undo
command.

## Example

    {
      "config_version": 1,
      "notify_after_automatic_run": true,
      "debug_logging": false,
      "policies": [
        {
          "id": "retire-mature-cards",
          "name": "Retire Mature Cards",
          "mode": "on_demand",
          "scope": {
            "decks": ["Mining"],
            "include_subdecks": true,
            "include_suspended": false
          },
          "match": "all",
          "conditions": [
            {"type": "interval", "days": 365, "operator": "gte"}
          ],
          "actions": [
            {"type": "tag", "tags": ["retired"]},
            {"type": "suspend"},
            {"type": "move", "deck": "Retired"}
          ]
        }
      ]
    }

In JSON, `mode: "on_demand"` is the **On demand** mode shown in the policy editor.
Keep this mode while testing. Card Janitor evaluates every configured policy
and shows its scope, conditions, actions, and the number of cards it would
clean up.
Every policy is included by default in the dashboard. The checkboxes affect
only the current run. Change `mode` to `automatic` to also run a policy once
per day without approval. Each run is recorded in Anki's undo history.

The window remains open while you inspect cards. **Browse** opens the union
from all checked policies. Select a row and use **Edit** to change that policy;
**Refresh** recalculates the table. Running a policy on demand does not count as
that day's automatic cleanup.

Policy modes are:

- `on_demand` — runs only when you start cleanup from Card Janitor
- `automatic` — runs once per day without confirmation and can also be run on demand

Automatic cleanup runs when a profile opens if it has not yet run that day. It
also runs when Anki's day changes while the application remains open. Use Card
Janitor whenever you want to run policies on demand.

Set `notify_after_automatic_run` to `false` to suppress successful automatic-run
summaries. No summary is shown when no cards were changed.
Configuration errors and action conflicts are still reported.

Set `debug_logging` to `true` to print policy evaluation counts and timing to
the terminal. Configuration errors and unexpected exceptions are always
printed, regardless of this setting.

## Conditions

### Age

    {"type": "age", "days": 365, "source": "first_review", "operator": "gte"}

Age uses completed 24-hour periods. `first_review` is the earliest review-log
entry with a genuine answer rating. Cards without such history do not match any
first-review-age comparison. `card_created` uses the creation timestamp embedded
in the card ID.

> **Warning:** `card_created` does not mean "imported into this collection." Imported cards usually
> retain the source author's card IDs and creation timestamps. A newly imported premade
> deck may consequently be years old according to this condition and qualify on its
> first evaluation. Anki does not expose a reliable per-card local-import timestamp.

Use creation-age conditions only for cards whose provenance you understand. Keep the
policy in On demand (`mode: "on_demand"`) and inspect its matching cards before changing
it to Automatic. Card Janitor does not attempt to rewrite card IDs. If you use another
add-on to normalize creation dates, back up the collection first and verify that the
tool safely updates all related references.

### Current interval

    {"type": "interval", "days": 180, "operator": "gte"}

Compares Anki's current interval, in days, with `days`. New cards normally have
an interval of zero, so the `eq` and `lt` operators can include them.

### Card state

    {"type": "card_state", "states": ["new", "learning"]}

Select one or more Anki states: `new`, `learning`, `review`, or `relearning`.
A card matches when its current state is one of the selected values. This
describes the card's current Anki state, not whether it has ever been reviewed.

### Review history

    {"type": "review_history", "operator": "not_exists"}

Use `exists` to match cards with a genuine answer entry in Anki's review log,
or `not_exists` to match cards without one. A previously reviewed card that was
later reset to New still has review history.

### Combining conditions

    "match": "any",
    "conditions": [
      {"type": "age", "days": 365, "source": "first_review", "operator": "gte"},
      {"type": "interval", "days": 180, "operator": "gte"}
    ]

Use `match: "any"` for OR and `match: "all"` for AND. `conditions` must contain
at least one age, interval, card-state, or review-history condition. Nested
AND/OR groups are not supported.

For example, the conditions for a stale-new-card policy are:

    "match": "all",
    "conditions": [
      {"type": "age", "days": 30, "source": "card_created", "operator": "gte"},
      {"type": "review_history", "operator": "not_exists"}
    ]

Numeric conditions support `gt`, `gte`, `eq`, `lte`, and `lt`. These correspond
to greater than, at least, exactly, at most, and less than.

## Actions

- `{"type": "tag", "tags": ["retired"]}` adds one or more tags to the note. Anki has no card-level tags, so sibling cards share them.
- `{"type": "suspend"}` suspends the qualifying card.
- `{"type": "move", "deck": "Retired"}` moves the card to an existing normal deck.
- `{"type": "delete_card"}` deletes the card and removes its note only if no cards remain.

`delete_card` must be the policy's only action. It can use `mode: "automatic"`, but automatic deletion runs without confirmation. On-demand deletion is shown in the dashboard before execution. The shipped configuration contains no policies, and the example uses `mode: "on_demand"` with reversible tag, suspend, and move actions.

## Scope behavior

`decks` must contain one or more exact deck names. `include_subdecks` includes all descendants of each named deck. Suspended cards are excluded by default. Buried cards remain eligible because burial is temporary.

## Multiple profiles

Policy definitions are stored in the current collection, so each profile has its
own policies and they follow that collection through AnkiWeb sync. Add-on-wide
settings such as notifications and debug logging remain shared by profiles on the
same Anki installation. The daily automatic-run marker is tracked separately for
each profile. A configured deck that no longer exists in its collection is
reported as a policy error.

## Overlapping policies

Compatible actions are merged and deduplicated during on-demand and automatic execution. Cards with conflicting move destinations, or a deletion combined with another policy's action, are skipped and reported.
