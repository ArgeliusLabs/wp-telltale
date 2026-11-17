# WP Telltale

**Find the clues a WordPress site leaves behind.**

WP Telltale collects public WordPress translation, configuration, account-identifier, and content metadata for OSINT investigations. Inspect one site or give it a text file of targets and open **one combined, searchable HTML report**. JSON preserves the underlying observations; a batch CSV provides one summary row per target.

**Version 0.2.0 · Python 3.10+ · Standard library only · No API key or pip install required**

An additional WordPress translation can be a useful lead. It does not reveal a person's selected dashboard language or establish who operates a site. WP Telltale keeps observed configuration, collection failures, and analyst review cues separate. It does not assign a country or attribution score.

## Start with a target file

Put one domain or installation URL on each line of `targets.txt`:

```text
# Replace these fictional examples with your investigation targets.
https://newsroom.example/
publisher.example
https://magazine.example/blog/
```

Run this from the directory containing `wp_telltale.py`. The command works in Windows Command Prompt, PowerShell, and shells where `python` starts Python 3.10 or newer:

```text
python wp_telltale.py --targets targets.txt --out reports/case-001
```

On Linux or macOS, use `python3` if that is your Python 3 command. On Windows, `py -3` is another common launcher.

Open **`reports/case-001.html`**. The same run creates:

| File | Purpose |
| --- | --- |
| `case-001.html` | One searchable overview, review cues, shared identifiers, and expandable target details |
| `case-001.json` | Full per-target reports, collection metadata, input problems, and identifier matches |
| `case-001.csv` | A flat summary for sorting, filtering, or importing into other tools |

The `reports/` directory is created automatically. If you omit `--out` in batch mode, the tool uses a timestamped prefix under `reports/`. Sites run sequentially. One unavailable site does not stop the rest of the list.

The input accepts UTF-8 text, including a Windows UTF-8 byte-order mark and CRLF line endings. Blank lines and lines beginning with `#` are ignored. Exact normalized duplicates are skipped. Invalid lines are recorded by line number while valid targets continue. Empty files and files with no valid targets produce an input error. See [batch input and report details](docs/BATCH_REPORTS.md).

## Try it without contacting a website

The demonstrations use built-in fictional responses and make no network requests:

```text
python wp_telltale.py --demo-batch --out reports/demo-batch
python wp_telltale.py --demo --out reports/demo-single
```

The batch demo includes multiple fictional cases and an identifier shared by distinct hosts, so you can explore the combined report before using your own target list. The sample [target file](examples/targets.example.txt) demonstrates file syntax; it is not the source of the built-in demo data.

## Inspect one site

Existing single-target commands still work:

```text
python wp_telltale.py https://example.org/ --out reports/single-site
```

Replace `example.org` with your target. For a subdirectory installation, use its base URL, such as `https://example.org/blog/`. Single-target mode prints a console summary and writes HTML/JSON when `--out` is provided.

For a short first pass across a list:

```text
python wp_telltale.py --targets targets.txt --quick --out reports/first-pass
```

To retain response bodies as well as reports:

```text
python wp_telltale.py --targets targets.txt --sample 10 --evidence-dir evidence/case-001 --out reports/case-001
```

Use a fresh evidence directory for each run. Batch mode creates a separate, Windows-safe subdirectory for each target. Explicit report prefixes can replace existing report files, so use a new prefix when preserving a timeline.

## What the language results mean

**Available translations, a user's selected dashboard language, and the public site's language are different observations.**

| Result | Meaning |
| --- | --- |
| `en_US` | English, United States; built into the standard locale choices |
| `ru_RU` | Russian, Russia; one locale identifier, with a language and regional component |
| `en-US` on the homepage | The HTML form of English/US; the underscore/hyphen difference is not a language mismatch |
| Additional translation indicated | Public metadata advertises a locale beyond built-in `en_US` |
| Selected dashboard language: unknown | The collector has not read any individual account's active preference |
| Public language switcher: not assessed | Available WordPress translations do not establish that readers can switch article languages |

