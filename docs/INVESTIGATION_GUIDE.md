# Investigating WordPress configuration clues

WP Telltale helps answer a narrow question: **What does this site's public technical footprint tell us to investigate next?**

A language configuration can expose a detail that is not visible in a site's public content. Exact account identifiers and unusual infrastructure references can help connect that detail to other assets. Establishing the operator, purpose, or sponsor requires additional evidence and consideration of other explanations.

This guide describes the implemented checks in version 0.2.0 and useful follow-up work. Items labeled follow-up are not automatically performed by the tool. The [language guide](LANGUAGES.md) explains locale semantics; the [batch guide](BATCH_REPORTS.md) describes text files, report formats, and collection status.

## 1. Begin with the site's claimed identity

Before interpreting a mismatch, record what you are comparing it against: the claimed organization, editorial location, intended audience, and languages in which it publishes. Preserve the page and date supporting those claims.

A site that presents itself as a monolingual local publication calls for a different comparison from an international consulting company with multilingual employees. An unexpected setting is a question to investigate, not a conclusion.

For example:

```bash
python3 wp_telltale.py https://example.org \
  --expected-languages en \
  --expected-timezone America/New_York \
  --evidence-dir case-evidence \
  --out case-report
```

Use an installation URL rather than an article URL when WordPress is installed in a subdirectory. The expected values are supplied by the analyst; they are not automatically inferred from a country label or a domain suffix.

### Start with a group of targets

For targets gathered from multiple research sources, keep a case-specific text file with one domain or installation URL per line. Whole-line comments can record the source or selection rationale:

```text
# Fictional examples; replace with domains supported by your case research.
https://newsroom.example/
publisher.example
```

Run one combined report:

```text
python wp_telltale.py --targets targets.txt --out reports/case-001 --evidence-dir evidence/case-001
```

Use the HTML overview to identify review cues and exact tracking-ID matches. Then open the relevant target details and source evidence. The supplied expected-language and timezone baselines apply to every target; omit them or split the list if the sites serve different audiences.

## 2. Separate the kinds of language evidence

| Observation | What it establishes | What remains unknown |
| --- | --- | --- |
| User-schema `locale.enum` includes `zh_CN` | The API advertises that locale as an available user-interface choice | Whether any current user selected it, who installed it, and why |
| Login language selector includes `ru_RU` | The public login interface offers that locale | A particular account's language preference |
| Homepage has `lang="en-US"` | The page declares that language | The language of every visible passage, the backend language, or the operator's location |
| Open Graph metadata uses another locale | A separate metadata declaration is present | Whether it is intentional, inherited, or stale |
| `hreflang` links advertise several languages | The site advertises alternate language versions | Whether those versions exist or are maintained; external links are not fetched |
| Sampled post or filename uses another script | That string contains characters from the observed script | A complete language identification or the identity of its author |

WordPress's [user-schema implementation](https://developer.wordpress.org/reference/classes/wp_rest_users_controller/get_item_schema/) constructs locale choices from default values and available translations. Its [`get_available_languages()` implementation](https://developer.wordpress.org/reference/functions/get_available_languages/) discovers translation files and has a filter that can alter the result. Accordingly, the collector describes an advertised configuration rather than claiming it has inspected the server's filesystem.

The default `en_US` choice is not evidence of an extra installed pack. An empty default-language choice is not an unknown foreign language. `en_US` and the homepage form `en-US` denote the same language/region. `ru_RU` is one Russian locale, not two separate options.

