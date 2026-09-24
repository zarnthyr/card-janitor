# Development

Card Janitor targets Python 3.11+ and uses the Anki 26.8.1 development packages
pinned in `uv.lock`. Runtime dependencies are limited to APIs bundled with Anki.

## Setup and checks

Install the development environment and run the standard checks with:

```bash
uv sync
make check
make build
make inspect
```

`make check` runs the automated formatting, linting, and test suite. `make build`
creates `card-janitor.ankiaddon`, and `make inspect` validates the packaged
artifact.

Changes that affect Anki integration should also be tested with the packaged
add-on in the expendable `dev` profile described below.

## Manual integration testing

The automated tests cover policy parsing, evaluation, planning, action
dispatch, and integration with Anki's backend APIs. Manual testing additionally
exercises Card Janitor inside Anki, including the Browser, UI, collection
mutations, destructive deletion, and Undo.

Manual test helpers must only be used with the expendable profile named `dev`.
Never run them against a personal or otherwise non-expendable profile.

### Development fixture

The fixture requires AnkiConnect and the `dev` profile. With that profile open,
run:

```bash
make dev-seed
```

The command refuses to run against any other profile. It replaces only notes
tagged `card_janitor_test_fixture`.

Copy `tests/manual/dev-profile-config.json` into:

```text
Card Janitor… → Edit as JSON…
```

Save it and open Card Janitor. The twelve policies should report:

| Policy | Cards |
| --- | ---: |
| Dev — Old Reviews | 2 |
| Dev — Long Intervals | 3 |
| Dev — Never Studied | 2 |
| Dev — Learning States | 2 |
| Dev — Grouped Mature Reviews | 3 |
| Dev — Grouped New or Learning | 4 |
| Dev — Repair Suspended Leech | 1 |
| Dev — Replace All Tags | 1 |
| Dev — Delete Note | 1 |
| Dev — Suspend Note | 1 |
| Dev — Unsuspend Note | 1 |
| Dev — Move Note | 1 |

The first six policies have seven unique matching cards. The two grouped
policies deliberately overlap all seven of those cards with the four flat
policies: one demonstrates `review AND (old OR long interval)`, and the other
demonstrates `new OR (learning AND studied)`. The tag repair, replacement, and
delete policies target three separate cards. The three note-action policies
affect three outside-scope siblings, for a total of thirteen cards to clean up.

Two unmatched cards verify that conditions exclude an in-scope card and that
deck scope excludes an out-of-scope card. The move action uses the separate
top-level `Card Janitor Retired` deck, verifying that moved cards leave the test
source hierarchy completely.

Run the integration test as follows:

1. Confirm the policy counts, seven overlapping cards, and a total of thirteen
   cards to clean up.
2. Click **Browse** and confirm that it shows the same thirteen cards.
3. Click **Clean Up** and confirm all actions complete.
4. Confirm that the isolated delete card and its note were removed.
5. Use Anki's Undo command and confirm all thirteen cards, including the deleted
   card and note, are restored.
6. Click **Refresh** and confirm the original counts return.

Run `make dev-seed` again whenever a clean fixture is needed. Keep fixture
policies manual-only so opening the profile cannot mutate them before
inspection.

### Note-action fixtures

The note-action fixtures use a two-card note type. **Dev — Suspend Note**,
**Dev — Unsuspend Note**, and **Dev — Move Note** should each report one card.

Their in-scope triggering card already satisfies the action, while a sibling in
**Card Janitor Test::Outside Scope** needs updating. Browse should show that
sibling, cleanup should update it, and Undo should restore its previous state or
deck. The unsuspension fixture uses an active in-scope trigger and a suspended
out-of-scope sibling.

These policies restrict scope to **Card Janitor Test Siblings**. Confirm the
editor loads that selection and the manager shows it in the Scope column.
Changing the selection to **Basic** should show no matches for these fixtures.

The note-action policies also include sibling suspension and review-history
conditions. Counts should remain one each. **Dev — Unsuspend Note** should match
through `any` suspended despite its suspended sibling being outside scope.
Changing that condition to `none` should show no matches. The other two fixtures
check `none` studied across both cards of the note.

### Cleanup Preview

Open **Preview…** beside **Clean Up** and check:

- Planned changes
- All affected
- Overlapping policies
- Conflicts

Compatible overlapping actions should be merged. Skipped cards should explain
conflicting intentions without showing planned changes.

Select rows to Browse only those cards, or clear the selection to Browse the
entire current view. Closing the Browser should restore the preview's focus.

Preview a policy from both its form and JSON views. This evaluates that policy
alone without saving or applying it, and its title should identify the current
policy.

Check that note-action siblings explain why they are included and that
already-satisfied actions are omitted. Add and remove editor rows and return
from JSON with both Apply and Cancel to check window sizing.

Preview must not create an Undo entry.

### Cleanup History

Use the expendable `dev` profile after changes to history execution semantics,
event validation, persistence, or the viewer.

1. Enable **Record cleanup history on this device**, run `make dev-seed`, and
   load `tests/manual/dev-profile-config.json`.
2. Apply the complete manual cleanup. Open the clickable **Last cleanup**
   summary and confirm the newest row reports Manual source, no automatic
   trigger, Changed, 13 distinct cards, and 12 participating policies.
3. Inspect the selected event. Shared effects should be shown once under all
   contributing policies, and note-wide changes must identify consequential
   siblings in **Affected**.