A logged-in editor could use Russian dashboard menus while readers see an English news site. Another editor could use English on the same installation. WordPress supports [per-user dashboard language preferences](https://make.wordpress.org/core/2016/11/07/user-admin-languages-and-locale-switching-in-4-7/); this is not exclusive to accounts with the Administrator role.

The main check reads the permitted `locale` values in the public REST users-route schema. Standard WordPress combines its default choices with [`get_available_languages()`](https://developer.wordpress.org/reference/functions/get_available_languages/), which discovers translation files and permits filtering. The tool reads that advertised list; it does not inspect the filesystem or a user's saved preference. See the official [schema implementation](https://developer.wordpress.org/reference/classes/wp_rest_users_controller/get_item_schema/).

For an English homepage advertising Russian, a defensible finding is:

> The site's public WordPress metadata advertises Russian translation availability while its homepage declares English. Individual dashboard language preferences were not observed.

This can justify follow-up. It does not establish that anyone currently uses Russian, that the articles have a Russian edition, or that the operator is in Russia. An unused pack, multilingual staff, a contractor, or an imported installation can explain the same observation. The [language guide](docs/LANGUAGES.md) explains the evidence and wording in detail.

## What gets collected and compared

| Collection | Examples | Use |
| --- | --- | --- |
| Advertised WordPress translations | Core users-route `locale.enum`; login-language choices when present | Configuration clues with their source fields |
| Public language declarations | HTML `lang`, Open Graph locales, `hreflang` links | Compare the public declarations with available translations |
| Site timezone | REST `timezone_string` and `gmt_offset` | Review configuration against a supplied baseline |
| Tracking identifiers | Google Analytics, Tag Manager, AdSense, Meta Pixel, Yandex Metrica, Baidu Analytics | Exact identifiers to investigate across targets |
| Domain references | Site URLs, canonical URLs, linked assets, inline CSS URLs | Migration remnants, content sources, custom services |
| Public author metadata | Display names, slugs, profile and avatar references | Reuse of public account-related details |
| Recent and earliest posts | Titles, slugs, local/GMT dates | Content remnants and stored publication offsets |
| Public media metadata | Filenames, source URLs, returned `image_meta` | Creation and content-source clues |
| Visible implementation details | Theme/plugin paths and REST namespaces | Distinctive shared construction details |

Batch reports identify **exact tracking-ID matches across distinct target hosts**. A `www` alias, a second path, or a different port on the same hostname is not counted as an independent host for that comparison. Matches carry source evidence; they are not ownership determinations. An identifier can belong to an agency or appear in copied code or an article example.

AdSense `ca-host-pub-...` platform accounts are excluded from publisher-ID pivots. Wildcard strings are excluded from plugin/theme names. CSS references are recorded without fetching their destinations. These changes address false positives and missing references found during early testing.

## Options

Run `python wp_telltale.py --help` for the complete executable option list.

| Option | Purpose |
| --- | --- |
| Positional URL | Inspect one WordPress installation |
| `--targets FILE` | Read targets from a text file; alias: `--target-file FILE` |
| `--demo` | Fictional offline single-target demonstration |
| `--demo-batch` | Fictional offline multi-target demonstration |
| `--out PREFIX` | Write report files; batch adds CSV to HTML/JSON |
| `--quick` | Skip public author, post, and media samples |
| `--sample N` | Maximum records per main sample; default 10, range 1–100 |
| `--expected-languages en,es` | Compare with supplied audience-language expectations |
| `--expected-timezone America/New_York` | Compare the literal configured zone name with an expected name |
| `--evidence-dir PATH` | Save response bodies and manifests in a fresh directory |
| `--timeout N` | Per-socket timeout in seconds; default 12, maximum 60 |
| `--delay N` | Minimum interval between request starts within a target; default 0.35 seconds |
| `--max-requests N` | Per-target request budget, including redirects and failed attempts; default 20, range 1–100 |
| `--version` | Print the installed version |

Choose one input mode: a positional URL, a target file, `--demo`, or `--demo-batch`. Expected-language and timezone options apply to every target in a batch; omit them or split the list when audiences differ.

## Scope and interpretation

The collector makes bounded public GET requests and, where needed, OPTIONS requests for REST schema discovery. This is active collection visible in server logs. Each target is restricted to its hostname and `www` counterpart, with supported HTTP-to-HTTPS canonicalization. Discovered external references are recorded without being followed.

A failed request or missing field means **unknown**. It does not prove that a site is offline, that WordPress is absent, or that a translation is not installed. A successful metadata check also does not mean every sample succeeded. Review the coverage and request records in the report.

The collector does not authenticate, execute the site's JavaScript, paginate through its full history, or perform DNS, RDAP, certificate-transparency, or historical-infrastructure research. The timeout is per socket operation, not a deadline for an entire target. Responses are capped at 3 MiB each. See the [investigation guide](docs/INVESTIGATION_GUIDE.md) for how to combine these observations with other evidence.

### Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Every accepted target produced WordPress REST or language evidence, with no rejected inputs; some individual fields may still be unavailable |
| `1` | At least one target was unknown or failed, or a batch contained rejected input lines |
| `2` | Argument, input-file, or local output/evidence error |
| `130` | Keyboard interruption; batch mode attempts to save completed work and partial collection information |

Exit codes describe collection status. They do not classify targets as benign or suspicious.

## Documentation and development

- [Language results explained](docs/LANGUAGES.md)
- [Target files, combined reports, JSON/CSV layout, and troubleshooting](docs/BATCH_REPORTS.md)
- [Investigation workflow, research examples, and classroom exercise](docs/INVESTIGATION_GUIDE.md)
- [Changes in this release](CHANGELOG.md)

The collector remains one Python file for easy review and classroom use. Run the offline regression suite with:

```text
python -m unittest discover -s tests -v
```

Tests use fictional fixtures and local HTTP services. The GitHub workflow defines Ubuntu and Windows jobs for Python 3.10, 3.12, and 3.13. When contributing a fixture, replace real personal information and investigation indicators with fictional values. Collected reports, response bodies, and working target lists belong outside the repository's published examples.

Useful next additions include Matomo endpoint/site-ID extraction, comparisons between saved runs, and optional infrastructure enrichment. Those are follow-up ideas, not features claimed in this release.

## Credit and license

The locale check was inspired by Max Lesser's November 20, 2025 [investigation of a suspected virtual-espionage network](https://www.memeticwarfare.io/p/guest-post-chinese-virtual-espionage). WP Telltale preserves the distinction between an available translation and an observed active account preference.

An [Argelius Labs](https://github.com/ArgeliusLabs) project. MIT license; see [LICENSE](LICENSE).
