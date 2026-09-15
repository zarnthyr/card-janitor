# Settings

These settings apply to every Anki profile on this installation. Policies are
stored separately in each collection and are managed from the Card Janitor
window.

## Options

- `notify_after_automatic_run` — show a summary after automatic cleanup changes cards
- `debug_logging` — print policy evaluation details to Anki's terminal output

No automatic-run summary is shown when no cards were changed. Configuration
errors and action conflicts are still reported. Configuration errors and
unexpected exceptions are always printed, even when debug logging is disabled.

`config_version` identifies the settings format and must remain `1`.

The default configuration is:

    {
      "config_version": 1,
      "notify_after_automatic_run": true,
      "debug_logging": false
    }

Use **Tools → Card Janitor… → Settings…** for the normal settings interface.
Anki's Add-ons configuration editor exposes the same installation-wide values
as JSON.

To edit policies as JSON, open **Tools → Card Janitor…** and choose **Edit as
JSON…**.
