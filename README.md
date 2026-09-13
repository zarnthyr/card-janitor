# Card Janitor

Configurable policy-based cleanup for Anki cards.

Applies cleanup policies according to deck scope, study age, current interval, or new-card status, either after manual preview and approval or automatically on a configurable schedule.

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

Each policy has three parts:

* Scope — one or more decks, optionally including subdecks
* Rule — age, interval, new-card status, or an AND/OR composition
* Actions — tag, suspend, move, or delete the qualifying cards

First-review age is derived from genuine answer entries in Anki's review log. Cards without first-review history do not qualify for that rule. Card-creation age is available separately.

## Policy States

| State     | Behavior                                                               |
| --------- | ---------------------------------------------------------------------- |
| Disabled  | Available in the manual dialog but unchecked and never scheduled       |
| Manual    | Included in manual runs by default and never scheduled                 |
| Automatic | Included in manual runs by default and also run on the configured schedule |

Automatic execution can run on profile open, daily, or both. Daily execution uses Anki's day-change hook rather than background polling. Its completion notification can be disabled independently.

## Configuration

Open the policy manager:

```text
Tools → Card Janitor…
Card Janitor → Add… or Edit…
```

The add-on ships with no policies, so installing it cannot modify a collection. Begin with a manual tag-and-suspend policy and open **Card Janitor…** to preview its results. The dashboard can open candidates in Anki's Browser or apply the configured actions after approval.

Policies can be created and repaired in the manager. **Settings…** remains available
for editing the underlying JSON and add-on-wide options.

Automatic deletion is supported but is never configured by default. It requires an explicit `delete_card` action with `state: "automatic"` and runs without confirmation.

See [config.md](./docs/config.md) for the complete schema and examples.

## Known Limitations

* Tags belong to notes in Anki, so tagging a qualifying card tags its note and any sibling cards
* First-review age cannot recover review history that was deleted or omitted during import
* Decks are configured by name, so renamed or missing decks cause the affected policy to fail closed
* Cards temporarily in filtered decks are excluded by default

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
