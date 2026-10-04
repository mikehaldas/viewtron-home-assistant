# Changelog

## 1.0.1

- Require viewtron SDK 1.3.1 or newer. License plate events now reach Home
  Assistant again: the bridge calls `get_plate_group()`, which 1.3.0 didn't
  have, so plate events failed before they were published.
- SDK 1.3.1 also fixes a crash on events with an empty image, and keeps the
  camera connection open when an event can't be parsed.
- Fix the SDK version pin in the Dockerfile. It was unquoted, so the shell
  treated `>=1.3.0` as an output redirect and the pin was ignored.