A logged-in account can select a dashboard language independently of the public site language. This applies to roles such as Editor as well as Administrator. An English public site can therefore coexist with a Russian dashboard preference without any public language-switching button. The scan only establishes that the translation is advertised as available; it does not establish that a current account has selected it. WordPress explains the distinction in its [per-user dashboard language documentation](https://make.wordpress.org/core/2016/11/07/user-admin-languages-and-locale-switching-in-4-7/).

WordPress's [login-language selector](https://make.wordpress.org/core/2021/12/20/introducing-new-language-switcher-on-the-login-screen-in-wp-5-9/) provides another public view of available languages. Because it normally uses the same underlying configuration as the schema, matching results are corroborating views of one fact, not two independent attribution indicators.

### Why the schema can be public when the setting is private

An API can describe the fields and permitted values for an operation while requiring authentication to perform it. A locale enum in route metadata does not give permission to edit users and does not reveal a particular user's selected value. WP Telltale reads available metadata; it does not perform the described user-management operations.

## 3. Prioritize the clues

The ranking below describes the usefulness of a lead for further investigation. It is not a probability that a state or organization operates the site.

| Priority | Clue collected by WP Telltale | Good next question | Common alternative explanation |
| --- | --- | --- | --- |
| High | Exact advertising, analytics, or tag-manager IDs | Where else does this exact identifier appear, and during what period? | A shared agency account, copied template, or deliberately copied identifier |
| High | Unusual domain references in site URLs or assets | Does this reveal a previous domain, source site, or custom service used elsewhere? | Migration residue, syndication, stock assets, or a development environment |
| High | Unexpected advertised WordPress translations | Is the same unusual language configuration present across apparently separate sites? | Multilingual personnel, a contractor, an imported installation, or an unused pack |
| Medium | Named timezone inconsistent with claimed location | Does other evidence support operations in that zone? | Editorial scheduling preferences or a default configuration |
| Medium | Reused public author names, slugs, or avatar references | Does reuse involve distinctive identities and corroborating links? | Generic names, syndicated contributors, or a shared publisher |
| Medium | Non-generic theme/plugin paths and REST namespaces | Are custom components reused with the same unusual paths? | An agency's legitimate portfolio or a commercial product |
| Medium | Earliest post remnants and unusual slugs | What does the site's initial public content reveal about its setup or previous purpose? | Sample content left by a designer or an imported database |
| Medium | Public media filenames and `image_meta` | Is there a recurring creator, filename pattern, or source host? | Shared stock media, syndicated articles, or reused original files |
| Supporting | Recent post times and local/GMT differences | Is there a consistent publication pattern across a larger, independently justified sample? | Scheduling, bulk imports, automated publishing, or incorrect historical timestamps |
| Supporting | Common themes, plugins, or ordinary third-party domains | Is there anything distinctive beyond a popular component? | Widespread ordinary use |

### The account identifier is more useful than the service's nationality

Finding Yandex Metrica on an English-language site may merit a question. Finding the same distinctive counter ID on that site and a separately branded organization creates a concrete relationship to investigate. The same reasoning applies to Google, Meta, Baidu, and other platforms.

Batch reports compare exact tracking identifiers across distinct target hostnames and retain their available source evidence. A `www` alias, another path, or another port on the same hostname does not count as an independent matching host. The comparison is within the current batch; older report files are not automatically loaded.

Check the exact string in its source context. It may be embedded executable configuration, an example in an article, an old comment, or copied markup. The tool does not execute the page to verify that a tracker currently sends data. A shared agency account can connect otherwise independent publishers.

AdSense platform accounts beginning `ca-host-pub-` are excluded from publisher-ID pivots. A platform account describes a service relationship and can appear across unrelated sites. A generic analytics vendor or CDN hostname is likewise not an account match.

### Timezone versus geography

Prefer retaining a named zone such as `Asia/Shanghai` or `Europe/Moscow` over converting it immediately into a country claim. Many regions share numerical offsets. Fixed offsets also omit daylight-saving rules.

The REST root's timezone fields describe WordPress configuration. WordPress's [REST index implementation](https://developer.wordpress.org/reference/classes/wp_rest_server/get_index/) reads `gmt_offset` and `timezone_string` from site options. Neither field exposes a visitor's or administrator's device timezone.

WP Telltale compares supplied timezone names literally. An alias such as a historical zone name can differ from your baseline while representing equivalent rules; inspect such a cue before calling it a geographic inconsistency. Language comparisons similarly use base languages, so `en_US` and `en_GB` match an `en` expectation.

Post fields provide another view: `date` is a publication timestamp in the site's timezone and `date_gmt` is its GMT counterpart. See the official [Posts API reference](https://developer.wordpress.org/rest-api/reference/posts/). A difference between them can reflect the setting in use when a post was stored. A mismatch with today's setting may be historical, not deceptive. Publication times are not evidence of login times or working hours.

### Public media metadata has a limited scope

WordPress exposes media-specific details and source URLs through its [Media API](https://developer.wordpress.org/rest-api/reference/media/). WP Telltale examines the metadata returned there. It does not download every original file or run a full EXIF/video analysis. An empty `image_meta` object does not establish that the original file contains no metadata.

## 4. Understand the collection boundary

The default collection includes one bounded sample of public authors, recent posts, and media. Each main response requests at most 10 records by default. A separate earliest-post request asks for up to five records. `--sample` changes the main sample limit from 1 to 100; the earliest sample remains limited to `min(sample, 5)`. No history pagination occurs.

The maximum request count defaults to 20 per target and includes redirects and failed attempts. Batch collection proceeds sequentially, with a separate budget for each target. `--quick` skips the author, post, and media samples. Blocks, failures, partial responses, and budget exhaustion can leave parts of the report incomplete.

For each target, the tool contacts the specified site and permitted `www` canonical equivalent. It records external domains, including bounded inline-CSS URL references, but does not follow them. The collection is visible in web-server logs. It does not use a browser session, authenticate, execute the site's JavaScript, submit forms, or guess user IDs.

For a larger investigation, first decide what additional collection would resolve a specific question. A higher record count is not automatically better evidence.

## 5. Preserve a clear evidence trail

Keep the JSON report, HTML report, batch CSV when applicable, collection time, command used, and any optional response-body files together. Record which values were supplied as expectations. For a finding that matters, retain its source URL and exact field or snippet.

A useful case note separates four statements:

1. **Observation:** A named response advertised a particular locale or contained a particular account ID at the collection time.
2. **Technical interpretation:** Standard WordPress normally derives the locale choices from available translations, or the markup references a named account identifier.
3. **Hypothesis:** A shared developer, inherited installation, or common operator could explain the finding.
4. **Follow-up:** A specified independent source could distinguish those explanations.

A saved body hash helps identify the bytes examined. It does not prove that the origin server was truthful or that the content has always been the same. Preserve collection limitations alongside positive results.

If you rerun the tool, use a different output prefix or case directory so the earlier observation remains available. Record a configuration change as a change; do not silently substitute the newer value for the older one.

## 6. Useful follow-up work

The following are investigation ideas beyond version 0.2.0:

| Follow-up | What it can resolve | Constraint |
| --- | --- | --- |
| Compare saved runs from different dates | New or removed locales, changed identifiers, redirects, and possible domain repurposing | Current matching is within one batch; historical report comparison is not implemented |
| Extract Matomo/Piwik tracker endpoints and site IDs | Additional precise analytics relationships | Not implemented; a site ID should be paired with its tracker endpoint because the number alone is not globally unique |
| Historical DNS and certificate-transparency research | Earlier infrastructure relationships and domain timelines | Shared hosting and certificate services require context |
| RDAP and historical registration records | Registration timing and available registrant information | Privacy services and ownership changes can limit interpretation |
| Web archives | Earlier branding, contacts, locale remnants, and content changes | Archive timestamps are capture times, and missing captures prove little |
| Selected original-file metadata | Editing software, source filenames, creator strings, and timestamp offsets | Download only files relevant to the question; metadata can be inherited or manipulated |
| Corporate and professional records | Whether claimed organizations and staff can be independently corroborated | An absence in one database is not proof of fabrication |
| Larger publication comparisons | Repeated phrasing, synchronized articles, and content supply chains | Syndication and scheduled posts can mimic coordination |

For TLS and DNS research, distinguish an origin server from a CDN edge. Common hosting providers, registrars, and certificate issuers have many unrelated customers.

## 7. Research examples

### Advertised locales in a suspected virtual-espionage network

Max Lesser's November 20, 2025 [Memetic Warfare guest report](https://www.memeticwarfare.io/p/guest-post-chinese-virtual-espionage) describes Chinese locale choices in the WordPress schemas of sites presenting themselves as consulting businesses. Its investigation also used infrastructure and shared web-resource relationships. This is the direct inspiration for the locale check. Treat its published assessments as the author's findings, not live results produced by WP Telltale.

### Exact account reuse in PAPERWALL

Citizen Lab's February 7, 2024 [PAPERWALL report](https://citizenlab.ca/research/paperwall-chinese-websites-posing-as-local-news-outlets-with-pro-beijing-content/) linked network domains to Haimai company assets through a shared AdSense publisher identifier. The report also considered repeated WordPress authors, content, and infrastructure. It explicitly explains why Tencent hosting on its own is insufficient for attribution. This illustrates the value of an exact account-level pivot over a broad provider association.

### Media and infrastructure in Doppelgänger

Qurium's 2022 [Doppelgänger investigation](https://www.qurium.org/alerts/under-the-hood-of-a-doppelganger/) combined certificate and infrastructure relationships with redirects and video metadata, including filenames and editing details. It is a useful model for an additional media-analysis stage. Preserve numerical timestamp offsets as observed values rather than mapping one offset to a unique country or city.

## 8. Classroom exercise

Generate the fictional combined report:

```text
python wp_telltale.py --demo-batch --out reports/classroom-demo
```

The demo makes no network requests. For a smaller single-site exercise, use `--demo` instead.

Ask participants to complete the following tasks:

1. Identify the declared public language, advertised WordPress translations, and site timezone. Record the source of each observation. Explain why the selected dashboard language remains unknown.
2. Write three plausible explanations for the differences, including ordinary operational explanations.
3. Inspect an exact identifier shared across the demo hosts. Find its source context and explain why an account match is more useful than a generic theme or provider.
4. Inspect earliest-post or media clues. Decide whether they add independent evidence or repeat the same underlying configuration history.
5. Check the collection statuses and identify what an unknown result leaves unresolved.
6. Write a short assessment that clearly separates observations, inference, and unresolved questions.

A good answer might conclude that a site merits further investigation and identify the next useful evidence. It need not assign a country or discover a campaign.

### Discussion prompts

- Would your assessment change if the site employed a multilingual contractor?
- Could all the language clues come from one copied WordPress installation?
- Does a shared account identify an operator, an agency, or simply copied code?
- What would you expect to observe if this were a compromised legitimate site?
- Which independent source would most change your confidence?

## Sources

- WordPress, user schema: https://developer.wordpress.org/reference/classes/wp_rest_users_controller/get_item_schema/
- WordPress, available language discovery: https://developer.wordpress.org/reference/functions/get_available_languages/
- WordPress, individual dashboard languages: https://make.wordpress.org/core/2016/11/07/user-admin-languages-and-locale-switching-in-4-7/
- WordPress, user-locale preference: https://developer.wordpress.org/reference/functions/get_user_locale/
- WordPress, login-language selector: https://make.wordpress.org/core/2021/12/20/introducing-new-language-switcher-on-the-login-screen-in-wp-5-9/
- WordPress, REST index fields: https://developer.wordpress.org/reference/classes/wp_rest_server/get_index/
- WordPress, Posts API: https://developer.wordpress.org/rest-api/reference/posts/
- WordPress, Media API: https://developer.wordpress.org/rest-api/reference/media/
- Max Lesser, suspected virtual-espionage network: https://www.memeticwarfare.io/p/guest-post-chinese-virtual-espionage
- Citizen Lab, PAPERWALL: https://citizenlab.ca/research/paperwall-chinese-websites-posing-as-local-news-outlets-with-pro-beijing-content/
- Qurium, Doppelgänger: https://www.qurium.org/alerts/under-the-hood-of-a-doppelganger/
