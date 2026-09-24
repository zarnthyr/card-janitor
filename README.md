# Card Janitor

Configurable policy-based cleanup for Anki cards.

Card Janitor performs periodic collection housekeeping. Create policies that
find cards based on their scope, age, scheduling state, review history, tags,
and other properties, then tag, suspend, move, flag, or delete them.

Policies can be run manually or automatically on daily, profile-open, or sync
triggers. Card Janitor deliberately does not react to individual edits or
reviews, keeping cleanup predictable and separate from normal studying.

> [!WARNING]
> Card Janitor can make destructive collection changes, including permanently
> deleting cards. Back up your collection and preview the effects of your
> policies before executing them. You use Card Janitor at your own risk; its
> authors are not responsible for collection damage or data loss.

![Card Janitor policy manager](./assets/banner.png)

## What can it do?

For example, Card Janitor can:

- delete cards that remain unstudied 30 days after their original creation
- suspend cards one year after their first review
- move mature cards to a retirement deck once their interval reaches 365 days
- delete cards that Anki has tagged as leeches and suspended

These are only examples. Card Janitor does not ship with any policies by default.

## Installation

1. Download `card-janitor.ankiaddon` from the
   [latest GitHub release](https://github.com/zarnthyr/card-janitor/releases/latest).
2. Double-click the file, or in Anki choose:

   ```text
   Tools → Add-ons → Install from file...
   ```

3. Restart Anki if prompted.

Card Janitor is pre-1.0 software. Its policy configuration format and behavior
may change between releases.

## Policies

A policy has four parts:

- **Triggers** — when the policy can run automatically
- **Scope** — which decks, note types, and card types it can operate on
- **Conditions** — which cards within that scope qualify
- **Actions** — what happens to qualifying cards

For example, a policy could apply to cards in a `Mining` deck and its subdecks,
match cards that are at least 30 days old and have never been studied, and
delete the matching cards.

### Triggers

Every policy can be run manually. Automatic triggers are optional:

- **Daily** — once per Anki day
- **On open** — when a profile is opened
- **On sync** — after an opening or manual collection sync

Selecting **None** keeps a policy manual-only.

Automatic cleanup runs without confirmation. It can be disabled globally in
Card Janitor's settings without removing triggers from individual policies.

### Scope

Scope limits where a policy can operate.

Policies can be restricted by:

- deck and subdeck
- note type
- card type

Leaving a dimension unrestricted includes current and future decks or types in
that dimension. The deck selector supports whole subdeck branches or exact
decks. A selected note type can include all of its card types or an exact set.

Suspended and non-suspended cards are both eligible by default. Filtered-deck
cards are excluded.

### Conditions

Conditions determine which cards within the scope match a policy.

Available conditions include:

- age since creation, first review, or last review
- current interval and days overdue
- card state and flag
- review history and answer counts
- correct-answer rate and lapse count
- FSRS stability, difficulty, and retrievability
- SM-2 ease
- note tags
- suspension state
- sibling suspension and review history

A policy can operate on all cards within its scope without conditions. When
conditions are used, a policy can require **all** of them (AND) or **any** of
them (OR). For more complex rules, conditions can be grouped one level deep to
combine AND and OR, such as `A AND (B OR C)`.

> [!WARNING]
> **Age since creation** uses the timestamp stored in Anki's card ID. Imported
> cards can retain the original creator's timestamp, so a newly imported deck
> may appear to be years old. Preview creation-age policies before using them.

### Actions

Matching cards can be:

- given, stripped of, or assigned an exact set of tags
- suspended or unsuspended
- moved to another deck
- assigned a flag or have their flag cleared
- deleted individually
- deleted together with their complete note

Some actions can operate on entire notes rather than only the matching cards.
Because notes can have multiple cards, a note action may affect sibling cards
outside the policy's scope. For whole-note suspension, unsuspension, movement,
or deletion, manager Browse and Preview include siblings that need the action.
Browse inside an individual policy editor shows only the in-scope cards that
trigger the policy, so use Preview to inspect its complete planned effects.

When several policies match the same cards, compatible actions are combined.
Cards with incompatible planned changes are skipped and reported as conflicts.

## Getting started

Open Card Janitor from:

```text
Tools → Card Janitor…
```

1. Leave **Trigger** set to **None**.
2. Choose a narrow scope and the conditions you want.
3. Add the desired action(s).
4. Use **Browse** to inspect matching cards.
5. Use **Preview…** to inspect the changes Card Janitor would make.
6. Save the policy and run cleanup manually.
7. Add an automatic trigger later if you want the policy to run unattended.

The policy manager also has a **Cleanup Preview** for the combined result of
all selected policies, including conflicts between them.

## Safety and Undo

Card Janitor does not ship with any policies by default. Collection-wide
deletion of all cards without a scope or condition restriction is rejected.
Clicking **Clean Up** applies the checked policies without another confirmation;
automatic triggers also execute without per-run confirmation.

Cleanup participates in Anki's normal Undo system. Changes are grouped into a
Card Janitor Undo entry as cleanup proceeds. If an unexpected operation fails,
Card Janitor reports whether it can verify that the available Undo entry
contains all successfully completed operations from that cleanup.

Undo availability still follows Anki's normal Undo history and is not a
persistent rollback mechanism.

## Cleanup history

Card Janitor records a local cleanup history by default. Click the **Last
cleanup** summary in the policy manager to review recent manual and automatic
runs, including the participating policies, changes made, affected cards or
notes, and the historical reasons those policies matched.

Cleanup history is an audit, not a backup or persistent Undo mechanism. It is
stored locally for the current Anki profile and does not sync. The viewer can
load older entries, export the complete JSONL history, or permanently delete
it. Recording can be disabled under **Settings…** without deleting existing
history; there is no automatic retention limit.

## Advanced editing

Policies are normally created and edited with Card Janitor's policy editor.

**Edit as JSON…** is also available for advanced editing, copying, and sharing
policies. Review shared policies carefully before saving them, particularly
their actions and automatic triggers.

See [policies.md](./docs/policies.md) for the complete JSON format and reference.

## Storage and profiles

Policies are stored in the Anki collection and sync with it, so each profile
has its own policies.

Add-on settings such as automatic-cleanup, cleanup-history, notification, and
debug preferences are local to the Anki installation. Automatic cleanup and
cleanup history are tracked separately for each profile. Cleanup history is
stored in local add-on files rather than in the collection and does not sync.

## Known limitations

- Tags belong to notes in Anki, so changing tags affects sibling cards.
- First-review age cannot recover review history that was deleted or omitted
  during import.
- Decks, note types, and card types are referenced by name. Policies containing
  missing or renamed references are not applied until repaired.

## Development

See [development.md](./docs/development.md) for development setup, testing,
packaging, and release instructions.

## Attribution

Anki: Ankitects Pty Ltd and contributors.

## License

GNU AGPL v3 or later. See [LICENSE](./LICENSE).
