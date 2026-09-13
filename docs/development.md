# Development

```bash
uv sync
make check
make build
make inspect
```

The project targets Python 3.10+ and uses the Anki 26.08 development packages. Runtime dependencies are limited to APIs bundled with Anki.

For manual testing, install `card-janitor.ankiaddon` in Anki and begin with a
policy whose state is `manual`. Open **Card Janitor…** to review the policy table,
inspect qualifying cards in the Browser, execute the policy, and test undo before trying
the `automatic` state.

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
