# Understanding WordPress language evidence

A result such as `en_US, ru_RU` means that public metadata advertises two WordPress interface locale choices. It does not establish that a visitor can switch the news articles to Russian or that a particular account uses Russian in the dashboard.

## Four separate questions

| Question | Evidence the collector can obtain | Limit |
| --- | --- | --- |
| What language does the public page declare? | Homepage HTML `lang`, Open Graph locale fields, and advertised `hreflang` alternatives | These are declarations, not full natural-language analysis or proof that every alternate page works |
| Which WordPress translations are advertised as available? | Core users-route locale enums and, where present, the login-language selector | Public configuration can be filtered, cached, or customized |
| Which language has a particular account selected? | Not collected | The schema's list of choices is not that account's saved value |
| Can ordinary readers switch the content language? | Not assessed by this collector | Available WordPress interface translations do not establish a public multilingual content system |

The distinction matters because the public language, available translations, and selected account language can all differ without a problem.

## Read the locale codes

| Code | Meaning |
| --- | --- |
| `en_US` | English, United States |
| `en-US` | The HTML-style representation of English, United States |
| `ru_RU` | Russian, Russia |
| `zh_CN` | The WordPress locale commonly used for Simplified Chinese |
| `zh_TW` | The WordPress locale commonly used for Traditional Chinese, Taiwan |
| `vi` | Vietnamese; some WordPress locale identifiers do not have a region suffix |
| Empty string, `""` | The default locale choice in the users schema |

`ru_RU` is one identifier. The lowercase component identifies the language and the uppercase component identifies the region. It is not two Russian accounts or two Russian options. `en_US` and `en-US` are the same language/region designation expressed with different separators.

Standard WordPress explicitly includes `en_US` in the allowed choices. Its presence alone is not evidence that an additional English pack was installed. The tool excludes this built-in choice and the empty default entry when listing additional translations.

## Where the observation comes from

The public REST index can describe the fields accepted by the users route. One of those fields is `locale`; its enum lists permitted values. A fictional fragment is:

```json
{
  "locale": {
    "type": "string",
    "enum": ["", "en_US", "ru_RU"]
  }
}
```

The main field locations are the users-route entries under:

```text
routes["/wp/v2/users"].endpoints[*].args.locale.enum
```

Related core routes for a specific user and the current user can repeat that configuration. The collector only accepts recognized core user-schema locations for this check. An arbitrary plugin's catalog of supported languages is not treated as proof of available WordPress translations.

When a usable REST index is available but lacks the relevant enum, the collector can try an OPTIONS schema request. It may read `schema.properties.locale.enum` or equivalent route argument metadata. That is a read of the API's schema; it does not create or edit an account.

The [WordPress users-schema source](https://developer.wordpress.org/reference/classes/wp_rest_users_controller/get_item_schema/) combines the empty default and `en_US` with the result of [`get_available_languages()`](https://developer.wordpress.org/reference/functions/get_available_languages/). The latter discovers `.mo` and `.l10n.php` translation files in the language directory and permits filtering of the returned list.

Accordingly, the finding is **advertised translation availability consistent with the standard mechanism**. It is not a direct filesystem inventory. An additional enum value gives more specific evidence than the simple fact that WordPress supports that language in general.

## What “admin interface” means

WordPress permits a logged-in user to select a preferred dashboard language in their profile. The preference is associated with the account, and accounts with roles such as Editor or Author can also have one. The phrase “admin interface” describes the dashboard area; it does not restrict this feature to the Administrator role.

For example, a hypothetical site could publish English articles, have one editor use Russian dashboard controls, and have another editor use English controls. That requires no public Russian-language button. WordPress documents this separation in its [user admin languages explanation](https://make.wordpress.org/core/2016/11/07/user-admin-languages-and-locale-switching-in-4-7/). The [user-locale function](https://developer.wordpress.org/reference/functions/get_user_locale/) retrieves a saved preference and otherwise falls back to the site locale.

The translation resources themselves are not exclusive to the dashboard. They can also supply translated WordPress-generated text in other contexts. Installing a WordPress translation does not automatically translate an editor's articles or add a multilingual-content switcher.

WP Telltale does not authenticate and does not retrieve an individual account's saved language preference. The report therefore keeps the selected dashboard language unknown. The older JSON field `interface_locales.admin_selected_language` remains for compatibility; it must not be read as an observed active setting.

## The public login-language selector

Some WordPress login pages offer a language dropdown. It changes the language of the login interface, not the language of a site's articles and not necessarily an account's saved preference. See WordPress's [login-screen language switcher documentation](https://make.wordpress.org/core/2021/12/20/introducing-new-language-switcher-on-the-login-screen-in-wp-5-9/).

The collector can record the available choices in that selector. A missing selector does not establish that no extra translations exist. Theme/plugin behavior, customized login paths, filtering, or a blocked page can reduce visibility.

The REST enum and login dropdown normally draw on the same translation configuration. If they agree, those are two views of one underlying configuration fact, not two independent reasons to attribute a site.

## How to read a review cue

Suppose the homepage declares `en-US` and the schema advertises `en_US` and `ru_RU`.

The supported observation is:

> Russian translation availability is advertised while the homepage declares English. The selected dashboard language is unknown.

An unsupported conclusion would be:

> The administrator is currently operating the dashboard in Russian.

The tool has not measured the latter. It compares the homepage declaration with the available translations. The comparison uses base languages: `en_US` and `en_GB` both match an `en` baseline, while `ru_RU` differs.

The optional `--expected-languages` argument adds the analyst's own baseline. It does not infer an expected language from a country's name or domain suffix. In a batch, the same supplied baseline applies to every target, so split lists or omit the baseline when audiences differ.

## Why this can still be useful

An available translation can preserve a clue about site setup or maintenance, even when it is not visibly used on the public pages. Possible explanations include current multilingual staff, a developer's preference, a previous operator, an imported backup, or an unused language pack.

The value comes from the surrounding evidence. An unusual translation alongside an exact account-identifier match, a legacy asset domain, and independently documented organizational connections can motivate a focused investigation. Language alone does not establish a person's location, nationality, role, or state affiliation.

Use the same standard for all languages. Chinese-language configuration can be ordinary in Singapore or Malaysia; Russian-language configuration can be ordinary in a site maintained by a multilingual contractor. Public positioning supplies a comparison baseline, not a prohibition on staff using another language.

## Recording a finding

Preserve the source URL, timestamp, JSON pointer or HTML snippet, and response hash. A report should separate the observation from the proposed explanation:

1. **Observed:** At the collection time, a named REST response advertised `ru_RU` in the core users-route locale enum.
2. **Technical interpretation:** Standard WordPress derives additional choices from available translation resources, subject to filtering.
3. **Unresolved:** No individual account's selected language, installation history, operator identity, or location was observed.
4. **Next check:** Compare a distinctive account identifier or legacy domain with independently collected evidence.

Missing or blocked metadata remains unknown. An enum containing only `en_US` means that this source advertised no additional locale at the collection time; it is not a complete forensic inventory of every translation-related file or plugin on the server.
