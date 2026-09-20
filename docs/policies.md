# Policy JSON

Add and edit policies in the Card Janitor window. Choose **Edit as JSON** there
to edit the current collection's policy data directly.

**Review copied or shared policies before saving.** They may enable automatic
deletion without confirmation. Check their scope, conditions, actions, and triggers;
changing deck names alone is not enough. Remove `triggers` from copied policies
and inspect affected cards with **Browse** before adding automatic triggers.

Policies are stored in the current Anki collection and sync with it.
Each policy requires `id`, `name`, and a non-empty `actions` array. Optional
composition fields describe only active configuration: omit `triggers` for
manual-only execution, omit `scope` for unrestricted scope, and omit both
`match` and `conditions` for All cards. Active selectors, conditions, actions,
and triggers keep their configuration fields explicit.

Invalid policies remain visible in Card Janitor and can be repaired with
**Edit** or **Edit as JSON**. A structural error in the stored collection policy
list prevents it from being loaded safely. A collection-context error, such as
a missing deck or scheduler mismatch, blocks that policy when it is selected or
due; an unrelated unchecked or inactive policy does not block the run.

The ordinary policy editor checks deck, note-type, card-type and move-deck
references when saving. It also rejects FSRS conditions while FSRS is disabled,
and SM-2 ease conditions while FSRS is enabled. Advanced JSON editing can still
represent an invalid policy; the manager keeps it visible and shows the specific
reason without allowing cleanup.
Opening an affected policy shows its saved errors in the editor; Save checks the
edited policy again and refuses to save while a problem remains.
Most draft validation is deferred until Save, the explicit full validation
point. Existing saved errors and Save errors are shown in the relevant General, Scope,
Conditions, or Actions section, with one combined panel directly below that
section's help text. Multiple messages begin with **Fix the following:** and a
short list. Only errors that do not belong to one section appear at the top.
Save reports these persistent inline errors without also opening a redundant
warning dialog. It gathers collection-context errors that can be determined
from the draft even when unrelated required fields are still incomplete.
Browse and Preview remain available while editing. Clicking either validates
only the inputs that operation needs and shows a transient explanation if it
cannot proceed, without scanning cards first.
The form immediately shows scheduler errors when FSRS or SM-2 conditions are
selected: FSRS conditions need collection-wide FSRS enabled, SM-2 conditions
need it disabled, and the two condition families cannot be combined. This
feedback does not depend on choosing a scope. It is GUI-only collection
validation: structurally valid JSON can still be imported or stored when its
scheduler requirements do not match the current collection, then repaired in
the editor.
In both JSON editors, malformed or schema-invalid JSON remains in the JSON view
with its full error shown in a visible top panel. Structurally valid individual
JSON still applies to the form, even when collection-specific references or
scheduler requirements are invalid; after applying it, those errors appear in
the appropriate form section. Bulk JSON remains able to store structurally valid
policies with collection-specific errors so they can be repaired in the manager.

Deck names are resolved when a policy is evaluated. A missing or filtered
destination deck is an error.
When enabled in Settings, Card Janitor also checks references in automatic
policies after the opening sync attempt and warns if a policy needs attention,
even when its trigger is not due. This lightweight check does not scan cards or
replace the Last cleanup result. Rows with invalid definitions or references
are marked with a warning symbol and error-colour tint in the manager.

## Safety and undo

Card Janitor can permanently delete cards. Back up your collection before use,
begin in on-demand cleanup, and inspect matching cards with **Browse** before
cleaning up. Automatic triggers apply policies without confirmation.

Card Janitor participates in Anki's normal collection Undo system. A successful
cleanup that changes cards is grouped into one Card Janitor entry. If an
unexpected later operation fails, earlier operations may remain applied and
Card Janitor reports that Anki Undo should be used; an unusual Undo-grouping
failure can require more than one Undo.
Undo restores collection changes but does not reset Daily's completion record.
To retry an undone cleanup on the same day, apply it manually.
Undo availability follows Anki's normal history and is not guaranteed after
arbitrary later collection operations.
Policy configuration changes do not create collection undo entries. Removing a
policy therefore requires confirmation but cannot be undone with Anki's Undo
command.

