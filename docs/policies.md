# Policy JSON

Add and edit policies in the Card Janitor window. Choose **Edit as JSON** there
to edit the current collection's policy data directly.

**Review copied or shared policies before saving.** They may enable automatic
deletion without confirmation. Check their scope, conditions, actions, and mode;
changing deck names alone is not enough. Set copied policies to
`"mode": "on_demand"` and inspect affected cards with **Browse** before enabling
`"mode": "automatic"`.

Policies are stored in the current Anki collection and sync with it.

If any policy is invalid, Card Janitor will not run cleanup until the problem is
fixed. Invalid policies remain visible in Card Janitor and can be repaired with
**Edit** or **Edit as JSON**.

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
      "policies": [
        {
          "id": "retire-mature-cards",
          "name": "Retire Mature Cards",
          "mode": "on_demand",
          "scope": {
            "decks": [{"deck": "Mining", "include_subdecks": true}],
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

Opening cleanup waits for Anki's opening collection sync attempt to finish when
automatic sync is enabled; otherwise it runs immediately. A failed or cancelled
sync leaves cleanup using the local collection. Cleanup changes reach other
devices on the next sync. Interrupted cleanup may apply some actions before
failing; use the named Anki Undo entry to revert completed changes.

The window remains open while you inspect cards. **Browse** opens the union
from all checked policies. Select a row and use **Edit** to change that policy;
**Refresh** recalculates the table. Running a policy on demand does not count as
that day's automatic cleanup.

**Browse** in the policy editor validates and evaluates the current form without
saving it, then opens the cards that would require an action in Anki's Browser.

**Duplicate…** opens an editable copy of the selected valid policy with a new
internal ID. It is not saved until you click **Save**. Inside Add/Edit,
**Edit as JSON…** switches to a single-policy JSON editor, including incomplete
or unsaved settings. Paste one policy object, not a whole configuration.
**Apply** validates JSON, updates the unsaved form, and returns to it.
**Cancel/Escape** returns to the unchanged form without validation, asking
before discarding changed JSON. Only the form's **Save** persists the policy;
**Browse** previews either view without saving.
Internal IDs are preserved for edited policies and generated for new ones,
even if pasted JSON contains another ID. Closing a changed policy editor asks
before discarding its unsaved changes.

Policy modes are:

- `on_demand` — runs only when you start cleanup from Card Janitor
- `automatic` — runs once per day without confirmation and can also be run on demand

Automatic cleanup runs when a profile opens if it has not yet run that day. It
also runs when Anki's day changes while the application remains open. Use Card
Janitor whenever you want to run policies on demand.

## Scope

Use `"all_decks": true` to cover every current and future deck, or `decks` for
individual selections. These alternatives cannot be combined. For example:

    "scope": {"all_decks": true, "include_suspended": false}

`decks` must contain one or more selectors, each with `deck` and
`include_subdecks`. A recursive selector includes that deck and all current and
future descendants; an exact selector includes only that deck. Scope is the union
of these selectors.
Suspended cards are excluded by default.
Buried cards remain eligible because burial is temporary.

Optionally add `note_types` to restrict scope to one or more note-type names:

    "scope": {"all_decks": true, "note_types": ["Basic", "Cloze"]}

Cards must satisfy both the deck selection and the note-type selection. Omit
`note_types` for **All note types**, including types created later. An explicit
list must be non-empty and contains only the named types; a missing or renamed
type is reported as a policy error. The compact selector opens a checklist.
The manager's Scope column shows a single selected type or a count when several
are selected; hover to see the full list. All note types remains implicit there.

Note-type filtering identifies triggering notes. Notes actions still affect
their sibling cards outside the selected decks; all siblings share that note type.

The editor's compact deck selector opens a collapsible tree. Clicking a deck
cycles through deck plus descendants, deck only, and unselected. Partial checks
indicate an exact deck or a mixed branch; expand the branch to inspect it.
New sibling decks under a partially selected parent are excluded unless that
parent is recursively selected.
The expanded **All decks** root selects everything when unchecked or partial,
and clears everything when checked. Changing an individual deck under a full
selection leaves a partial selection of current decks; future top-level decks
are then excluded. Selecting all current branches individually does not enable
collection-wide coverage of future top-level decks.

## Conditions

The editor's condition picker groups card properties under **Cards** and shared
note properties, such as tags, under **Notes**. Group headings are not selectable.

### All cards

    {"type": "all_cards"}

Matches every card allowed by the policy's scope. `all_cards` must be the
policy's only condition.
In the editor, choose **All cards** in the **Match** selector; condition rows
are hidden while this option is selected. Choose AND or OR to use conditions.

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

Both age and interval conditions support whole-number day values from 0 through
100,000, matching the editor's supported range.

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

### Note tags

Each tag-array entry must be one tag, without whitespace or commas inside its
name.

    {"type": "tags", "operator": "contains_any", "tags": ["leech"]}

Tag matching is case-insensitive. Use `contains_any`, `contains_all`, or
`contains_none` to control how multiple tags are matched. Tags belong to notes,
so every sibling card generated from a note sees the same tags.

### Suspension state

    {"type": "suspension", "operator": "is_suspended"}

Use `is_suspended` or `is_not_suspended`. A suspended card retains its New,
Learning, Review, or Relearning state, so suspension is separate from the card-state
condition. A policy that uses `is_suspended` must also set
`scope.include_suspended` to `true`.

### Sibling suspension and review history

    {"type": "sibling_suspension", "operator": "all"}
    {"type": "sibling_review_history", "operator": "none"}

Both conditions accept `all`, `any`, or `none`. Sibling suspension checks how
many cards are suspended; sibling review history checks how many have a genuine
answer in the review log. Review history still counts if a studied card is reset
to New; manual scheduling entries do not count as studying.

These are in the editor's **Notes** group. They inspect **every card of the
note**, including the triggering card itself and siblings outside scope, even
when those siblings are suspended, buried, or in filtered decks. Scope still
determines which cards can trigger the policy. For example, `any` suspended can
match an active in-scope card with a suspended out-of-scope sibling.

`all` suspended requires `scope.include_suspended: true`, since the triggering
card must also be suspended. The editor enables that setting automatically.

For deleting completely unstudied notes, combine `sibling_review_history` with
`operator: "none"` and `delete_note`. A card-level `review_history: not_exists`
condition alone could also delete that card's studied siblings.

The sibling-condition banner explains that the checks extend outside scope;
the manager's Conditions column summarizes the selected sibling check.

### Leech example

Anki normally tags a leech's note and can suspend the particular card.
Combining both conditions avoids matching non-suspended sibling cards that share the
note's `leech` tag:

    "scope": {
      "decks": [{"deck": "Mining", "include_subdecks": true}],
      "include_suspended": true
    },
    "match": "all",
    "conditions": [
      {"type": "tags", "operator": "contains_any", "tags": ["leech"]},
      {"type": "suspension", "operator": "is_suspended"}
    ]

Pair this with `delete_card` to remove only the suspended leech card. Use
`delete_note` only when you also intend to remove the note and every sibling
card it generates. Automatic policies run during Card Janitor's daily cleanup,
not at the instant Anki adds the leech tag.

### Combining conditions

    "match": "any",
    "conditions": [
      {"type": "age", "days": 365, "source": "first_review", "operator": "gte"},
      {"type": "interval", "days": 180, "operator": "gte"}
    ]

Use `match: "any"` for OR and `match: "all"` for AND. `conditions` must contain
at least one condition. `all_cards` must appear alone; the other condition types
can be combined. Nested AND/OR groups are not supported.

For example, the conditions for a stale-new-card policy are:

    "match": "all",
    "conditions": [
      {"type": "age", "days": 30, "source": "card_created", "operator": "gte"},
      {"type": "review_history", "operator": "not_exists"}
    ]

Numeric conditions support `gt`, `gte`, `eq`, `lte`, and `lt`. These correspond
to greater than, at least, exactly, at most, and less than.

## Actions

- `{"type": "tag", "tags": ["retired"]}` adds one or more tags to the note. `add_tags` is also accepted as an alias.
- `{"type": "remove_tags", "tags": ["leech"]}` removes one or more tags from the note.
- `{"type": "replace_tags", "tags": ["reviewed"]}` replaces the note's complete tag set. An empty array clears all tags.
- `{"type": "suspend"}` suspends the qualifying card.
- `{"type": "unsuspend"}` unsuspends the qualifying card. The policy scope must include suspended cards.
- `{"type": "move", "deck": "Retired"}` moves the card to an existing normal deck.
- `{"type": "suspend_note"}` suspends all cards of each matching note.
- `{"type": "unsuspend_note"}` unsuspends all cards of each matching note.
- `{"type": "move_note", "deck": "Retired"}` moves all cards of each matching note to an existing normal deck.
- `{"type": "delete_card"}` deletes the card and removes its note only if no cards remain.
- `{"type": "delete_note"}` deletes the matching card's note and every card generated from it, including sibling cards outside the selected deck scope.

All tag actions affect notes, so sibling cards share their result. Replacing tags is
destructive and is highlighted in the policy editor. Overlapping policies are skipped
as conflicts when they add and remove the same tag, replace a note's tags in
incompatible ways, or both suspend and unsuspend a card.

`delete_card` and `delete_note` must each be the policy's only action. Either can
use `mode: "automatic"`, but automatic deletion runs without confirmation.
On-demand deletion is shown in the dashboard before execution. The shipped
configuration contains no policies, and the example uses `mode: "on_demand"`
with reversible tag, suspend, and move actions.

For **Notes** actions, scope and conditions identify the triggering cards. The
action then applies to every card of their notes, including siblings outside
the selected decks or excluded by the scope's suspension setting. Filtered
cards cannot trigger a policy, but can be affected as siblings of a matching
note. Note unsuspension does not require including suspended cards in scope
unless its triggering cards are suspended.

Policy counts and **Browse** include sibling cards that require an action,
including those deleted with a note. An already satisfied triggering card does
not prevent a note action from updating its siblings. If a sibling has a
conflicting policy, the note-wide operation is skipped for the whole note.
If new cards would be affected between preview and execution, the whole note
is skipped rather than expanding the approved operation.

## Multiple profiles

Policy definitions are stored in the current collection, so each profile has its
own policies and they follow that collection through AnkiWeb sync. Add-on-wide
settings such as notifications and debug logging remain shared by profiles on the
same Anki installation. The daily automatic-run marker is tracked separately for
each profile. A configured deck that no longer exists in its collection is
reported as a policy error.

## Overlapping policies

Compatible actions are merged and deduplicated during on-demand and automatic execution. Cards with conflicting move destinations, or a deletion combined with another policy's action, are skipped and reported.

When checked policies conflict, click the dashboard's skipped-card summary to
see the affected card IDs, involved policies, their actions, and reasons.
Select rows and use **Browse selected** to inspect just those skipped cards,
or clear the selection and use **Browse** for all skipped cards. The main
**Browse** button still includes all
candidate cards from checked policies, including conflicts; cleanup totals
exclude conflicting cards. Already-satisfied actions can still conflict with
another policy's intended changes. A conflict affecting a note-wide action
skips all affected cards of that note together.
