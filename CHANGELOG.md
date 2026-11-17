# Changelog

## 0.2.0 (2026-11-17)

### Added

- Native text-file target input with `--targets` and the `--target-file` alias.
- UTF-8/BOM and Windows line-ending support, blank lines and whole-line comments, normalized deduplication, and line-numbered input errors.
- Sequential batch collection with per-target budgets, progress, failure handling, and partial-result handling on interruption.
- One combined searchable HTML report, full JSON export, and a summary CSV.
- Exact tracking-identifier comparisons across distinct target hosts, with source evidence and explicit interpretation limits.
- Fictional offline batch demonstration through `--demo-batch`.
- Per-target evidence subdirectories suitable for Windows paths.
- Inline-CSS URL references from style attributes and style blocks, recorded without fetching the referenced resources.
- Dedicated language and batch-report documentation.

### Changed

- Console and HTML labels distinguish advertised WordPress translations from a selected dashboard language and a public content-language switcher.
- Language review cues compare available translations with homepage declarations; they do not claim an observed active dashboard language mismatch.
- Existing single-target commands and individual JSON field names remain available.

### Fixed

- AdSense `ca-host-pub-...` platform accounts no longer produce publisher-ID substring pivots.
- Wildcard path strings are excluded from plugin/theme inventories.
- CSV text with potential formula prefixes is escaped for spreadsheet review; JSON retains original values.

## 0.1.0 (2026-11-17)

- Initial single-target public WordPress metadata collector.
- Core user-schema locale discovery, login-language choices, public page declarations, timezone metadata, tracking identifiers, public author/post/media samples, JSON/HTML reports, and optional response-body preservation.
- Fictional offline demonstration and regression tests.