## Example

    {
      "policies": [
        {
          "id": "2f87a1d4-956b-4f3c-a80e-d53792a4761b",
          "name": "Retire Mature Cards",
          "scope": {
            "decks": [{"deck": "Mining", "include_subdecks": true}]
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

In JSON, an omitted `triggers` field means manual-only (**Trigger → None** in the
editor). Keep triggers omitted while testing. Card Janitor evaluates every configured policy
and shows its scope, conditions, actions, and the number of cards it would
clean up.
Every policy is included by default in the dashboard. The checkboxes affect
only the current cleanup. Add automatic triggers to also apply a policy without
approval. Collection changes are grouped into one Anki Undo entry.

Opening cleanup waits for Anki's opening collection sync attempt to finish when
automatic sync is enabled; otherwise it runs immediately. A failed or cancelled
sync leaves cleanup using the local collection. Cleanup changes reach other
devices on the next sync. Interrupted cleanup may apply some actions before
failing; use the named Anki Undo entry to revert completed changes.

The window remains open while you inspect cards. **Browse** opens the union
from all checked policies. Select a row and use **Edit** to change that policy;
**Refresh** recalculates the table. Applying a policy manually does not count as
that day's automatic cleanup.

**Browse** in the policy editor validates and evaluates the current form
without saving it, then opens every card matching its scope and conditions in
Anki's Browser. It does not require a policy name or actions.
**Preview…** beside **Save** opens **Cleanup Preview** with the merged changes from the editor's current
policy alone. Other policies are not included. A name is optional for preview;
scope, conditions and actions must still be usable. If Browse or Preview cannot
proceed, it shows a transient message without adding new persistent form errors.
Neither operation saves or applies the policy.
The title identifies the policy, or **Unnamed policy** if no name is entered.
The single-policy preview shows planned changes without the combined-policy
View selector. Only Card and Changes are shown by default; Reason appears only for
note-expansion explanations. New policies start with empty condition and action lists;
conditions must be chosen or explicitly set to **All cards**, and at least one
action is required before saving or previewing.

**Duplicate…** opens an editable copy of the selected valid policy with a new
internal ID. It is not saved until you click **Save**. Inside Add/Edit,
**Edit as JSON…** switches to a single-policy JSON editor, including incomplete
or unsaved settings. Paste one policy object, not a whole configuration.
**Apply** validates JSON, updates the unsaved form, and returns to it.
**Cancel/Escape** returns to the unchanged form without validation, asking
before discarding changed JSON. Only the form's **Save** persists the policy;
**Browse** and **Preview…** work in either view without saving.
Internal IDs are preserved for edited policies and generated for new ones,
even if pasted JSON contains another ID. Generated IDs are UUIDs rather than
name-derived labels, so renaming a policy does not change its identity and
independently shared policies are very unlikely to collide. Bulk JSON preserves
IDs and rejects duplicates; adding a shared policy through the individual editor
gives it a fresh ID. Closing a changed policy editor asks before discarding its
unsaved changes.

## Automatic triggers

The optional top-level `triggers` array configures automatic execution and must
be non-empty when present. Omit it for a manual-only policy; all policies remain
manually runnable regardless of their triggers. Each entry is an object with a `type`:

- `daily` — once per Anki day, checked on profile open and day change.
- `on_open` — each profile open, including switching profiles.
- `on_sync` — after opening/manual collection sync attempts finish, including
  failed or cancelled attempts, except when closing Anki and/or switching profiles. Media-only
  sync is not a trigger.

Daily is checked on opening and day change, not periodically throughout the day.

In the editor, **Trigger** opens a compact checkbox selector; selecting **None**
clears automatic triggers, and selecting any trigger deselects **None**.
The manager's **Trigger** column summarizes
selected triggers, and its tooltip explains only the current setting.

For example: `"triggers": [{"type": "on_open"}, {"type": "on_sync"}]`.
Any listed event can apply the policy. Duplicate types, unknown types, and unknown
trigger fields are rejected. Trigger objects allow future types to have their
own settings; no other trigger types are supported yet.

Opening events wait for opening sync and are combined with its On sync event;
each eligible policy is evaluated once in the shared conflict-handled run.
Daily limits only the Daily trigger; On open and On sync can apply the same
policy again that day. Any successful automatic cleanup of a policy with a
Daily trigger satisfies its daily limit; manual cleanups do not.
Daily completion is tracked per policy and profile, including runs with no
changes or only conflicts. Failed runs do not mark completion. Events arriving
during a run are coalesced for a subsequent run after success; failure clears
pending events rather than automatically retrying a partially completed run.
Cleanup never starts another sync.

Closing sync does not trigger cleanup or delay Anki's shutdown. It uploads
cleanup changes already made as usual. Cards that become eligible since the
last cleanup are handled by the next configured event. Changes made after
opening/manual sync are uploaded on a later sync, including closing sync.

## Scope

Omit `scope` to cover every current and future deck and every note/card type.
When `scope` is present, it must contain at least one real restriction. Use
`decks` for individual deck selections. For example:

    "scope": {"decks": [{"deck": "Mining", "include_subdecks": true}]}

`decks` must contain one or more selectors, each with `deck` and
`include_subdecks`. A recursive selector includes that deck and all current and
future descendants; an exact selector includes only that deck. Scope is the union
of these selectors.
Suspended and non-suspended cards are both eligible. Use a Suspension state
condition when a policy should include only one of those states.
Buried cards remain eligible because burial is temporary.

Use `note_types` alone to restrict note/card types while leaving decks unrestricted:

    "scope": {"note_types": [{"name": "Basic"}, {"name": "Cloze"}]}

Cards must satisfy both the deck selection and the note-type selection. Omit
`note_types` for **All note types**, including types created later. An explicit
list must be non-empty and contains only the named types; a missing or renamed
type is reported as a policy error. The compact selector opens a checklist.
The manager's Scope column shows a single selected type or a count when several
are selected; hover to see the full list. All note types remains implicit there.

Omitting `card_types` from a selected note type includes all its current and
future card types. To select exact card types, use a non-empty list:

    "note_types": [
      {"name": "Basic"},
      {"name": "Basic (and reversed card)", "card_types": ["Card 2"]}
    ]

Expand a note type in the compact selector to choose card types without adding
height to the main policy form. Explicit card-type lists exclude future
card types. A policy referring to a missing or renamed card type is marked as
invalid and is not applied until it is repaired.

Note-type and card-type filtering identifies triggering cards. Notes actions still affect
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

Omit both `match` and `conditions` to match every card allowed by the policy's
scope. Empty condition arrays, either matching field without the other, and the
obsolete `{"type": "all_cards"}` pseudo-condition are invalid.
In the editor, choose **All cards** in the **Match** selector; condition rows
are hidden while this option is selected. Choose AND or OR to use conditions.

### Age

    {"type": "age", "days": 365, "source": "first_review", "operator": "gte"}

Age uses completed 24-hour periods. `first_review` is the earliest review-log
entry with a genuine answer rating. Cards without such history do not match any
first-review-age comparison. `last_review` uses the latest genuine answer and
likewise excludes cards without review history. `card_created` uses the creation timestamp embedded
in the card ID.

> **Warning:** `card_created` does not mean "imported into this collection." Imported cards usually
> retain the source author's card IDs and creation timestamps. A newly imported premade
> deck may consequently be years old according to this condition and qualify on its
> first evaluation. Anki does not expose a reliable per-card local-import timestamp.

Use creation-age conditions only for cards whose provenance you understand. Keep the
policy manual-only (omit `triggers`) and inspect its matching cards before
adding automatic triggers. Card Janitor does not attempt to rewrite card IDs. If you use another
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

### Overdue, flags, and accumulated review history

    {"type": "overdue", "days": 30, "operator": "gte"}
    {"type": "card_flag", "flags": ["none", "red", "purple"]}
    {"type": "answer_count", "count": 100, "operator": "gte"}
    {"type": "correct_answer_count", "count": 80, "operator": "gte"}
    {"type": "correct_answer_rate", "percent": 70, "operator": "lt"}
    {"type": "lapse_count", "count": 8, "operator": "gte"}

`overdue` matches due Review cards and compares whole overdue days; cards due
today have value 0. Card flags are `none`, `red`, `orange`, `green`, `blue`,
`pink`, `turquoise`, and `purple`.

Answer counts use genuine review-log ratings 1 through 4. A correct answer is
any rating other than Again (ratings 2 through 4); manual/rescheduling entries
do not count. Correct-answer rate is `correct / all genuine answers * 100`, and
cards with no genuine answers do not match it. Lapse count uses Anki's
cumulative per-card lapse value.

### Scheduler metrics

    {"type": "fsrs_stability", "days": 90, "operator": "gte"}
    {"type": "fsrs_difficulty", "percent": 70, "operator": "lte"}
    {"type": "fsrs_retrievability", "percent": 60, "operator": "lt"}
    {"type": "sm2_ease", "percent": 250, "operator": "gte"}

FSRS conditions require FSRS to be enabled. Stability is measured in days;
difficulty is normalized from FSRS's 1-10 scale to 0-100%; retrievability is
Anki's current estimate on a 0-100% scale. Floating-point FSRS metrics support
`gt`, `gte`, `lte`, and `lt`, but not exact equality.

SM-2 ease is the card's ease factor as a percentage (for example, 250%). It is
available only while FSRS is disabled, avoiding a misleading mix of scheduling
models. The ordinary editor prevents saving scheduler-incompatible policies.
Collection-specific scheduler mismatches entered through JSON or caused by a
later scheduler change are not applied until their requirements are satisfied.

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
condition. Without a Suspension state condition, either state can match.

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
      "decks": [{"deck": "Mining", "include_subdecks": true}]
    },
    "match": "all",
    "conditions": [
      {"type": "tags", "operator": "contains_any", "tags": ["leech"]},
      {"type": "suspension", "operator": "is_suspended"}
    ]

Pair this with `delete_card` to remove only the suspended leech card. Use
`delete_note` only when you also intend to remove the note and every sibling
card it generates. Automatic policies are applied on their configured triggers,
not at the instant Anki adds the leech tag.

### Combining conditions

    "match": "any",
    "conditions": [
      {"type": "age", "days": 365, "source": "first_review", "operator": "gte"},
      {"type": "interval", "days": 180, "operator": "gte"}
    ]

Use `match: "any"` for OR and `match: "all"` for AND. `conditions` must contain
at least one condition, and `match` and `conditions` must occur together. Nested
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

- `{"type": "add_tags", "tags": ["retired"]}` adds one or more tags to the note.
- `{"type": "remove_tags", "tags": ["leech"]}` removes one or more tags from the note.
- `{"type": "replace_tags", "tags": ["reviewed"]}` replaces the note's complete tag set. An empty array clears all tags.
- `{"type": "suspend"}` suspends the qualifying card.
- `{"type": "unsuspend"}` unsuspends the qualifying card.
- `{"type": "move", "deck": "Retired"}` moves the card to an existing normal deck.
- `{"type": "suspend_note"}` suspends all cards of each matching note.
- `{"type": "unsuspend_note"}` unsuspends all cards of each matching note.
- `{"type": "move_note", "deck": "Retired"}` moves all cards of each matching note to an existing normal deck.
- `{"type": "delete_card"}` deletes the card and removes its note only if no cards remain.
- `{"type": "delete_note"}` deletes the matching card's note and every card generated from it, including sibling cards outside the selected deck scope.
- `{"type": "set_flag", "flag": "purple"}` sets the qualifying card's flag. Supported colours are red, orange, green, blue, pink, turquoise, and purple.
- `{"type": "clear_flag"}` clears the qualifying card's flag.

All tag actions affect notes, so sibling cards share their result. Replacing tags is
destructive and is highlighted in the policy editor. Overlapping policies are
skipped when their intentions conflict, including contradictory tag changes,
incompatible replacements, suspend versus unsuspend, different flags or moves,
and note deletion combined with other actions.

`delete_card` and `delete_note` must each be the policy's only action. Either can
use automatic triggers, but automatic deletion happens without confirmation.
Collection-wide deletion of All cards across All decks is invalid. A delete
policy must include at least one deck, note/card-type, or matching-condition
restriction.
On-demand deletion is shown in the dashboard before actions are applied. The shipped
configuration contains no policies, and the example omits `triggers`
with reversible tag, suspend, and move actions.

For **Notes** actions, scope and conditions identify the triggering cards. The
action then applies to every card of their notes, including siblings outside
the selected decks. Filtered cards cannot trigger a policy, but can be affected
as siblings of a matching note.

Manager policy counts and manager **Browse** include sibling cards that require
a note action, including those deleted with a note. An individual editor's
**Browse** instead shows only the in-scope cards matching its current scope and
conditions, because it deliberately ignores the draft action. Individual
**Preview…** includes the action-expanded siblings and labels them **Included by
note action**. An already satisfied triggering card does not prevent a note
action from updating its siblings. If a sibling has a conflicting policy, the
note-wide operation is skipped for the whole note.
If new cards would be affected between preview and applying actions, the whole note
is skipped rather than expanding the approved operation.

## Last cleanup

The dashboard's **Last cleanup** link shows the latest manual or automatic
result, including its time, policy names, initiating triggers, affected-card
count, skipped conflicts and any failure. It is stored locally per profile,
not in policy JSON, and does not sync. Completed checks with no changes are
recorded too; events with no eligible policies leave the previous result intact.
Undo does not update the recorded result.
Policy names link to the current policy editor when the policy still exists;
deleted policies remain listed without a link.

## Multiple profiles

Policy definitions are stored in the current collection, so each profile has its
own policies and they follow that collection through AnkiWeb sync. Add-on-wide
settings such as automatic cleanup, notifications and debug logging remain shared by profiles on the
same Anki installation. Daily completion is tracked separately for each policy
and profile. A configured deck that no longer exists in its collection is
reported as a policy error.

## Overlapping policies

Compatible actions are merged and deduplicated during manual and automatic
cleanup. Cards are skipped when policies express incompatible intentions, such
as different moves, suspend versus unsuspend, different flags, contradictory or
incompatible tag changes, or note deletion combined with another action.

Click **Preview…** beside **Clean Up** on the dashboard to open **Cleanup Preview**
at **Planned changes**. Switch between **All affected**,
**Planned changes**, **Overlapping policies**, and **Conflicts**. Each row shows
the card, targeting policies, actual merged changes, status, and reason. Satisfied
actions are omitted from planned changes; skipped cards show no changes and
explain the competing actions. Policy names can include satisfied intentions
that still matter for conflict detection. Every conflict is also classified as
an overlap. Note deletion is labelled explicitly;
expanded siblings are marked **Included by note action**.

Select rows and use **Browse** to inspect those cards, or clear the
selection and use **Browse** for all cards in the current view. The preview is
read-only and reflects the current evaluation, not a guarantee that a later
cleanup applies unchanged card state. Card state is evaluated again before
mutation; if a selected saved policy definition changed, cleanup is cancelled
instead of applying the stale definition. Refresh recalculates the preview;
changing checked policies updates it. An editor preview closes if its unsaved
settings change.
The main **Browse** button still includes all
candidate cards from checked policies, including conflicts; cleanup totals
exclude conflicting cards. Already-satisfied actions can still conflict with
another policy's intended changes. A conflict affecting a note-wide action
skips all affected cards of that note together.

Policies are matched as a batch against the pre-cleanup collection state. An
action from one policy does not make another policy newly match during that same
cleanup. Policies intentionally depending on another policy's output can match
on a subsequent cleanup.
