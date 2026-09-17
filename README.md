# Card Janitor

Configurable policy-based cleanup for Anki cards.

Applies cleanup policies according to deck scope, age, interval, card state,
review history, note tags, or suspension state, either on demand or automatically
on daily, open, or sync triggers.

> [!WARNING]
> Card Janitor can make destructive collection changes, including permanently
> deleting cards. Back up your collection before use. Begin with on-demand
> cleanup and no automatic triggers, inspect matching cards with **Browse**,
> and verify Anki's undo behavior
> before enabling automatic cleanup. You use this add-on at your own risk; its
> author accepts no responsibility or liability for collection damage or data loss.

> [!CAUTION]
> Card Janitor is pre-1.0 software. Its policy configuration format and behavior
> may change without warning between releases.

![Card Janitor policy manager](./assets/banner.png)

## Installation

### Manual Installation

1. Download `card-janitor.ankiaddon` from the
   [latest GitHub release](https://github.com/zarnthyr/card-janitor/releases/latest).
2. Double-click the file, or open Anki and choose:

```text
Tools → Add-ons → Install from file...
```

3. Restart Anki if prompted.

### Install From Source

```bash
git clone https://github.com/zarnthyr/card-janitor.git
cd card-janitor
uv sync
make build
```

Then install `card-janitor.ankiaddon` from Anki's add-ons screen or by double clicking it.

## Policies

Each policy has four parts:

* Triggers — when to apply the policy automatically; None keeps it manual-only
* Scope — deck selections, optionally restricted to selected note types
* Conditions — card properties, note tags, or sibling suspension/review history, combined with AND/OR
* Actions — change tags, suspend or unsuspend, move, or delete the qualifying cards

### Scope

Scope limits a policy to one or more decks. It can optionally include their
subdecks and cards that are already suspended. Filtered-deck cards are excluded.
The compact deck selector opens a collapsible tree. Each deck can include all
current and future descendants, only itself, or nothing. Partial checks indicate
an exact selection or a mixed branch.
The note-type selector defaults to **All note types**, including types created
later. Selecting specific types restricts the cards matched within those decks.
The manager's Scope column shows note-type restrictions when present; its
tooltip lists every selected type.
Choose **All decks** at the tree root to include every current and future deck.

### Conditions

> [!WARNING]
> Card-creation age is derived from the timestamp encoded in Anki's card ID. Imported
> cards commonly retain the original creator's timestamp; it is **not** the date the
> card was imported into your collection. A creation-age policy can therefore match an
> entire premade deck immediately. Use this condition only for cards whose provenance
> you understand, and preview it with on-demand cleanup before enabling automatic
> actions.

| Condition | What it matches | Important detail |
| --- | --- | --- |
| All cards | Every card allowed by the selected scope | Must be the policy's only condition |
| Age since first review | Whole days since the card's first genuine answer | Cards without review history do not match |
| Age since creation | Whole days since the card's original creation timestamp | Imported cards may retain much older creation dates |
| Current interval | The card's current scheduled interval in days | Supports greater than, at least, exactly, at most, and less than comparisons |
| Card state | New, Learning, Review, or Relearning cards | One or more states can be selected |
| Review history | Whether the card has ever received a genuine answer | History remains after a studied card is reset to New |
| Note tags | Notes containing any, all, or none of the selected tags | Tags are shared by sibling cards |
| Suspension state | Suspended or non-suspended cards | Matching suspended cards also requires the scope option |
| Sibling suspension | All, any, or none of a note's cards are suspended | Checks every card of the note, including cards outside scope |
| Sibling review history | None, any, or all of a note's cards have been studied | Checks every card of the note, including cards outside scope |

A policy can require **all** conditions to match (AND), or allow **any** condition
to match (OR). Nested combinations such as `A AND (B OR C)` are not currently
supported.
Sibling conditions include the matching card itself, plus suspended, buried,
and filtered siblings. Use **Sibling review history → none studied** when
deleting notes that must be completely unstudied, rather than checking only
the matching card's review history.

### Actions

Matching cards can be:

* given, stripped of, or assigned an exact set of tags — tags belong to notes, so sibling cards share them
* suspended or unsuspended
* moved to another existing deck
* deleted individually, or deleted together with their complete note and every sibling card — deletion must be the policy's only action

Choose **Cards** to act on matching cards, or **Notes** to suspend, unsuspend,
move, or delete all cards belonging to matching notes. Notes actions can affect
siblings outside the selected scope; counts and **Browse** include the cards
that require an action.

Compatible actions from overlapping policies are combined. Cards are skipped
and reported when policies specify conflicting move destinations or combine
deletion with another action.
Click the skipped-card summary to inspect involved policies and conflict
reasons. Cleanup totals exclude conflicts; **Browse** includes them so you can
inspect all candidates. The conflict window's **Browse** shows only skipped cards.

### Example policies

Card Janitor can, for example:

* delete cards that remain unstudied 30 days after their original creation
* suspend cards one year after their first review
* move mature cards to a retirement deck once their interval reaches 365 days
* delete cards that Anki has tagged as leeches and suspended

Use **Browse** in the policy manager or editor to inspect the cards a policy
would currently clean up. The editor evaluates its current unsaved settings.

## Automatic triggers

All policies can be applied manually. Choose automatic triggers in the policy
editor's **Trigger** selector to also apply them without confirmation. **None** means
manual-only:

| Trigger | Behavior |
| --- | --- |
| Daily | Once per Anki day, on profile open or day change |
| On open | Every profile open, including switching profiles |
| On sync | After opening/manual sync, excluding closing sync |

Opening cleanup waits for opening auto-sync. On open and On sync triggers
are combined into one run when they coincide. Failed or cancelled sync attempts
still evaluate the local collection. Closing sync does not trigger cleanup;
it uploads any cleanup changes already made as usual. Newly matching cards
wait until the next configured event. Cleanup never starts another sync;
changes made after sync are uploaded on the next sync. Daily limits are per
policy and do not limit other triggers. Completion notifications can be disabled
independently and wait for an existing notification to disappear.

## Configuration

Open the policy manager:

```text
Tools → Card Janitor…
Card Janitor → Add… or Edit…
```

The add-on ships with no policies, so installing it cannot modify a collection. Begin with an on-demand tag-and-suspend policy and open **Card Janitor…** to preview its results. The dashboard can open candidates in Anki's Browser or apply the configured actions.

Policies can be created and repaired in the manager.
Use **Duplicate…** to start a new policy from an existing one. Inside Add/Edit,
**Edit as JSON…** edits that individual policy's unsaved settings; **Apply**
updates the unsaved form, while **Cancel/Escape** returns to the unchanged form.
Only the form's **Save** commits the policy. Browse also
previews unsaved JSON. Internal IDs are managed automatically. Closing a
changed policy editor asks before discarding edits.

**Settings…** controls automatic cleanup, notifications and debug logging.
Turn off **Enable automatic cleanup** to pause all triggers without changing
policies; manual cleanup remains available. The manager's
**Edit as JSON…** opens the current
collection's policies for advanced editing.

The manager shows **Last cleanup** for the most recent manual or automatic
cleanup. Click it for the time, affected-card count, skipped conflicts and any
failure, along with the policy names and initiating triggers captured at that
time. Click a policy name to edit it if the policy still exists.
This result is stored locally per profile and does not sync; Undo
does not change the recorded outcome.

Policy definitions are stored in the current collection and sync with it, so
each profile has its own policies. Automatic cleanup, notification and debug settings are shared
across profiles on the same Anki installation. Automatic cleanup is tracked
separately for each profile.

Automatic deletion is supported but is never configured by default. It requires
an explicit `delete_card` or `delete_note` action with automatic triggers and
applies those actions without confirmation.

See [policies.md](./docs/policies.md) for the complete policy schema and examples.

## Known Limitations

* Tags belong to notes in Anki, so tagging a qualifying card tags its note and any sibling cards
* First-review age cannot recover review history that was deleted or omitted during import
* Anki does not store a reliable per-card timestamp for when a card was imported into the current collection
* Decks are configured by name, so renamed or missing decks cause that policy to fail closed
* Notification and debug settings are shared across Anki profiles on the same installation

## Development

See [development.md](./docs/development.md) for setup, testing, and packaging notes.

## Attribution

Anki:
Ankitects Pty Ltd and contributors

## License

GNU AGPL v3 or later.\
See [LICENSE](./LICENSE).

## Info

Repository: https://github.com/zarnthyr/card-janitor

Issue tracker: https://github.com/zarnthyr/card-janitor/issues
