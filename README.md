# Card Retirement

Configurable automatic retirement policies for Anki cards.

Retires cards according to deck scope, study age, current interval, or answer count, with explicit manual, notification, and automatic execution modes.

## Installation

### Manual Installation

1. Download `card-retirement.ankiaddon` from the
   [latest GitHub release](https://github.com/zarnthyr/card-retirement/releases/latest).
2. Double-click the file, or open Anki and choose:

```text
Tools → Add-ons → Install from file...
```

3. Restart Anki if prompted.

### Install From Source

```bash
git clone https://github.com/zarnthyr/card-retirement.git
cd card-retirement
uv sync
make build
```

Then install `card-retirement.ankiaddon` from Anki's add-ons screen or by double clicking it.

## Policies

Each policy has three parts:

* Scope — one or more decks, optionally including subdecks
* Rule — age, interval, answer count, or an AND/OR composition
* Actions — tag, suspend, move, or delete the qualifying cards

First-review age is derived from genuine answer entries in Anki's review log. Cards without first-review history do not qualify for that rule. Card-creation age is available separately.

## Execution Modes

| Mode      | Behavior                                                        |
| --------- | --------------------------------------------------------------- |
| Manual    | Only evaluates or runs when selected from the Tools menu        |
| Notify    | Checks after profile opening and reports cards requiring action |
| Automatic | Checks after profile opening and applies configured actions     |

Automatic checks are rate-limited and do not use background polling.

## Configuration

Open the JSON settings editor from:

```text
Tools → Card Retirement → Settings...
```

The add-on ships with no policies, so installing it cannot modify a collection. Begin with a manual tag-and-suspend policy, use **Preview Policy...** to inspect the exact cards in Anki's Browser, and enable notification or automatic execution only when satisfied with the result.

Automatic deletion is supported but is never configured by default. It requires an explicit `delete_card` action with `mode: "automatic"` and runs without confirmation.

See [config.md](./docs/config.md) for the complete schema and examples.

## Known Limitations

* Tags belong to notes in Anki, so tagging a qualifying card tags its note and any sibling cards
* First-review age and successful-answer rules cannot recover history that was deleted or omitted during import
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

Repository: https://github.com/zarnthyr/card-retirement

Issue tracker: https://github.com/zarnthyr/card-retirement/issues
