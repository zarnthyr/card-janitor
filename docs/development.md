# Development

```bash
uv sync
make check
make build
make inspect
```

The project targets Python 3.11+ and uses the Anki 26.8.1 development packages pinned in `uv.lock`. Runtime dependencies are limited to APIs bundled with Anki.

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
save it, then open Card Janitor. The ten policies should report these
counts:

| Policy | Cards |
| --- | ---: |
| Dev — Old Reviews | 2 |
| Dev — Long Intervals | 3 |
| Dev — Never Studied | 2 |
| Dev — Learning States | 2 |
| Dev — Repair Suspended Leech | 1 |
| Dev — Replace All Tags | 1 |
| Dev — Delete Note | 1 |
| Dev — Suspend Note | 1 |
| Dev — Unsuspend Note | 1 |
| Dev — Move Note | 1 |

The first four policies have seven unique matching cards, with two cards
matching more than one policy. The tag repair, replacement, and delete policies
target three separate cards. The three note-action policies affect three
outside-scope siblings, for a total of thirteen cards to clean up.
Two unmatched cards verify that conditions exclude an in-scope card and
deck scope excludes an out-of-scope card. The move action uses the separate
top-level `Card Janitor Retired` deck, verifying that moved cards leave the test
source hierarchy completely.

Run the integration test as follows:

1. Confirm the policy counts and a total of thirteen cards to clean up
2. Click **Browse** and confirm that it shows the same thirteen cards
3. Click **Clean Up** and confirm all actions complete
4. Confirm that the isolated delete card and its note were removed
5. Use Anki's Undo command and confirm all thirteen cards, including the deleted
   card and note, are restored
6. Click **Refresh** and confirm the original counts return

Run `make dev-seed` again whenever a clean fixture is needed. Keep every fixture
policy On demand so opening the profile cannot mutate it before inspection.

The note-action fixtures use a two-card note type. **Dev — Suspend Note**,
**Dev — Unsuspend Note**, and **Dev — Move Note** should each report one card:
their in-scope triggering card already satisfies the action, while a sibling
in **Card Janitor Test::Outside Scope** needs updating. Browse should show that
sibling, cleanup should update it, and Undo should restore its previous state
or deck. Note unsuspension deliberately excludes suspended triggering cards
from scope while still updating its suspended sibling.
These policies restrict scope to **Card Janitor Test Siblings**. Confirm the
editor loads that selection and the manager shows it in the Scope column.
Changing the selection to **Basic** should show no matches for these fixtures.
The note-action policies also include sibling suspension and review-history
checks. Counts remain one each. **Dev — Unsuspend Note** should match through
`any` suspended despite its suspended sibling being outside scope. Changing
that condition to `none` should show no matches. The other two fixtures check
`none` studied across both cards of the note.

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

## GitHub workflows

Pull requests and pushes to `main` run the CI workflow. CI checks formatting,
linting, and tests on Python 3.11 through 3.13, then builds and inspects the
add-on package once. The resulting `.ankiaddon` file is uploaded as a workflow
artifact.

Version tags such as `v0.1.0` run the release workflow. The workflow verifies
that the tag matches the version in `pyproject.toml`, repeats the checks, and
publishes a GitHub Release with `card-janitor.ankiaddon` attached. An existing
tag can also be rebuilt from the workflow's manual trigger.

## Release process

1. Update the version in `pyproject.toml`
2. Confirm the README and policy documentation describe the supported behavior
3. Run `make clean all inspect`
4. Install that packaged artifact in the expendable `dev` profile and complete
   the manual integration test
5. Commit the version and documentation changes
6. Push `main` and confirm CI passes
7. Create and push a signed version tag matching `pyproject.toml`

For the current release, the final commands are:

```bash
git tag -s v0.2.0 -m "Card Janitor v0.2.0"
git push origin main
git push origin v0.2.0
```

Pushing the tag publishes the GitHub Release automatically. Do not move or
reuse a published version tag; increment the project version for the next
release.

Releases are created as drafts, receive the add-on asset, and are then published.
With release immutability enabled, published assets and tags cannot be changed.
Rerunning the workflow resumes an unfinished draft, replacing its draft asset
before publishing. An already-published release is left untouched; ship changes
under a new version tag instead. Runs for the same tag are serialized.
