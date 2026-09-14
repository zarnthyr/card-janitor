# Configuration

Add and edit policies in the Card Janitor window. Use **Settings** to configure
automatic cleanup, notifications, and debug logging. Choose **Edit JSON** from
that dialog to edit the underlying configuration directly.

Invalid configuration fails closed: no on-demand or automatic cleanup runs
while a validation error is present. Invalid policies remain visible in Card
Janitor and can be repaired with **Edit**. Invalid add-on-wide settings can be
repaired through **Settings** or **Edit JSON**.

Deck names are resolved when a policy is evaluated. A missing or filtered move
destination is an error.

## Mining example

    {
      "config_version": 1,
      "notify_after_automatic_run": true,
      "debug_logging": false,
      "policies": [
        {
          "id": "mining-cleanup",
          "name": "Mining cleanup",
          "state": "manual",
          "scope": {
            "decks": ["Mining"],
            "include_subdecks": true,
            "include_suspended": false
          },
          "rule": {
            "type": "age",
            "days": 365,
            "from": "first_review",
            "operator": "gte"
          },
          "actions": [
            {"type": "tag", "tag": "retired"},
            {"type": "suspend"}
          ]
        }
      ]
    }

Keep `state` set to `manual` while testing. **Card Janitor** evaluates every
configured policy and shows its scope, conditions, actions, and the number of
cards it would clean up.
Policies in the `manual` or `automatic` state are included by default; disabled
policies remain available but start unchecked. The checkboxes affect only the
current run. Change the state to `automatic` to also run a policy once per Anki
day without approval. Each run is recorded in Anki's undo
history.

The window remains open while you inspect cards. **Browse** opens
the union from all checked policies. Select a row and use **Edit** to change
that policy; **Refresh** recalculates the table. Running a policy manually does
not count as that day's automatic cleanup.

Policy states are:

- `disabled` — never scheduled and unchecked by default in the dashboard
- `manual` — checked by default in the dialog but never scheduled
- `automatic` — checked by default and run at most once per Anki day

Automatic cleanup runs on profile opening if it has not yet run that Anki day.
It also runs when the Anki day changes while the application remains open. Use
**Card Janitor** whenever you want to run policies manually.

Set `notify_after_automatic_run` to `false` to suppress successful automatic-run
summaries. No summary is shown when no cards were changed.
Configuration errors and action conflicts are still reported.

Set `debug_logging` to `true` to print policy evaluation counts and timing to
the terminal. Configuration errors and unexpected exceptions are always
printed, regardless of this setting.

## Conditions

### Age

    {"type": "age", "days": 365, "from": "first_review", "operator": "gte"}

Age uses completed 24-hour periods. `first_review` is the earliest review-log
entry with a genuine answer rating. Cards without such history do not match any
first-review-age comparison. `card_created` uses the creation timestamp embedded
in the card ID.

> **Warning:** `card_created` does not mean "imported into this collection." Imported cards usually
> retain the source author's card IDs and creation timestamps. A newly imported premade
> deck may consequently be years old according to this condition and qualify on its
> first evaluation. Anki does not expose a reliable per-card local-import timestamp.

Use creation-age conditions only for cards whose provenance you understand. Keep the
policy in `manual` mode and inspect its matching cards before changing it to
`automatic`. Card Janitor does not attempt to rewrite card IDs. If you use another
add-on to normalize creation dates, back up the collection first and verify that the
tool safely updates all related references.

### Current interval

    {"type": "interval", "days": 180, "operator": "gte"}

Compares Anki's current interval, in days, with `days`. New cards normally have
an interval of zero, so the `eq` and `lt` operators can include them.

### Card state

    {"type": "card_state", "states": ["new", "learning"], "operator": "in"}

Use `in` for "is any of" and `not_in` for "is none of." Select one or more Anki
states: `new`, `learning`, `review`, or `relearning`. This describes the card's
current Anki state, not whether it has ever been studied.

### Review history

    {"type": "review_history", "operator": "not_exists"}

Use `exists` to match cards with a genuine answer entry in Anki's review log,
or `not_exists` to match cards without one. A previously reviewed card that was
later reset to New still has review history.

### Combining conditions

    {
      "type": "any",
      "rules": [
        {"type": "age", "days": 365, "from": "first_review", "operator": "gte"},
        {"type": "interval", "days": 180, "operator": "gte"}
      ]
    }

Use `any` for OR and `all` for AND. Groups must contain at least one condition.
Composition is deliberately limited to one flat group: every child must be a
single age, interval, card-state, or review-history condition. Nested AND/OR
groups are rejected.

For example, the conditions for a stale-new-card policy are:

    {
      "type": "all",
      "rules": [
        {"type": "age", "days": 30, "from": "card_created", "operator": "gte"},
        {"type": "review_history", "operator": "not_exists"}
      ]
    }

Numeric conditions support `gt`, `gte`, `eq`, `lte`, and `lt`. These correspond
to greater than, at least, exactly, at most, and less than.

## Actions

- `{"type": "tag", "tag": "retired"}` tags the note. Anki has no card-level tags, so sibling cards share it.
- `{"type": "suspend"}` suspends the qualifying card.
- `{"type": "move", "deck": "Retired"}` moves the card to an existing normal deck.
- `{"type": "delete_card"}` deletes the card and removes its note only if no cards remain.

`delete_card` must be the policy's only action. It can use `state: "automatic"`, but automatic deletion runs without confirmation. Manual deletion is shown in the dashboard before execution. The shipped configuration contains no policies, and the Mining example uses `state: "manual"` with tag and suspend actions.

## Scope behavior

`decks` must contain one or more exact deck names. `include_subdecks` includes all descendants of each named deck. Suspended cards are excluded by default. Buried cards remain eligible because burial is temporary.

## Overlapping policies

Compatible actions are merged and deduplicated during manual and automatic execution. Cards with conflicting move destinations, or a deletion combined with another policy's action, are skipped and reported.
