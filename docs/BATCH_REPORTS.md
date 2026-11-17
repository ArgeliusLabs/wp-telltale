# Target files and combined reports

Version 0.2.0 accepts a file of domains or installation URLs and produces one combined HTML report, plus JSON and CSV exports. All collection uses the same bounded single-target collector.

## Input format

```text
# Case notes can be kept in whole-line comments.
https://newsroom.example/
publisher.example
https://magazine.example/blog/

# This is a duplicate of the first normalized target and will be skipped.
https://NEWSROOM.EXAMPLE
```

The example domains are fictional. Replace them with the targets in your investigation; use `--demo-batch` for an offline demonstration.

- Save the file as UTF-8. A UTF-8 byte-order mark, Windows CRLF, and ordinary LF line endings are supported.
- Put one domain or HTTP(S) installation URL on each line.
- Leading/trailing whitespace is removed. Blank lines and whole-line `#` comments are ignored.
- A bare domain uses HTTPS. Include `http://` explicitly if needed.
- Use a WordPress installation root such as `/blog/`, rather than an article URL.
- Embedded credentials, URL queries/fragments, unsupported schemes, and whitespace inside a target are rejected.
- Inline comments are not supported. Put the comment on its own line.

The parser normalizes targets and skips exact normalized duplicates, retaining the first occurrence. It records duplicate line numbers for review. It does not treat every possible canonical redirect as a duplicate: for example, an HTTP URL and an HTTPS URL may both remain in the accepted list.

Invalid lines are recorded as line number and error message, without copying the rejected text into the report. Valid targets continue. A file with no accepted targets is an input error and starts no collection.

### Windows

From the folder containing the script:

```text
python wp_telltale.py --targets "C:\cases\example\targets.txt" --out "C:\cases\example\reports\first-pass"
```

Use `py -3` if that is your Python launcher. Quoting paths with spaces works in both Command Prompt and PowerShell.

### Linux or macOS

```bash
python3 wp_telltale.py --targets targets.txt --out reports/first-pass
```

`--target-file` is an alias for `--targets`. Choose exactly one input mode: a positional URL, a target file, `--demo`, or `--demo-batch`.

## Collection behavior

Targets run sequentially, with progress printed as each target is handled. A normal network error, unavailable REST endpoint, or unknown WordPress status does not stop later targets. Each target has its own request budget and delay setting.

The defaults are a 12-second per-socket timeout, a 0.35-second minimum interval between request starts, and a 20-request budget per target. Redirects and failed attempts count toward that budget. The timeout is not a total deadline for a site or batch.

`--quick` skips author, post, and media sampling. Otherwise, each main collection requests at most `--sample` records, default 10 and maximum 100. The earliest-post sample is capped at `min(sample, 5)`. The tool does not paginate through the full site history.

The same `--expected-languages` and `--expected-timezone` values apply to every target. For a geographically mixed list, omit a shared baseline or split it into appropriate groups. Baselines are analyst inputs, not automatically established site facts.

## One HTML report, two data exports

Given:

```text
python wp_telltale.py --targets targets.txt --out reports/case-001
```

The tool writes:

```text
reports/case-001.html
reports/case-001.json
reports/case-001.csv
```

Parent directories are created as needed. When `--out` is omitted for a batch, the prefix is `reports/wp-telltale-batch-<UTC timestamp>`. Explicit prefixes can replace previous report files. Choose a different prefix to retain each collection as a separate observation.

### HTML

Open the HTML in a browser. It contains a searchable target overview, review cues, shared tracking identifiers, input problems, and expandable target details. The file is self-contained; its report interface does not require a CDN or web service.

Start with the status and coverage. Then review additional translations, language declarations, timezone settings, identifiers, and their source details. A cue means there is something to inspect. It does not mean that the site belongs to an influence operation.

### JSON

The combined report has `mode: "batch"` and `schema_version: "1.0"`. Important fields are:

| Field | Contents |
| --- | --- |
| `targets` | Accepted, normalized target URLs |
| `input_file` | Source target-file path, or null for the built-in demonstration |
| `observed_at`, `completed_at` | UTC collection timestamps |
| `parameters`, `baseline` | Collection limits and analyst-supplied expectations |
| `rejected_inputs` | Objects containing a line number and error message; rejected raw text is not copied |
| `duplicate_inputs` | Duplicate line, normalized target, and first line where it was accepted |
| `results` | Per-target status, error information, and a nested individual `report` when available |
| `pending_targets` | Targets not reached before interruption |
| `shared_identifiers` | Exact kind/value matches across distinct target hosts, with host/target lists and source evidence |
| `counts` | Aggregate collection counts |
| `interrupted` | Whether collection was interrupted |
| `local_errors` | Local evidence/output-related failures retained in the report where possible |
| `output_files` | Paths of the generated HTML, JSON, and CSV |

