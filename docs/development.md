# Development

```bash
uv sync
make check
make build
make inspect
```

The project targets Python 3.10+ and uses the Anki 26.09 development packages. Runtime dependencies are limited to APIs bundled with Anki.

For manual testing, install `card-janitor.ankiaddon` in Anki and begin with an
On demand policy (`mode: "on_demand"`). Open **Card Janitor…** to add or edit
policies, review card counts, inspect qualifying cards in the Browser, execute
a policy, and test undo before trying Automatic mode. The manager is a
modeless tool window, so it remains available while working in the Browser.

## Manual integration test

The unit tests cover policy parsing, evaluation, planning, and action dispatch.
The manual integration test additionally exercises Card Janitor inside Anki,
including the Browser, collection mutations, destructive deletion, and Anki's
undo history.

This test requires AnkiConnect and an expendable profile named `dev`. With that
profile open, seed the fixture with:

```bash
make dev-seed
```

The command refuses to run against any other profile. It replaces only notes
tagged `card_janitor_test_fixture`. Copy
`tests/manual/dev-profile-config.json` into **Card Janitor… > Edit as JSON…**,
save it, then open Card Janitor. The five policies should report these
counts:

| Policy | Cards |
| --- | ---: |
| Dev — Old Reviews | 2 |
| Dev — Long Intervals | 3 |
| Dev — Never Studied | 2 |
| Dev — Learning States | 2 |
| Dev — Delete | 1 |

The first four policies have seven unique matching cards, with two cards
matching more than one policy. The delete policy targets a separate eighth
card. Two unmatched cards verify that conditions exclude an in-scope card and
deck scope excludes an out-of-scope card. The move action uses the separate
top-level `Card Janitor Retired` deck, verifying that moved cards leave the test
source hierarchy completely.

Run the integration test as follows:

1. Confirm the policy counts and a total of eight cards to clean up
2. Click **Browse** and confirm that it shows the same eight cards
3. Click **Clean Up** and confirm all actions complete
4. Confirm that the isolated delete card and its note were removed
5. Use Anki's Undo command and confirm all eight cards, including the deleted
   card and note, are restored
6. Click **Refresh** and confirm the original counts return

Run `make dev-seed` again whenever a clean fixture is needed. Keep every fixture
policy On demand so opening the profile cannot mutate it before inspection.

## Linked development installation

On macOS, install a live development copy into Anki with:

```bash
make dev-install
```

The installer creates `card_janitor` in Anki's `addons21` directory. Runtime
source files are symlinked to this checkout, while `config.json` is a separate
file that Anki can edit without changing the repository. Restart Anki after
Python changes; attempting to reload the module in place can register hooks
more than once.

To use a nonstandard Anki data location, set
`CARD_JANITOR_ANKI_ADDONS_DIR` to its `addons21` directory. Remove the linked
development installation and its local configuration with:

```bash
make dev-uninstall
```
