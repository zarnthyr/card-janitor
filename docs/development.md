# Development

```bash
uv sync
make check
make build
make inspect
```

The project targets Python 3.10+ and uses the Anki 26.08 development packages. Runtime dependencies are limited to APIs bundled with Anki.

For manual testing, install `card-retirement.ankiaddon` in Anki and begin with a
`manual` policy. Exercise Preview before Run, confirm the Browser IDs, then test
undo. Test `notify` mode before `automatic` mode.