Each ordinary `results` entry contains `target`, `status`, `report`, and `error`. If a target fails or is interrupted before a full report is returned, `partial_requests` can preserve the completed request records. The nested report preserves the single-target structure, including `wordpress`, `interface_locales`, `homepage`, `samples`, `review_cues`, `coverage`, `requests`, and `warnings`. Existing JSON names are retained for compatibility even where the human-facing wording has become more precise.

Status values describe evidence collection:

| Status | Meaning |
| --- | --- |
| `observed` | WordPress REST metadata or available-language evidence was obtained; individual samples can still be unavailable |
| `unknown` | That evidence was not obtained; other public page details may still be present |
| `error` | Collection could not complete for the target; read its error |
| `interrupted` | The current target was interrupted; its record may contain only partial collection information |

These are not attribution or threat categories. JSON is the preferred export for preserving exact source values and writing further analysis.

### CSV

The batch CSV is a flat review table. It also includes unattempted targets with status `pending`; in JSON, those URLs are stored separately in `pending_targets`. Its columns are:

```text
target, status, wordpress_rest_observed, translation_availability,
advertised_translations, additional_translations, selected_dashboard_language,
homepage_language, timezone, utc_offset, review_cue_count, review_cues,
tracking_identifiers, shared_identifier_count, request_count,
collection_warnings, error
```

The CSV is UTF-8. Potential spreadsheet formula prefixes in text cells are escaped with a leading apostrophe; the JSON retains the original values. Use the JSON and response evidence when exact byte-for-byte field comparison matters. A blank or unknown value is not an assertion that the feature is absent.

## Shared identifier matches

A match requires the same identifier kind and value on at least two distinct target hostnames. Host grouping removes a leading `www.` and ignores scheme, port, and installation path. Multiple scans of the same hostname therefore do not manufacture a cross-host relationship.

The report includes the target URLs and available source evidence for each match. Inspect those snippets before treating an identifier as active configuration. The same string could appear in a code example, article, copied template, shared agency setup, or deliberately planted markup.

AdSense platform-account strings beginning `ca-host-pub-` are excluded from publisher-account pivots. Common themes, a shared CDN, and generic linked service hosts are not treated as account-identifier matches. The tool does not infer an operator or state sponsor from a match.

Matching happens within the current batch. Version 0.2.0 does not load older report files or compare separate collection dates automatically.

## Evidence directories

```text
python wp_telltale.py --targets targets.txt --evidence-dir evidence/case-001 --out reports/case-001
```

Use a new or empty evidence root. Batch mode creates unique, indexed, Windows-safe subdirectories for the targets. Response-body files are written without silently overwriting earlier captures. A local evidence/output failure is reported as an error rather than discarded.

Request records retain source URLs, timestamps, response status, selected headers, SHA-256 hashes, and the paths of saved bodies. The hash identifies the collected bytes; it does not prove the authenticity of a site's claim. Responses are capped at 3 MiB, and a truncated prefix is marked as such.

Collection does not authenticate, send cookies, or follow external reference domains. Reports and saved bodies can still contain public names, source identifiers, and your target selection. Keep case-specific material in your case directory. This repository's examples are fictional.

## Interruption and exit codes

On Ctrl+C, batch mode attempts to write completed results, any available current request log, and pending targets. The interrupted target should not be treated as a completed collection. Saving can still fail if the local output path is unwritable.

There is no automatic resume in v0.2.0. Use the pending-target information to prepare a fresh list and a fresh output/evidence location.

| Exit code | Meaning |
| --- | --- |
| `0` | All accepted targets produced WordPress REST or language evidence, with no rejected inputs |
| `1` | At least one target was unknown or failed, or the input contained rejected lines |
| `2` | Invalid arguments, unreadable/empty/all-invalid input, or a local report/evidence error |
| `130` | Keyboard interruption |

A batch with a rejected line can still produce useful reports and complete every valid target. An exit code of 1 should prompt inspection of the report, not the assumption that nothing was collected.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| “Unrecognized arguments: --targets” | Run `python wp_telltale.py --version`; native batch input starts with v0.2.0 |
| The target file is rejected | Save as UTF-8, use one domain/base URL per line, and put comments on separate lines |
| Many targets are unknown | Review HTTP statuses, timeouts, blocked indexes, and HTML fallback responses; unknown is not a verdict on the CMS |
| Batch takes longer than expected | Collection is sequential and socket timeouts are not whole-target deadlines; use `--quick` for an initial pass |
| Existing evidence is rejected | Choose a new/empty evidence root rather than reusing a captured case directory |
| The CSV displays an apostrophe | It may be spreadsheet-formula escaping; consult JSON for the original value |
| A translation differs from the homepage | Read the [language guide](LANGUAGES.md); availability does not reveal active use |
