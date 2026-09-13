# Configuration

Card Retirement is configured as JSON through **Tools → Card Retirement → Settings…**.

Invalid configuration fails closed: if any validation error is present, no manual or automatic policy runs. Deck names are resolved when a policy is evaluated, and a missing or filtered move destination is an error.

## Mining example

```json
{
  "config_version": 1,
  "automatic_schedule": "daily",
  "notify_after_automatic_retirement": true,
  "debug_logging": false,
  "policies": [
    {
      "id": "mining-retirement",
      "name": "Mining retirement",
      "enabled": true,
      "mode": "manual",
      "scope": {
        "decks": ["Mining"],
        "include_subdecks": true,
        "include_suspended": false,
        "include_filtered_decks": false
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

Keep `mode` set to `manual` while testing. **Find Cards to Retire…** opens and
selects candidates in Anki's Browser. **Cards → Retire Selected Cards…** applies
the configured actions to the selection without reapplying the policy's scope
or rule. Change the mode to `automatic` to apply scope, rule, and actions on the
configured schedule. Like Anki's own Browser deletion, retirement does not ask
for confirmation and is recorded in Anki's undo history.

`automatic_schedule` supports:

- `profile_open` — run whenever the profile opens
- `daily` — run at most once per Anki day, on profile opening or day change
- `profile_open_and_daily` — run on every profile opening and day change

Set `notify_after_automatic_retirement` to `false` to suppress successful
automatic-retirement summaries. No summary is shown when no cards were changed.
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

### New cards

```json
{"type": "new"}
```

Matches cards whose Anki card type is still new. Combine this with card-creation
age to retire cards that were added but not learned within a desired period.

### Compound rules

```json
{
  "type": "any",
  "rules": [
    {"type": "age", "days": 365, "from": "first_review"},
    {"type": "interval", "days": 180}
  ]
}
```

Use `any` for OR and `all` for AND. Groups must contain at least one rule.

For example, a stale-new-card rule is:

```json
{
  "type": "all",
  "rules": [
    {"type": "age", "days": 30, "from": "card_created"},
    {"type": "new"}
  ]
}
```

## Actions

- `{"type": "tag", "tag": "retired"}` tags the note. Anki has no card-level tags, so sibling cards share it.
- `{"type": "suspend"}` suspends the qualifying card.
- `{"type": "move", "deck": "Retired"}` moves the card to an existing normal deck.
- `{"type": "delete_card"}` deletes the card and removes its note only if no cards remain.

`delete_card` must be the policy's only action. It can use `mode: "automatic"`, but automatic deletion runs without confirmation. The shipped configuration contains no policies, and the Mining example uses `mode: "manual"` with tag and suspend actions.

## Scope behavior

`decks` must contain one or more exact deck names. `include_subdecks` includes all descendants of each named deck. Suspended cards and cards temporarily in filtered decks are excluded by default. Buried cards remain eligible because burial is temporary.

## Overlapping policies

Compatible actions are merged and deduplicated during automatic execution. Cards with conflicting move destinations, or a deletion combined with another policy's action, are skipped and reported.