4. Use Anki Undo once and confirm the collection is restored. The cleanup event
   should remain in history; v1 does not claim to track later Undo or Redo.
5. Run a successful no-op cleanup and confirm it appears as **No change** with
   a policies-evaluated explanation rather than invented changes.
6. Exercise complete-history Export and permanent Delete. Export must not alter
   the active history; Delete should remove the profile history only after
   confirmation and should leave no retained archive.
7. Disable history, run a cleanup, and confirm no new event is added. Re-enable
   it and confirm later runs are recorded without reconstructing the gap.

For corruption handling, work only on a copied expendable profile-history log.
Append one malformed line and confirm the viewer marks that row Corrupt while
surrounding valid events remain readable and Export preserves the complete
underlying JSONL.

### Undo history stress test

Use this focused test after changes to cleanup execution or Undo grouping. It
exercises 101 Undo-producing backend operations, substantially exceeding Anki's
30-entry Undo-history limit.

1. Run `make dev-seed` with the expendable `dev` profile open.
2. Copy `tests/manual/undo-stress-config.json` into **Card Janitor… → Edit as
   JSON…** and save it. This temporarily replaces the normal development
   policies.
3. Confirm **Dev — Undo Stress** reports one matching card in
   **Card Janitor Test::Delete**.
4. Click **Clean Up** and confirm it completes without an Undo-grouping error.
5. Confirm Anki's Undo command shows one **Card Janitor: Clean Up** entry.
6. Use Undo once and confirm all `cj_test_undo_000` through
   `cj_test_undo_100` tags are removed from the note.
7. Reload `tests/manual/dev-profile-config.json` to restore the normal manual
   integration policies.

Test the stress policy by itself. Do not combine it with the normal delete
policy for the same fixture card: tagging and deleting that card intentionally
conflict during planning.

### Automatic triggers

The supplied development policies remain manual-only.

In the expendable `dev` profile, give a reversible tag-only policy **On open**
and **On sync** triggers. Remove its output tag between checks so each run has
something to change.

Verify that:

- reopening or switching into the profile applies the policy
- a later collection sync applies it again on the same day
- opening auto-sync produces one cleanup Undo entry rather than two
- failed or cancelled sync attempts still evaluate the local collection
- closing sync does not apply the policy
- cleanup changes already made still upload through normal closing sync

For **Daily**, verify that:

- two policies track completion independently
- reopening does not rerun a completed daily-only policy that day
- a newly added daily policy remains eligible
- manual cleanup does not consume the daily limit

Changes made after sync should appear in the next sync, including closing sync.
Cleanup must not initiate another sync or intercept Anki's shutdown sequence.

### Policy and editor spot checks

Before a release that changes the policy model or editor, also spot-check the
affected controls in the packaged add-on.

The current baseline includes:

- exact card-type scope
- suspended-card matching with and without a Suspension state condition
- an FSRS or SM-2 metric appropriate to the profile's scheduler
- setting and clearing a card flag
- rejection of an incompatible FSRS/SM-2 combination
- repair behavior for a missing deck, note type, card type, or move destination

Missing references should remain visible for repair but must not be applied.

## Linked development installation

On macOS, install a live development copy into Anki with:

```bash
make dev-install
```

The installer creates `card_janitor` in Anki's `addons21` directory. Runtime
source files are symlinked to the checkout, while `config.json` is a separate
file that Anki can edit without changing the repository.

Restart Anki after Python changes. Reloading the module in place can register
hooks more than once.

To use a nonstandard Anki data location, set
`CARD_JANITOR_ANKI_ADDONS_DIR` to its `addons21` directory.

Remove the linked development installation and its local configuration with:

```bash
make dev-uninstall
```

## GitHub workflows

Pull requests and pushes to `main` run CI. CI checks formatting, linting, and
tests on Python 3.11 through 3.13, then builds and inspects the add-on package.
The resulting `.ankiaddon` file is uploaded as a workflow artifact.

Version tags such as `v0.1.0` run the release workflow. The workflow verifies
that the tag matches the version in `pyproject.toml`, repeats the checks, and
publishes a GitHub Release with `card-janitor.ankiaddon` attached.

The workflow's manual trigger can resume an unfinished draft release for an
existing tag. It does not replace an already-published release.

## Release process

1. Update the version in `pyproject.toml`.
2. Confirm the README and policy documentation describe the supported behavior.
3. Run `make clean all inspect`.
4. Commit the release candidate on a release branch, push it, and open a pull
   request to `main`.
5. Confirm pull-request CI passes, then install its packaged artifact in the
   expendable `dev` profile and complete the manual integration test.
6. Merge the pull request into `main`.
7. Update local `main` to the merged commit, then create and push a signed
   version tag matching `pyproject.toml`.

Replace `X.Y.Z` with the version chosen for the release:

```bash
git switch main
git pull --ff-only origin main
git tag -s vX.Y.Z -m "Card Janitor vX.Y.Z"
git push origin vX.Y.Z
```

Pushing the tag publishes the GitHub Release automatically. Do not move or
reuse a published version tag; increment the project version in both
`pyproject.toml` and `src/version.py` for the next release.

Releases are created as drafts, receive the add-on asset, and are then
published. With release immutability enabled, published assets and tags cannot
be changed.

Rerunning the workflow resumes an unfinished draft, replacing its draft asset
before publishing. An already-published release is left untouched; ship changes
under a new version tag instead. Runs for the same tag are serialized.
