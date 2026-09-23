# Settings

Card Janitor settings apply to every Anki profile on this installation.
Policies are stored separately in each collection and are managed from the
Card Janitor window.

## Options

### Automatic cleanup

`automatic_cleanup_enabled` — allows automatic policy triggers to run cleanup.
Enabled by default.

Turn this off to pause all automatic cleanup without changing or removing
triggers from individual policies. Manual cleanup remains available.

### Automatic cleanup notifications

`notify_after_automatic_run` — shows a summary when automatic cleanup changes
cards. Enabled by default.

No completion notification is shown when no cards were changed. Configuration
errors, action conflicts, and unexpected failures are still reported.

### Automatic policy warnings

`warn_on_invalid_automatic_policies` — when automatic cleanup is enabled,
checks automatic policies on profile open and warns when a policy has invalid
collection references or is incompatible with the current scheduler. The check
waits for opening sync when automatic sync is enabled. Enabled by default.

This is a lightweight validation check. It does not scan cards, apply actions,
or change the dashboard's Last cleanup result. Errors encountered during an
actual manual or automatic cleanup are still reported when this setting is
disabled.

### Cleanup history

`cleanup_history_enabled` — records an append-only audit of future manual and
automatic cleanup runs in Card Janitor's local add-on files. Enabled by
default.

History is local to this Anki installation and profile-history stream. It is
not stored in the collection and does not sync through Anki. Turning history
off stops new events only; it does not delete, truncate, or hide existing
history. Turning it on again records future runs and does not reconstruct the
gap. Deliberate exports can contain collection metadata such as policy, deck,
tag, and note-type names.

History has no automatic retention limit. Export copies the complete local
history without changing it. Clear permanently removes all cleanup history for
the profile and retains no archive or recovery copy, so export or manually copy
the JSONL file first when it should be kept. Import is not currently supported.

### Debug logging

`debug_logging` — prints additional cleanup diagnostics to Anki's terminal
output. Disabled by default.

Configuration errors and unexpected exceptions are always printed even when
debug logging is disabled.

## Configuration format

`config_version` identifies the settings format and must remain `1`.

The default configuration is:

    {
      "config_version": 1,
      "automatic_cleanup_enabled": true,
      "cleanup_history_enabled": true,
      "notify_after_automatic_run": true,
      "warn_on_invalid_automatic_policies": true,
      "debug_logging": false
    }

Use **Tools → Card Janitor… → Settings…** for the normal settings interface.

Anki's Add-ons configuration editor exposes the same installation-wide settings
as JSON.

To edit policies as JSON, open **Tools → Card Janitor…** and choose **Edit as
JSON…**.
