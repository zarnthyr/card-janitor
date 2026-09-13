# Configuration

Policies can be added and edited in the Card Janitor window. The **Settings…**
button opens the underlying JSON for add-on-wide options and advanced editing.

Invalid configuration fails closed: if any validation error is present, no manual or automatic policy runs. Invalid policy entries remain visible in the manager and can be repaired with **Edit…**. Invalid add-on-wide settings are shown in the manager and can be repaired through **Settings…**. Deck names are resolved when a policy is evaluated, and a missing or filtered move destination is an error.

Configuration version 2 replaces the former `enabled` and `mode` policy fields
with the single `state` field.

## Mining example

```json
{
  "config_version": 2,
  "automatic_schedule": "daily",
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
        "from": "first_review"
      },
      "actions": [
        {"type": "tag", "tag": "retired"},
        {"type": "suspend"}
      ]
    }
  ]
}
```

Keep `state` set to `manual` while testing. **Card Janitor…** evaluates every
configured policy and shows its scope, conditions, actions, and affected-card count.
Policies in the `manual` or `automatic` state are included by default; disabled
policies remain available but start unchecked. The checkboxes affect only the
current run. Change the state to `automatic` to also run a policy on the
configured schedule without approval. Each run is recorded in Anki's undo
history.

The window remains open while you inspect cards. **View Included Cards** opens
the union from all checked policies. Select a row and use **Edit…** to change
that policy; **Refresh** recalculates the table. Running an automatic policy
manually does not alter its next scheduled run.

Policy states are:

- `disabled` — never scheduled and unchecked by default in the dashboard
- `manual` — checked by default in the dialog but never scheduled
- `automatic` — checked by default in the dialog and also run automatically

`automatic_schedule` supports:

- `profile_open` — run whenever the profile opens
- `daily` — run at most once per Anki day, on profile opening or day change
- `profile_open_and_daily` — run on every profile opening and day change

Set `notify_after_automatic_run` to `false` to suppress successful automatic-run
summaries. No summary is shown when no cards were changed.
Configuration errors and action conflicts are still reported.

Set `debug_logging` to `true` to print policy evaluation counts and timing to
the terminal. Configuration errors and unexpected exceptions are always
printed, regardless of this setting.

## Rules

### Age

```json
{"type": "age", "days": 365, "from": "first_review"}
```

Age uses elapsed 24-hour periods. `first_review` is the earliest review-log entry with a genuine answer rating. Cards without such history do not match. `card_created` uses the creation timestamp embedded in the card ID.

### Current interval

```json
{"type": "interval", "days": 180}
```

Matches cards whose current Anki interval is at least as large as `days`. New cards normally have an interval of zero and do not match.

### Card state

```json
{"type": "card_state", "state": "new"}
```

Matches cards in the selected Anki state: `new`, `learning`, `review`, or
`relearning`. This describes the card's current Anki state, not whether it has
ever been studied.

### Study status

```json
{"type": "study_status", "status": "never_studied"}
```

`never_studied` matches cards with no genuine answer entry in Anki's review
log. Unlike the `new` card state, it does not match a previously studied card
that was later reset to New.

### Combining conditions

```json
{
  "type": "any",
  "rules": [
    {"type": "age", "days": 365, "from": "first_review"},
    {"type": "interval", "days": 180}
  ]
}
```

Use `any` for OR and `all` for AND. Groups must contain at least one condition.
Composition is deliberately limited to one flat group: every child must be a
single age, interval, card-state, or study-status condition. Nested AND/OR
groups are rejected.

For example, the conditions for a stale-new-card policy are:

```json
{
  "type": "all",
  "rules": [
    {"type": "age", "days": 30, "from": "card_created"},
    {"type": "study_status", "status": "never_studied"}
  ]
}
```

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
