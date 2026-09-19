# Settings

These settings apply to every Anki profile on this installation. Policies are
stored separately in each collection and are managed from the Card Janitor
window.

## Options

- `automatic_cleanup_enabled` — allow policy triggers to apply actions (enabled by default). Turn it off to pause automatic cleanup; manual cleanup remains available and policies are unchanged.
- `notify_after_automatic_run` — show a summary after automatic cleanup changes cards
- `warn_on_invalid_automatic_policies` — after opening sync, check automatic policy references and scheduler compatibility and warn if one needs attention (enabled by default)
- `debug_logging` — print cleanup diagnostics to Anki's terminal output

No completion notification is shown when no cards were changed. Configuration
errors and action conflicts are still reported. Configuration errors and
unexpected exceptions are always printed, even when debug logging is disabled.
Notifications wait for an existing Anki tooltip to disappear. The dashboard's
Last cleanup result is recorded independently of the notification setting.
The startup policy check is lightweight: it does not scan cards, apply actions,
or overwrite Last cleanup. Errors encountered by an actual manual or automatic
run are still reported regardless of this setting.

`config_version` identifies the settings format and must remain `1`.

The default configuration is:

    {
      "config_version": 1,
      "automatic_cleanup_enabled": true,
      "notify_after_automatic_run": true,
      "warn_on_invalid_automatic_policies": true,
      "debug_logging": false
    }

Use **Tools → Card Janitor… → Settings…** for the normal settings interface.
Anki's Add-ons configuration editor exposes the same installation-wide values
as JSON.

To edit policies as JSON, open **Tools → Card Janitor…** and choose **Edit as
JSON…**.
