#!/usr/bin/env python3
"""WP Telltale: bounded public WordPress metadata collection. Python 3.10+.

GET the REST index to read the users route's locale enum. Do not confuse
available translations with any administrator's selected language or identity.
See README.md for interpretation and official WordPress source references.
"""

from __future__ import annotations

import argparse
import base64
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import html
from html.parser import HTMLParser
import http.client
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, unquote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


VERSION = "0.2.0"
MAX_BYTES = 3 * 1024 * 1024
MAX_CSS_REFERENCES = 500
LOCALE_RE = re.compile(r"^[a-z]{2,3}(?:[_-][A-Za-z0-9]+)*$")
CORE_USER_ROUTES = {
    "/wp/v2/users", "/wp/v2/users/me", "/wp/v2/users/{id}",
    r"/wp/v2/users/(?P<id>[\d]+)", r"/wp/v2/users/(?P<id>\d+)",
}
LIMITATION = (
    "Advertised WordPress translations are configuration clues. They do not reveal "
    "any account's selected dashboard language, nationality, location, or state affiliation. "
    "They do not establish a visitor-facing language switcher. "
    "Missing data means unknown. Hosting, agencies, imports, multilingual staff, "
    "copied templates, and compromised sites can explain apparent inconsistencies."
)
LOCALE_NAMES = {
    "en_US": "English (United States)", "en_GB": "English (United Kingdom)",
    "en_AU": "English (Australia)", "ru_RU": "Russian", "zh_CN": "Simplified Chinese",
    "zh_TW": "Traditional Chinese (Taiwan)", "zh_HK": "Traditional Chinese (Hong Kong)",
    "vi": "Vietnamese", "th": "Thai", "id_ID": "Indonesian", "ms_MY": "Malay",
    "ko_KR": "Korean", "ja": "Japanese", "uk": "Ukrainian", "de_DE": "German",
    "fr_FR": "French", "es_ES": "Spanish (Spain)", "pt_BR": "Portuguese (Brazil)",
}


def locale_label(value):
    name = LOCALE_NAMES.get(value.replace("-", "_"))
    return name + " (" + value + ")" if name else value


def terminal_text(value):
    """Keep collected strings from issuing terminal controls or bidi overrides."""
    return re.sub(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]", "?", str(value))


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_target(value):
    """Accept a site root or installation directory, never a credentialed URL."""
    value = value.strip()
    if not value or re.search(r"[\x00-\x20\x7f]", value):
        raise ValueError("Supply a domain or HTTP(S) site URL without whitespace.")
    if "://" not in value:
        # Bare domain:port is useful for labs; other schemes are not site roots.
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", value) and not re.match(r"^[^/:]+:\d+(?:/|$)", value):
            raise ValueError("Only HTTP and HTTPS targets are supported.")
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only HTTP and HTTPS targets are supported.")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Embedded credentials are not supported.")
    if parsed.query or parsed.fragment:
        raise ValueError("Supply the site's base URL without a query or fragment.")
    try:
        parsed.port
        host = parsed.hostname.encode("idna").decode("ascii").lower()
    except (ValueError, UnicodeError) as exc:
        raise ValueError("Invalid hostname or port.") from exc
    host = "[" + host + "]" if ":" in host else host
    netloc = host + (":" + str(parsed.port) if parsed.port else "")
    return urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/") + "/", "", ""))


def api_url(root, route, params=None):
    """Append a REST route, including installations using ?rest_route=/."""
    p = urlsplit(root)
    query = dict(parse_qsl(p.query, keep_blank_values=True))
    if "rest_route" in query:
        query["rest_route"] = "/" + route.lstrip("/")
        path = p.path
    else:
        path = p.path.rstrip("/") + "/" + route.lstrip("/")
    query.update(params or {})
    return urlunsplit((p.scheme, p.netloc, path, urlencode(query), ""))


def language_base(value):
    return re.split(r"[_-]", value.strip().lower())[0]


def json_pointer(parts):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def extract_locale_enums(payload, options=False):
    """Only core users schemas qualify; arbitrary plugin locale catalogs do not."""
    hits = []
    if not isinstance(payload, dict):
        return hits

    def add(node, path):
        if isinstance(node, dict) and isinstance(node.get("enum"), list):
            values = [x for x in node["enum"] if isinstance(x, str)]
            hits.append({"path": json_pointer(path + ["enum"]), "values": values})

    def route_args(route, path):
        if not isinstance(route, dict) or not isinstance(route.get("endpoints"), list):
            return
        for i, endpoint in enumerate(route["endpoints"]):
            if isinstance(endpoint, dict) and isinstance(endpoint.get("args"), dict):
                add(endpoint["args"].get("locale"), path + ["endpoints", i, "args", "locale"])

    routes = payload.get("routes")
    if isinstance(routes, dict):
        for name, route in routes.items():
            if name in CORE_USER_ROUTES:
                route_args(route, ["routes", name])
    if options:
        schema = payload.get("schema")
        if isinstance(schema, dict) and isinstance(schema.get("properties"), dict):
            add(schema["properties"].get("locale"), ["schema", "properties", "locale"])
        route_args(payload, [])
    return hits


class PageParser(HTMLParser):
    def __init__(self, url):
        super().__init__(convert_charrefs=True)
        self.url = url
        self.data = {"html_language": None, "login_languages": [], "meta": {},
                     "references": [], "hreflangs": [], "rest_links": []}
        self.language_select = False
        self.in_style = False
        self.style_chunks = []
        self.css_reference_count = 0

    def css_references(self, value):
        """Record bounded inline CSS URLs without fetching any referenced resource."""
        pattern = r"url\(\s*(?:\"([^\"]*)\"|'([^']*)'|([^\s)]*))\s*\)"
        for match in re.finditer(pattern, value, re.I):
            if self.css_reference_count >= MAX_CSS_REFERENCES:
                break
            raw = next((group for group in match.groups() if group is not None), "").strip()
            if not raw or raw.startswith("#"):
                continue
            try:
                address = urljoin(self.url, raw)
                parsed = urlsplit(address)
                if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                    continue
            except ValueError:
                continue
            self.data["references"].append({"url": address, "kind": "inline CSS url"})
            self.css_reference_count += 1

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "style":
            self.in_style = True
            self.style_chunks = []
        if isinstance(attrs.get("style"), str):
            self.css_references(attrs["style"])
        if tag == "html":
            self.data["html_language"] = attrs.get("lang") or attrs.get("xml:lang")
        if tag == "select":
            self.language_select = (attrs.get("id") == "language-switcher-locales" or attrs.get("name") == "wp_lang")
        if tag == "option" and self.language_select and isinstance(attrs.get("value"), str):
            self.data["login_languages"].append(attrs["value"])
        if tag == "meta":
            key = attrs.get("property") or attrs.get("name") or attrs.get("http-equiv")
            if key and attrs.get("content"):
                self.data["meta"].setdefault(key.lower(), []).append(attrs["content"])
                if key.lower() in {"og:url", "og:image", "og:video", "twitter:image"}:
                    try:
                        address = urljoin(self.url, attrs["content"])
                        if urlsplit(address).scheme in {"http", "https"}:
                            self.data["references"].append({"url": address, "kind": key.lower()})
                    except ValueError:
                        pass
        raw = attrs.get("href") if tag in {"a", "link"} else attrs.get("src") if tag in {"script", "img", "source", "iframe"} else None
        if raw:
            try:
                address = urljoin(self.url, raw)
                valid = urlsplit(address).scheme in {"http", "https"}
            except ValueError:
                valid = False
            if valid:
                kind = (attrs.get("rel") or "link") if tag in {"a", "link"} else tag
                self.data["references"].append({"url": address, "kind": kind})
                if "https://api.w.org/" in (attrs.get("rel") or "").split():
                    self.data["rest_links"].append(address)
                if attrs.get("hreflang"):
                    self.data["hreflangs"].append({"language": attrs["hreflang"], "url": address})

    def handle_endtag(self, tag):
        if tag == "select":
            self.language_select = False
        if tag == "style":
            self.css_references("".join(self.style_chunks))
            self.style_chunks = []
            self.in_style = False

    def handle_data(self, data):
        if self.in_style:
            self.style_chunks.append(data)


def analyze_html(text, url):
    parser = PageParser(url)
    parser.feed(text)
    if parser.in_style:
        parser.css_references("".join(parser.style_chunks))
    result = parser.data
    result["login_languages"] = sorted(set(result["login_languages"]))
    result["asset_plugins"], result["asset_themes"] = [], []
    for kind in ("plugins", "themes"):
        paths = re.findall(r"/wp-content/" + kind + r"/([^/?#\s\"'<>]+)", text)
        result["asset_" + kind] = sorted({value for value in paths if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value)})
    patterns = {
        "google_analytics_ga4": r"\bG-[A-Z0-9]{6,16}\b",
        "google_analytics_ua": r"\bUA-\d{4,15}-\d+\b",
        "google_tag_manager": r"\bGTM-[A-Z0-9]{4,16}\b",
        # A word boundary alone also matches pub-* inside ca-host-pub-*.
        "google_adsense": r"(?<![\w-])(?:ca-)?pub-\d{10,20}(?![\w-])",
        "meta_pixel": r"fbq\(\s*['\"]init['\"]\s*,\s*['\"](\d{6,25})['\"]",
        "yandex_metrica": r"\bym\(\s*(\d{4,20})\s*,\s*['\"]init['\"]",
        "baidu_analytics": r"hm\.baidu\.com/hm\.js\?([a-fA-F0-9]{32})\b",
    }
    result["tracking_ids"] = {}
    result["tracking_evidence"] = []
    result["excluded_tracking_ids"] = [
        {"kind": "google_adsense_platform", "value": value,
         "reason": "Platform account metadata; excluded from site publisher pivots and cross-host matches."}
        for value in sorted(set(re.findall(r"(?<![\w-])ca-host-pub-\d{10,20}(?![\w-])", text)))
    ]
    for kind, pattern in patterns.items():
        values = set()
        for match in re.finditer(pattern, text):
            value = match.group(1) if match.lastindex else match.group(0)
            values.add(value)
            if len(result["tracking_evidence"]) < 100:
                snippet = text[max(0, match.start() - 70):match.end() + 70]
                result["tracking_evidence"].append({"kind": kind, "value": value, "source_url": url, "snippet": snippet})
        if values:
            result["tracking_ids"][kind] = sorted(values)
    return result


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    """Read-only requests, no cookies, TLS verification, bounded bytes and scope."""
    def __init__(self, target, timeout=12, delay=0.35, max_requests=20, evidence_dir=None, demo=False):
        self.target = target
        self.timeout = timeout
        self.delay = delay
        self.max_requests = max_requests
        self.evidence_dir = Path(evidence_dir) if evidence_dir else None
        self.demo = demo
        self.records = []
        self.warnings = []
        self.last_request = 0.0
        self.opener = build_opener(NoRedirect())
        if self.evidence_dir:
            self.evidence_dir.mkdir(parents=True, exist_ok=True)

    def allowed(self, address):
        try:
            a, b = urlsplit(self.target), urlsplit(address)
            strip_www = lambda s: (s or "").lower().removeprefix("www.")
            if b.scheme not in {"http", "https"} or b.username is not None or b.password is not None:
                return False
            if a.scheme == "https" and b.scheme != "https":
                return False
            if strip_www(a.hostname) != strip_www(b.hostname):
                return False
            pa, pb = a.port or (443 if a.scheme == "https" else 80), b.port or (443 if b.scheme == "https" else 80)
            return pa == pb or (a.scheme == "http" and pa == 80 and b.scheme == "https" and pb == 443)
        except (ValueError, AttributeError):
            return False

    def fetch(self, address, method="GET", purpose="metadata"):
        if method not in {"GET", "OPTIONS"}:
            raise ValueError("Only GET and OPTIONS are supported.")
        for hop in range(5):
            if not self.allowed(address):
                self.warnings.append("Not followed outside target host/port scope: " + address)
                return None
            if len(self.records) >= self.max_requests:
                self.warnings.append("Request budget reached; remaining checks were not attempted.")
                return None
            if not self.demo and self.last_request:
                time.sleep(max(0, self.delay - (time.monotonic() - self.last_request)))
            record = {"id": len(self.records) + 1, "url": address, "method": method,
                      "purpose": purpose, "observed_at": utc_now(), "status": None,
                      "headers": {}, "bytes": 0, "truncated": False, "error": None}
            body = b""
            started = self.last_request = time.monotonic()
            try:
                if self.demo:
                    status, headers, body = demo_response(address, method)
                    record.update(status=status, headers=headers)
                else:
                    request = Request(address, method=method, headers={
                        "User-Agent": "WP-Telltale/" + VERSION + " (public metadata research)",
                        "Accept": "application/json, text/html;q=0.9, */*;q=0.5",
                        "Accept-Encoding": "identity",
                    })
                    try:
                        response = self.opener.open(request, timeout=self.timeout)
                    except HTTPError as exc:
                        response = exc
                    with response:
                        record["status"] = response.code
                        # Keep relevant public headers; never save Set-Cookie.
                        selected = {"content-type", "content-language", "server", "x-powered-by", "link",
                                    "location", "date", "last-modified", "etag", "x-wp-total", "retry-after"}
                        record["headers"] = {k.lower(): v for k, v in response.headers.items() if k.lower() in selected}
                        body = response.read(MAX_BYTES + 1)
                if len(body) > MAX_BYTES:
                    body = body[:MAX_BYTES]
                    record["truncated"] = True
                    record["error"] = "Response exceeded 3 MiB; stored/hash bytes are a truncated prefix."
            except (URLError, OSError, ValueError, http.client.HTTPException) as exc:
                record["error"] = str(exc)
            record["elapsed_seconds"] = round(time.monotonic() - started, 3)
            record["bytes"] = len(body)
            record["sha256"] = hashlib.sha256(body).hexdigest()
            if self.evidence_dir and body:
                path = self.evidence_dir / (f"{record['id']:02d}_" + method.lower() + ".bin")
                # A new run must not silently overwrite prior evidence.
                with path.open("xb") as handle:
                    handle.write(body)
                record["evidence_file"] = str(path)
            self.records.append(record)
            result = dict(record, body=body)
            if record["status"] in {301, 302, 303, 307, 308}:
                location = record["headers"].get("location")
                if location and hop < 4:
                    try:
                        next_address = urljoin(address, location)
                        downgrade = urlsplit(address).scheme == "https" and urlsplit(next_address).scheme != "https"
                    except ValueError:
                        self.warnings.append("Invalid redirect location received: " + str(location))
                        return result
                    if downgrade:
                        self.warnings.append("HTTPS downgrade redirect not followed: " + next_address)
                        return result
                    address = next_address
                    continue
                self.warnings.append("Redirect chain not completed: " + address)
            # A rate-limit response stops collection rather than immediately retrying.
            if record["status"] == 429:
                self.max_requests = len(self.records)
                self.warnings.append("HTTP 429 received; collection stopped.")
            return result
        return None


def response_text(response):
    if not response:
        return ""
    content_type = response["headers"].get("content-type", "")
    charset = re.search(r"charset\s*=\s*[\"']?([\w-]+)", content_type, re.I)
    try:
        return response["body"].decode(charset.group(1) if charset else "utf-8", errors="replace")
    except LookupError:
        return response["body"].decode("utf-8", errors="replace")


def response_json(response):
    if not response or response["status"] != 200 or response["truncated"] or response["error"]:
        return None
    try:
        return json.loads(response["body"])
    except (ValueError, UnicodeError, RecursionError):
        return None


def source(response, path=None):
    result = {"url": response["url"], "request_id": response["id"],
              "observed_at": response["observed_at"], "sha256": response["sha256"]}
    if path is not None:
        result["json_pointer"] = path
    return result


def offset_hours(local, utc):
    """WordPress date fields are naive local/GMT strings, not login telemetry."""
    try:
        a, b = datetime.fromisoformat(local), datetime.fromisoformat(utc)
        if a.tzinfo or b.tzinfo:
            return None
        value = (a - b).total_seconds() / 3600
        return round(value, 3) if -14 <= value <= 14 else None
    except (TypeError, ValueError):
        return None


def script_families(value):
    """Unicode script presence, deliberately not natural-language detection."""
    found = []
    if re.search(r"[\u0400-\u052f]", value):
        found.append("Cyrillic (several languages)")
    if re.search(r"[\u3400-\u4dbf\u4e00-\u9fff]", value):
        found.append("Han characters (several languages)")
    return found


def collect(target, client, expected_languages=None, expected_timezone=None, sample=10, quick=False):
    report = {"tool": "WP Telltale", "version": VERSION, "target": target, "observed_at": utc_now(),
              "demo": client.demo, "attribution_assessed": False, "limitation": LIMITATION,
              "baseline": {"expected_languages": expected_languages or [], "expected_timezone": expected_timezone},
              "wordpress": {"rest_api_observed": False}, "interface_locales": {
                  "availability": "unknown", "advertised": [], "additional_packs_indicated": [],
                  "admin_selected_language": "not observed", "dashboard_selected_language": "unknown",
                  "public_language_switcher": "not assessed", "evidence": []},
              "homepage": {}, "login": {}, "samples": {}, "review_cues": [],
              "coverage": [], "references": [], "script_observations": []}

    def coverage(check, status, detail, response=None):
        item = {"check": check, "status": status, "detail": detail}
        if response:
            item["source"] = source(response)
        report["coverage"].append(item)

    def inspect_html(response):
        if not response or response["status"] != 200 or response["truncated"] or response["error"]:
            return {}
        info = analyze_html(response_text(response), response["url"])
        info["source"] = source(response)
        info["content_language"] = response["headers"].get("content-language")
        for hit in info["tracking_evidence"]:
            hit["source"] = source(response)
        report["references"].extend(dict(x, source=source(response)) for x in info["references"])
        return info

    def add_locale_evidence(hits, response, kind):
        for hit in hits:
            report["interface_locales"]["evidence"].append({
                "kind": kind, "values": hit["values"], "source": source(response, hit.get("path"))})

    home = client.fetch(target, purpose="homepage")
    report["homepage"] = inspect_html(home)
    coverage("Homepage", "observed" if report["homepage"] else "unavailable", "Public HTML and declarations", home)
    canonical = home["url"] if home and home["status"] == 200 else target
    # Preserve installation directory, including a redirect to /news/.
    base = canonical if urlsplit(canonical).path.endswith("/") else canonical.rsplit("/", 1)[0] + "/"
    candidates = list(report["homepage"].get("rest_links", []))
    if home:
        candidates.extend(re.findall(r'<([^>]+)>\s*;\s*rel=[\"\']?https://api\.w\.org/', home["headers"].get("link", "")))
    candidates.extend([urljoin(base, "wp-json/"), base + ("&" if "?" in base else "?") + "rest_route=/"])
    rest_response, rest = None, None
    for address in list(dict.fromkeys(candidates))[:4]:
        try:
            address = urljoin(base, address)
        except ValueError:
            client.warnings.append("Invalid REST discovery URL ignored: " + str(address))
            continue
        candidate = client.fetch(address, purpose="REST index")
        payload = response_json(candidate)
        if isinstance(payload, dict) and isinstance(payload.get("routes"), dict) and any(
                str(route).startswith("/wp/v2/") for route in payload["routes"]):
            rest_response, rest = candidate, payload
            break
    api_root = rest_response["url"] if rest_response else None
    wp_base = base
    if rest is not None:
        wp = report["wordpress"]
        wp.update({"rest_api_observed": True, "api_root": api_root, "source": source(rest_response)})
        for field in ("name", "description", "url", "home", "gmt_offset", "timezone_string", "namespaces", "site_icon_url"):
            if field in rest:
                wp[field] = rest[field]
        # Non-core language fields are preserved explicitly as extensions.
        wp["extension_language_fields"] = {k: rest[k] for k in ("site_language", "language", "locale") if k in rest}
        for key in ("home", "url", "site_icon_url"):
            if isinstance(rest.get(key), str):
                report["references"].append({"url": rest[key], "kind": "REST " + key, "source": source(rest_response, "/" + key)})
        if isinstance(rest.get("url"), str) and client.allowed(rest["url"]):
            p = urlsplit(rest["url"])
            wp_base = urlunsplit((p.scheme, p.netloc, p.path.rstrip("/") + "/", "", ""))
        add_locale_evidence(extract_locale_enums(rest), rest_response, "REST users route enum")
        coverage("WordPress REST index", "observed", "Public route schema and site configuration", rest_response)
        if not any(LOCALE_RE.fullmatch(v) for e in report["interface_locales"]["evidence"] for v in e["values"]):
            opt = client.fetch(api_url(api_root, "/wp/v2/users"), method="OPTIONS", purpose="users public schema")
            hits = extract_locale_enums(response_json(opt), options=True)
            if hits:
                add_locale_evidence(hits, opt, "REST users OPTIONS schema")
            coverage("Users OPTIONS schema", "observed" if hits else "unavailable", "Fallback for missing locale enum", opt)
    else:
        coverage("WordPress REST index", "unavailable", "No usable wp/v2 route index; WordPress is unconfirmed by REST.")

    login = client.fetch(urljoin(wp_base, "wp-login.php"), purpose="anonymous login language selector")
    report["login"] = inspect_html(login)
    login_languages = report["login"].get("login_languages", [])
    if login_languages:
        add_locale_evidence([{"values": login_languages}], login, "Anonymous login language selector")
    coverage("Login language selector", "observed" if login_languages else "unavailable",
             "Available choices; absence is not proof that packs are absent", login)
    locales = report["interface_locales"]
    advertised = {x for entry in locales["evidence"] for x in entry["values"] if LOCALE_RE.fullmatch(x)}
    if advertised:
        locales["availability"] = "advertised by public metadata"
        locales["advertised"] = sorted(advertised)
        locales["additional_packs_indicated"] = sorted(advertised - {"en_US"})
        schema_sets = {tuple(sorted(set(x["values"]))) for x in locales["evidence"] if x["kind"].startswith("REST")}
        if len(schema_sets) > 1:
            report["review_cues"].append({"signal": "Inconsistent locale schemas", "detail": "Core users route copies disagree; inspect raw evidence before interpreting."})

    specifications = [
        ("public_authors", "/wp/v2/users", {"per_page": sample, "context": "view", "_fields": "id,name,slug,url,link,description,avatar_urls"}),
        ("recent_posts", "/wp/v2/posts", {"per_page": sample, "orderby": "date", "order": "desc", "_fields": "id,date,date_gmt,modified,modified_gmt,slug,link,title,author"}),
        ("oldest_posts", "/wp/v2/posts", {"per_page": min(sample, 5), "orderby": "date", "order": "asc", "_fields": "id,date,date_gmt,modified,modified_gmt,slug,link,title,author"}),
        ("media", "/wp/v2/media", {"per_page": sample, "_fields": "id,date,date_gmt,slug,source_url,link,title,author,media_details"}),
    ]
    for name, route, params in specifications:
        if quick or not api_root:
            coverage(name, "skipped", "Quick mode" if quick else "No REST root")
            continue
        response = client.fetch(api_url(api_root, route, params), purpose=name)
        values = response_json(response)
        if not isinstance(values, list):
            coverage(name, "unavailable", "Endpoint blocked, absent, non-JSON, or budget exhausted", response)
            continue
        items = [x for x in values[:int(params["per_page"])] if isinstance(x, dict)]
        for item in items:
            if "date" in item:
                item["derived_publication_offset_hours"] = offset_hours(item.get("date"), item.get("date_gmt"))
            if name == "media" and isinstance(item.get("source_url"), str):
                try:
                    item["decoded_filename"] = unquote(urlsplit(item["source_url"]).path.rsplit("/", 1)[-1])
                except ValueError:
                    client.warnings.append("Invalid media source_url retained as raw evidence; filename not decoded.")
            for key in ("source_url", "url", "link"):
                if isinstance(item.get(key), str) and item[key]:
                    report["references"].append({"url": item[key], "kind": name + " " + key, "source": source(response)})
            display_values = {"slug": item.get("slug"), "name": item.get("name"), "filename": item.get("decoded_filename")}
            title = item.get("title")
            if isinstance(title, dict):
                display_values["title"] = html.unescape(re.sub(r"<[^>]+>", "", str(title.get("rendered", ""))))
            for field, text in display_values.items():
                if isinstance(text, str) and script_families(unquote(text)):
                    report["script_observations"].append({"sample": name, "id": item.get("id"), "field": field,
                        "value": unquote(text), "scripts": script_families(unquote(text)), "source": source(response)})
        report["samples"][name] = {"items": items, "source": source(response), "reported_total": response["headers"].get("x-wp-total")}
        coverage(name, "observed", f"{len(items)} public records; one page only", response)

    expected = {language_base(x) for x in expected_languages or []}
    if expected:
        unexpected = [x for x in locales["additional_packs_indicated"] if language_base(x) not in expected]
        if unexpected:
            report["review_cues"].append({"signal": "Advertised translation differs from supplied baseline", "detail": ", ".join(unexpected),
                "interpretation": "Translation availability does not establish active dashboard use or a public language switcher. Review developer, template, and multilingual explanations."})
    front_language = report["homepage"].get("html_language")
    if front_language:
        differing = [x for x in locales["additional_packs_indicated"] if language_base(x) != language_base(front_language)]
        if differing:
            report["review_cues"].append({"signal": "Advertised translation differs from homepage declaration", "detail": f"Homepage declares {front_language}; additional translation available: {', '.join(differing)}",
                "interpretation": "Selected dashboard language and a visitor-facing switcher are not established. Homepage HTML is a declaration, not detected prose language."})
    actual_zone = report["wordpress"].get("timezone_string")
    if expected_timezone and isinstance(actual_zone, str) and actual_zone and actual_zone != expected_timezone:
        report["review_cues"].append({"signal": "Site timezone differs from supplied baseline", "detail": f"{actual_zone} versus {expected_timezone}",
            "interpretation": "Literal configured zone names differ. Aliases may represent the same zone; this is not operator/server geolocation."})
    external = {}
    for ref in report["references"]:
        try:
            host = urlsplit(ref["url"]).hostname
        except ValueError:
            continue
        if host and host.lower().removeprefix("www.") != urlsplit(target).hostname.lower().removeprefix("www."):
            external.setdefault(host, set()).add(ref["kind"])
    report["external_reference_hosts"] = [{"host": host, "contexts": sorted(kinds)} for host, kinds in sorted(external.items())]
    offsets = Counter()
    seen_posts = set()
    for category in ("recent_posts", "oldest_posts"):
        for item in report["samples"].get(category, {}).get("items", []):
            post_id = item.get("id")
            if not isinstance(post_id, (str, int)) or isinstance(post_id, bool):
                continue
            if post_id in seen_posts:
                continue
            seen_posts.add(post_id)
            value = item.get("derived_publication_offset_hours")
            if value is not None:
                offsets[str(value)] += 1
    report["publication_offset_counts"] = dict(offsets)
    report["requests"] = client.records
    report["warnings"] = list(dict.fromkeys(client.warnings))
    report["completed_at"] = utc_now()
    return report


def render_html(report):
    """Self-contained report with escaped evidence; never executes target markup."""
    esc = lambda value: html.escape(str(value), quote=True)
    pretty = lambda value: esc(json.dumps(value, indent=2, ensure_ascii=False))
    rows = lambda pairs: "".join("<tr><th>" + esc(k) + "</th><td>" + esc(v) + "</td></tr>" for k, v in pairs)
    locales, wp = report["interface_locales"], report["wordpress"]
    basics = [("Target", report["target"]), ("Collected (UTC)", report["observed_at"]),
              ("Language evidence", locales["availability"]),
              ("WordPress translations advertised", ", ".join(locale_label(x) for x in locales["advertised"]) or "Unknown"),
              ("Additional translations indicated", ", ".join(locale_label(x) for x in locales["additional_packs_indicated"]) or ("None advertised" if locales["advertised"] else "Unknown")),
              ("Selected dashboard language", "Unknown (not observed)"),
              ("Public language switcher", "Not assessed"),
              ("Homepage language declaration", report["homepage"].get("html_language") or "Unknown"),
              ("Configured site timezone", wp.get("timezone_string") or "Unset / unavailable"),
              ("Configured UTC offset", wp.get("gmt_offset", "Unknown")), ("Attribution", "Not assessed")]
    cues = "".join("<li><strong>" + esc(x["signal"]) + "</strong>: " + esc(x["detail"]) + "<p>" + esc(x.get("interpretation", "")) + "</p></li>" for x in report["review_cues"])
    coverage = "".join("<tr><td>" + esc(x["check"]) + "</td><td>" + esc(x["status"]) + "</td><td>" + esc(x["detail"]) + "</td></tr>" for x in report["coverage"])
    sections = [("Language evidence and exact JSON pointers", locales["evidence"]),
                ("Tracking identifiers (observed strings, execution unverified)", report["homepage"].get("tracking_evidence", [])),
                ("Excluded platform identifiers", report["homepage"].get("excluded_tracking_ids", [])),
                ("WordPress configuration", wp), ("External reference hosts (not fetched)", report["external_reference_hosts"]),
                ("Unicode script observations (not language identification)", report["script_observations"]),
                ("Publication offset counts (not login times)", report["publication_offset_counts"]),
                ("Homepage and asset metadata", report["homepage"]), ("Public samples", report["samples"]),
                ("References", report["references"]), ("Request log and response hashes", report["requests"]),
                ("Collection warnings", report["warnings"])]
    details = "".join("<details><summary>" + esc(title) + "</summary><pre>" + pretty(data) + "</pre></details>" for title, data in sections)
    demo = '<div class="demo">FICTIONAL OFFLINE DEMO · No live website was contacted.</div>' if report["demo"] else ""
    return """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>WP Telltale report</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f2f5f2;color:#18352e;font:16px/1.6 system-ui,sans-serif}
main{max-width:1120px;margin:auto;padding:40px 22px}header{background:#083f34;color:#fff;padding:30px;border-radius:14px;margin-bottom:24px}
h1{margin:0;font-size:38px}header p{margin:5px 0 0;color:#c7ddd4}.demo{background:#f6dea2;padding:12px 18px;font-weight:700;border-radius:8px;margin-bottom:18px}
section,details{background:white;border:1px solid #d7e2dc;border-radius:10px;padding:20px;margin:16px 0}h2{font-size:22px;margin:0 0 12px}
table{width:100%;border-collapse:collapse}th,td{padding:10px;text-align:left;vertical-align:top;border-bottom:1px solid #e2e9e5;overflow-wrap:anywhere}
th{width:32%;font-weight:600}summary{font-weight:650;cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:13px/1.6 ui-monospace,monospace}
li{margin-bottom:12px}li p{margin:3px 0}footer{color:#547366;font-size:13px}code{font-family:ui-monospace,monospace}
@media(max-width:600px){main{padding:16px 10px}h1{font-size:28px}section,details{padding:12px}th,td{padding:7px}}
</style></head><body><main><header><h1>WP Telltale</h1><p>Public WordPress configuration and infrastructure clues</p></header>""" + demo + \
        "<section><h2>Observed configuration</h2><table>" + rows(basics) + "</table></section>" + \
        "<section><h2>Analyst review cues</h2>" + ("<ul>" + cues + "</ul>" if cues else "<p>No comparison cues were generated from available data. This is not a clean bill of health.</p>") + "</section>" + \
        "<section><h2>Interpretation</h2><p>" + esc(report["limitation"]) + "</p><p>en_US is built-in English; an empty enum value means the default choice. Translation availability can support account interface preferences without a public language switcher. REST enum copies and the login selector often use the same underlying function; they are not independent attribution evidence.</p></section>" + \
        "<section><h2>Collection coverage</h2><table><tr><th>Check</th><th>Status</th><th>Detail</th></tr>" + coverage + "</table></section>" + details + \
        "<footer>WP Telltale " + esc(VERSION) + " · Public responses are untrusted claims. SHA-256 hashes support evidence handling, not authenticity.</footer></main></body></html>"


def demo_response(address, method):
    """Invented classroom fixtures, not snapshots or claims about any real entity."""
    p = urlsplit(address)
    q = dict(parse_qsl(p.query))
    route = q.get("rest_route", p.path.removeprefix("/wp-json"))
    headers = {"content-type": "application/json; charset=utf-8"}
    site = "https://" + p.netloc
    secondary = p.hostname == "second-newsroom.example"
    if p.path == "/" and "rest_route" not in q:
        body = '''<!doctype html><html lang="en-US"><head><meta property="og:locale" content="en_US">
<meta name="generator" content="WordPress (fictional demo)"><link rel="https://api.w.org/" href="https://newsroom.example/wp-json/">
<link rel="canonical" href="https://old-brand.example/"><link rel="stylesheet" href="/wp-content/themes/example-news/style.css">
<script src="https://www.googletagmanager.com/gtag/js?id=G-DEMO123456"></script></head>
<body><h1>Fictional English-language newsroom</h1><script>ym(12345678, 'init', {});</script>
<ins data-ad-client="ca-pub-0000000000000000"></ins>
<meta name="google-adsense-platform-account" content="ca-host-pub-1111111111111111">
<style>.banner{background-image:url("https://old-brand.example/banner.jpg")}</style>
<img src="https://assets.example/press.jpg"></body></html>'''
        return 200, {"content-type": "text/html; charset=utf-8"}, body.replace("https://newsroom.example", site).encode()
    if p.path == "/wp-login.php":
        body = '<html lang="en-US"><body><select id="language-switcher-locales" name="wp_lang"><option value="en_US">English</option><option value="zh_CN">简体中文</option></select></body></html>'
        if secondary:
            body = body.replace("zh_CN", "ru_RU").replace("简体中文", "Русский")
        return 200, {"content-type": "text/html; charset=utf-8"}, body.encode()
    if route in {"", "/"}:
        data = {"name": "Fictional newsroom", "description": "Offline classroom fixture", "url": "https://newsroom.example", "home": "https://newsroom.example",
                "gmt_offset": 8, "timezone_string": "Asia/Shanghai", "namespaces": ["wp/v2", "example-demo/v1"],
                "routes": {"/wp/v2/users": {"endpoints": [{"methods": ["GET"], "args": {}}, {"methods": ["POST"], "args": {"locale": {"enum": ["", "en_US", "zh_CN"]}}}]},
                           "/wp/v2/posts": {"endpoints": []}, "/wp/v2/media": {"endpoints": []}}}
    elif route == "/wp/v2/users":
        data = [{"id": 1, "name": "Demo Editor", "slug": "demo-editor", "url": "https://agency.example/", "link": "https://newsroom.example/author/demo-editor/"}]
    elif route == "/wp/v2/posts":
        data = [{"id": 1 if q.get("order") == "asc" else 9, "date": "2025-11-20T16:00:00", "date_gmt": "2025-11-20T08:00:00", "slug": "hello-world",
                 "link": "https://newsroom.example/hello-world/", "title": {"rendered": "世界，您好！" if q.get("order") == "asc" else "Fictional local headline"}, "author": 1}]
    elif route == "/wp/v2/media":
        data = [{"id": 2, "slug": "example-image", "source_url": "https://newsroom.example/wp-content/uploads/2025/11/%E6%B5%8B%E8%AF%95.jpg",
                 "title": {"rendered": "Example image"}, "media_details": {"image_meta": {"credit": "Demo supplier", "camera": ""}}}]
    else:
        return 404, headers, b'{"code":"rest_no_route"}'
    encoded = json.dumps(data, ensure_ascii=False).replace("https://newsroom.example", site)
    if secondary:
        encoded = encoded.replace('"zh_CN"', '"ru_RU"').replace('"Asia/Shanghai"', '"Asia/Singapore"')
    return 200, headers, encoded.encode()


def console_summary(report):
    locales, wp = report["interface_locales"], report["wordpress"]
    lines = [f"WP Telltale {VERSION}", "FICTIONAL OFFLINE DEMO" if report["demo"] else report["target"],
             "WordPress translations advertised: " + (", ".join(locale_label(x) for x in locales["advertised"]) or "unknown"),
             "Additional translations indicated: " + (", ".join(locale_label(x) for x in locales["additional_packs_indicated"]) or ("none advertised" if locales["advertised"] else "unknown")),
             "Selected dashboard language: unknown (not observed)",
             "Public language switcher: not assessed",
             "Homepage declared language: " + str(report["homepage"].get("html_language") or "unknown"),
             f"Configured site timezone: {wp.get('timezone_string') or 'unset/unavailable'}; UTC offset: {wp.get('gmt_offset', 'unknown')}"]
    for finding in report["review_cues"]:
        lines.append("REVIEW: " + finding["signal"] + ": " + finding["detail"])
        if finding.get("interpretation"):
            lines.append("        " + finding["interpretation"])
    for kind, values in report["homepage"].get("tracking_ids", {}).items():
        lines.append("PIVOT " + kind + ": " + ", ".join(values))
    lines.append(f"Requests recorded: {len(report['requests'])}; attribution: not assessed")
    lines.extend("NOTE: " + x for x in report["warnings"])
    # Remote text must not emit terminal control sequences or bidi overrides.
    return terminal_text("\n".join(lines))


def load_targets(path):
    """Read one URL/domain per line; retain line-number diagnostics, never bad input."""
    targets, rejected, duplicates, first_seen = [], [], [], {}
    with Path(path).open("r", encoding="utf-8-sig") as handle:
        for number, raw in enumerate(handle, 1):
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            try:
                target = normalize_target(value)
            except (ValueError, UnicodeError):
                # Do not retain the raw line or exception: either could contain credentials.
                rejected.append({"line": number, "error": "Invalid target. Use a domain or HTTP(S) base URL without credentials, whitespace, query, or fragment."})
                continue
            if target in first_seen:
                duplicates.append({"line": number, "target": target, "first_line": first_seen[target]})
                continue
            first_seen[target] = number
            targets.append(target)
    return {"targets": targets, "rejected_inputs": rejected, "duplicate_inputs": duplicates,
            "input_file": str(path)}


def target_host(target):
    """Group www aliases, schemes, ports and installation paths as one target host."""
    try:
        return (urlsplit(target).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return ""


def find_shared_identifiers(results):
    """Exact observed ID matches across hosts; no ownership or execution inference."""
    groups = {}
    for item in results:
        report = item.get("report")
        if not isinstance(report, dict):
            continue
        target = report.get("target") or item.get("target", "")
        host = target_host(target)
        if not host:
            continue
        home = report.get("homepage", {})
        for kind, values in home.get("tracking_ids", {}).items():
            if not isinstance(values, list) or kind == "google_adsense_platform":
                continue
            for value in values:
                if not isinstance(value, str) or value.startswith("ca-host-pub-"):
                    continue
                group = groups.setdefault((kind, value), {"hosts": set(), "targets": set(), "evidence": []})
                group["hosts"].add(host)
                group["targets"].add(target)
                matching = [entry for entry in home.get("tracking_evidence", [])
                            if entry.get("kind") == kind and entry.get("value") == value]
                if not matching and home.get("source"):
                    matching = [{"source": home["source"]}]
                for entry in matching[:3]:
                    group["evidence"].append({"target": target, **entry})
    output = []
    for (kind, value), group in sorted(groups.items()):
        if len(group["hosts"]) < 2:
            continue
        output.append({"kind": kind, "value": value, "hosts": sorted(group["hosts"]),
                       "targets": sorted(group["targets"]), "evidence": group["evidence"],
                       "interpretation": "The same identifier string appears on distinct target hosts. Execution, current ownership and common control are unverified; copying or shared service providers can explain a match."})
    return output


def report_observed(report):
    return bool(report.get("wordpress", {}).get("rest_api_observed")
                or report.get("interface_locales", {}).get("advertised"))


def refresh_batch(batch):
    results = batch["results"]
    batch["shared_identifiers"] = find_shared_identifiers(results)
    batch["counts"] = {
        "accepted_targets": len(batch["targets"]),
        "attempted_targets": len(results),
        "observed_targets": sum(item["status"] == "observed" for item in results),
        "unknown_targets": sum(item["status"] == "unknown" for item in results),
        "error_targets": sum(item["status"] == "error" for item in results),
        "interrupted_targets": sum(item["status"] == "interrupted" for item in results),
        "pending_targets": len(batch["pending_targets"]),
        "rejected_inputs": len(batch["rejected_inputs"]),
        "duplicates_skipped": len(batch["duplicate_inputs"]),
        "wordpress_rest_observed": sum(bool((item.get("report") or {}).get("wordpress", {}).get("rest_api_observed")) for item in results),
        "targets_with_review_cues": sum(bool((item.get("report") or {}).get("review_cues")) for item in results),
        "shared_identifiers": len(batch["shared_identifiers"]),
        "requests": sum(len((item.get("report") or {}).get("requests", item.get("partial_requests", []))) for item in results),
    }
    batch["completed_at"] = utc_now()
    return batch


def shared_counts(batch):
    counts = Counter()
    for match in batch.get("shared_identifiers", []):
        counts.update(set(match.get("targets", [])))
    return counts


def csv_cell(value):
    """Prevent collected text from becoming a spreadsheet formula."""
    text = "" if value is None else str(value)
    leading = re.sub(r"^[\s\ufeff\x00-\x1f\x7f]+", "", text)
    prefix = text[:len(text) - len(leading)]
    if leading.startswith(("=", "+", "-", "@")) or any(char in prefix for char in "\t\r\n"):
        return "'" + text
    return text


def render_batch_csv(batch):
    columns = ["target", "status", "wordpress_rest_observed", "translation_availability",
               "advertised_translations", "additional_translations", "selected_dashboard_language",
               "homepage_language", "timezone", "utc_offset", "review_cue_count", "review_cues",
               "tracking_identifiers", "shared_identifier_count", "request_count",
               "collection_warnings", "error"]
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=columns)
    writer.writeheader()
    matches = shared_counts(batch)
    items = list(batch.get("results", [])) + [
        {"target": target, "status": "pending", "report": None, "error": "Not attempted"}
        for target in batch.get("pending_targets", [])
    ]
    for item in items:
        report = item.get("report") or {}
        locales, wp, home = report.get("interface_locales", {}), report.get("wordpress", {}), report.get("homepage", {})
        cues = report.get("review_cues", [])
        row = {
            "target": item["target"], "status": item["status"],
            "wordpress_rest_observed": str(bool(wp.get("rest_api_observed"))).lower() if report else "unknown",
            "translation_availability": locales.get("availability", "unknown"),
            "advertised_translations": "; ".join(locales.get("advertised", [])) or "unknown",
            "additional_translations": "; ".join(locales.get("additional_packs_indicated", [])) or ("none advertised" if locales.get("advertised") else "unknown"),
            "selected_dashboard_language": "unknown", "homepage_language": home.get("html_language", ""),
            "timezone": wp.get("timezone_string", ""), "utc_offset": wp.get("gmt_offset", ""),
            "review_cue_count": len(cues),
            "review_cues": " | ".join(cue["signal"] + ": " + cue["detail"] for cue in cues),
            "tracking_identifiers": "; ".join(kind + "=" + value for kind, values in home.get("tracking_ids", {}).items() for value in values),
            "shared_identifier_count": matches[item["target"]],
            "request_count": len(report.get("requests", item.get("partial_requests", []))),
            "collection_warnings": " | ".join(report.get("warnings", [])),
            "error": item.get("error") or "",
        }
        writer.writerow({key: csv_cell(value) for key, value in row.items()})
    return output.getvalue()


def render_batch_html(batch):
    """One offline document; target strings only enter escaped text, never scripts."""
    esc = lambda value: html.escape(str(value), quote=True)
    pretty = lambda value: esc(json.dumps(value, indent=2, ensure_ascii=False))
    counts = batch.get("counts", {})
    matches = shared_counts(batch)
    overview, target_details = [], []
    items = list(batch.get("results", [])) + [
        {"target": target, "status": "pending", "report": None, "error": "Not attempted"}
        for target in batch.get("pending_targets", [])
    ]
    for number, item in enumerate(items, 1):
        report = item.get("report") or {}
        locales, wp, home = report.get("interface_locales", {}), report.get("wordpress", {}), report.get("homepage", {})
        cues = report.get("review_cues", [])
        translations = ", ".join(locale_label(value) for value in locales.get("advertised", [])) or "Unknown"
        ids = "; ".join(kind + ": " + ", ".join(values) for kind, values in home.get("tracking_ids", {}).items())
        cues_text = " | ".join(cue["signal"] + ": " + cue["detail"] for cue in cues)
        hidden_search = json.dumps({"external_hosts": report.get("external_reference_hosts", []),
                                    "warnings": report.get("warnings", [])}, ensure_ascii=False)
        flag_review = "yes" if cues else "no"
        flag_shared = "yes" if matches[item["target"]] else "no"
        flag_incomplete = "yes" if item["status"] != "observed" else "no"
        zone = str(wp.get("timezone_string") or "No named zone") + "; UTC offset: " + str(wp.get("gmt_offset", "unknown"))
        overview.append(
            '<tr data-target-row data-review="' + flag_review + '" data-shared="' + flag_shared +
            '" data-incomplete="' + flag_incomplete + '"><td><a href="#target-' + str(number) + '">' +
            esc(item["target"]) + '</a><br><span class="status">' + esc(item["status"]) +
            '</span><span hidden>' + esc(hidden_search) + '</span></td><td>' + esc(translations) +
            '<br><span class="muted">Homepage: ' + esc(home.get("html_language") or "unknown") +
            '</span></td><td>' + esc(zone) + '</td><td>' + esc(cues_text or "No comparison cues") +
            ('<br><span class="error">' + esc(item["error"]) + '</span>' if item.get("error") else "") +
            '</td><td>' + esc(ids or "None observed") + '<br><strong>' + str(matches[item["target"]]) +
            ' shared identifier(s)</strong></td></tr>')
        cue_html = "".join("<li><strong>" + esc(cue["signal"]) + "</strong>: " + esc(cue["detail"]) +
                           "<p>" + esc(cue.get("interpretation", "")) + "</p></li>" for cue in cues)
        target_details.append(
            '<details id="target-' + str(number) + '" class="target-detail"><summary>' + esc(item["target"]) +
            " · " + esc(item["status"]) + "</summary><p><strong>Translations advertised:</strong> " +
            esc(translations) + "</p><p><strong>Selected dashboard language:</strong> unknown. " +
            "<strong>Public language switcher:</strong> not assessed.</p>" +
            ("<ul>" + cue_html + "</ul>" if cues else "<p>No comparison cues were generated from available data.</p>") +
            ('<p class="error">' + esc(item["error"]) + "</p>" if item.get("error") else "") +
            "<details><summary>Full collected evidence, coverage and request hashes</summary><pre>" +
            pretty(report or item) + "</pre></details></details>")
    shared_rows = "".join(
        "<tr><td>" + esc(match["kind"]) + "</td><td><code>" + esc(match["value"]) + "</code></td><td>" +
        "<br>".join(esc(target) for target in match["targets"]) + "</td></tr>"
        for match in batch.get("shared_identifiers", []))
    shared_section = (
        '<div class="table-wrap"><table><thead><tr><th>Identifier kind</th><th>Exact value</th><th>Targets</th></tr></thead><tbody>' +
        shared_rows + "</tbody></table></div>"
        if shared_rows else "<p>No exact cross-host tracking-identifier matches were observed in the collected responses.</p>")
    diagnostics = {"rejected_inputs": batch.get("rejected_inputs", []),
                   "duplicate_inputs": batch.get("duplicate_inputs", []),
                   "pending_targets": batch.get("pending_targets", [])}
    script = """
const search = document.getElementById('search');
const filter = document.getElementById('filter');
function applyFilter() {
  const term = search.value.toLocaleLowerCase();
  let visible = 0;
  for (const row of document.querySelectorAll('[data-target-row]')) {
    const selected = filter.value;
    const flag = selected === 'all' || row.dataset[selected] === 'yes';
    row.hidden = !flag || !row.textContent.toLocaleLowerCase().includes(term);
    if (!row.hidden) visible++;
  }
  document.getElementById('visible-count').textContent = visible + ' target(s) shown';
}
search.addEventListener('input', applyFilter);
filter.addEventListener('change', applyFilter);
for (const link of document.querySelectorAll('a[href^="#target-"]')) {
  link.addEventListener('click', () => {
    const detail = document.getElementById(link.getAttribute('href').slice(1));
    if (detail) detail.open = true;
  });
}
applyFilter();
"""
    script_hash = base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    head = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'sha256-""" + script_hash + """'; base-uri 'none'; form-action 'none'">
<title>WP Telltale combined report</title><style>
:root{color-scheme:light}*{box-sizing:border-box}body{margin:0;background:#f1f5f2;color:#18352e;font:16px/1.6 system-ui,sans-serif}
main{max-width:1450px;margin:auto;padding:36px 24px}header{background:#083f34;color:white;padding:28px 32px;border-radius:12px}
h1{margin:0;font-size:36px}header p{margin:5px 0;color:#d9eee5}h2{font-size:24px;margin:0 0 14px}
section,details.target-detail{background:white;border:1px solid #d5e2da;border-radius:9px;padding:22px;margin:20px 0}
.stats{display:flex;gap:14px;flex-wrap:wrap;margin:22px 0}.stat{background:white;border:1px solid #d5e2da;border-radius:8px;padding:14px 20px;flex:1;min-width:150px}
.stat b{display:block;font-size:30px;color:#07513e}.stat span,.muted{color:#536f62}.demo{background:#f6dea2;padding:12px 18px;font-weight:700;border-radius:8px;margin:18px 0}
.error{color:#923316}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;min-width:820px;font-size:14px}
th,td{text-align:left;vertical-align:top;padding:12px;border-bottom:1px solid #dce7df;overflow-wrap:anywhere}th{background:#edf5ef}tbody tr:nth-child(even){background:#fafcfb}
a{color:#086349}code,pre{font-family:ui-monospace,monospace}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;background:#f4f7f5;padding:16px}
summary{cursor:pointer;font-weight:650}details details{margin-top:16px}.controls{display:flex;gap:15px;flex-wrap:wrap;align-items:end;margin:20px 0}
label{display:block;font-weight:650}input,select{display:block;max-width:100%;padding:10px;border:1px solid #8da99a;border-radius:5px;font:inherit}
input{width:450px}.status{display:inline-block;background:#edf2ee;border-radius:4px;padding:2px 7px;font-size:12px;font-weight:700}
li p{margin:2px 0}.note{border-left:4px solid #a77419;padding-left:16px}footer{color:#536f62;font-size:13px;margin:25px 0}
[hidden]{display:none!important}@media(max-width:600px){main{padding:15px 10px}header{padding:20px}h1{font-size:28px}section,details.target-detail{padding:14px}}
@media print{body{background:white}main{max-width:none;padding:0}.controls{display:none}table{min-width:0;font-size:10px}th,td{padding:6px}header{color:#18352e;background:white}}
</style></head><body><main><header><h1>WP Telltale</h1><p>Combined public WordPress metadata report</p></header>"""
    demo = '<div class="demo">FICTIONAL OFFLINE DEMO · No live website was contacted.</div>' if batch.get("demo") else ""
    interrupted = '<p class="note"><strong>Interrupted collection.</strong> Completed results and available partial request logs are retained; pending targets were not assessed.</p>' if batch.get("interrupted") else ""
    stats = '<div class="stats">' + "".join(
        '<div class="stat"><b>' + str(counts.get(key, 0)) + '</b><span>' + esc(label) + '</span></div>'
        for key, label in [("accepted_targets", "accepted targets"), ("wordpress_rest_observed", "WordPress REST observed"),
                           ("targets_with_review_cues", "targets with review cues"), ("shared_identifiers", "shared identifiers")]) + '</div>'
    return head + demo + '<p class="muted">Started (UTC): ' + esc(batch.get("observed_at", "")) + ' · Completed (UTC): ' + esc(batch.get("completed_at", "")) + '</p>' + interrupted + stats + \
        '<section><h2>Read the language evidence correctly</h2><p>' + esc(batch.get("limitation", LIMITATION)) + \
        '</p><p><code>en_US</code> is built-in English; <code>en-US</code> is the equivalent homepage language form. An empty raw enum value means the default choice. ' + \
        'An advertised additional locale describes translation availability, not a selected dashboard language. Accounts can use different dashboard languages without a visitor-facing language selector.</p>' + \
        '<p><strong>Collection status:</strong> observed means a WordPress REST index or language evidence was obtained. Unknown means those checks did not establish either. Error means collection failed locally or unexpectedly. These are coverage labels, not judgments about the target.</p></section>' + \
        '<section><h2>Target overview</h2><div class="controls"><div><label for="search">Search targets, languages, IDs or clues</label><input type="search" id="search" placeholder="Domain, ru_RU, analytics ID…"></div>' + \
        '<div><label for="filter">Show</label><select id="filter"><option value="all">All targets</option><option value="review">Review cues</option><option value="shared">Shared identifiers</option><option value="incomplete">Unknown, errors or pending</option></select></div><span id="visible-count" aria-live="polite"></span></div>' + \
        '<div class="table-wrap"><table><thead><tr><th>Target / status</th><th>Advertised translations</th><th>Configured timezone</th><th>Review cues / limitations</th><th>Tracking strings</th></tr></thead><tbody>' + \
        "".join(overview) + '</tbody></table></div></section><section><h2>Exact identifier matches across hosts</h2>' + shared_section + \
        '<p>Matches group distinct hostnames, treating www aliases, ports, schemes and installation paths on the same hostname as one host. A shared string can result from copying or shared service providers. Execution, current ownership and common control remain unverified. Platform-account identifiers are excluded.</p>' + \
        '<details><summary>Shared identifier source evidence</summary><pre>' + pretty(batch.get("shared_identifiers", [])) + '</pre></details></section>' + \
        '<section><h2>Input and completion diagnostics</h2><pre>' + pretty(diagnostics) + '</pre></section><section><h2>Per-target evidence</h2><p>Expand a target to review its findings, source URLs, response hashes and collection coverage.</p>' + \
        "".join(target_details) + '</section><footer>WP Telltale ' + esc(VERSION) + ' · All target content is escaped. This report makes no background network requests. Missing markers are not proof of absence.</footer>' + \
        '<script>' + script + '</script></main></body></html>'


def atomic_write_text(path, text):
    """Replace a completed report only after all its bytes were written locally."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", newline="", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def write_batch_reports(batch, prefix):
    refresh_batch(batch)
    outputs = {"json": Path(str(prefix) + ".json"), "html": Path(str(prefix) + ".html"),
               "csv": Path(str(prefix) + ".csv")}
    batch["output_files"] = {kind: str(path) for kind, path in outputs.items()}
    payloads = {"json": json.dumps(batch, indent=2, ensure_ascii=False) + "\n",
                "html": render_batch_html(batch), "csv": render_batch_csv(batch)}
    for kind, path in outputs.items():
        atomic_write_text(path, payloads[kind])
    return outputs


def evidence_subdirectory(root, index, target):
    host = re.sub(r"[^A-Za-z0-9._-]+", "-", urlsplit(target).hostname or "target")[:65]
    digest = hashlib.sha256(target.encode()).hexdigest()[:12]
    return Path(root) / (f"{index:04d}-" + host + "-" + digest)


def write_evidence_manifest(client):
    if client.evidence_dir:
        with (client.evidence_dir / "manifest.json").open("x", encoding="utf-8") as handle:
            json.dump({"target": client.target, "demo": client.demo, "requests": client.records},
                      handle, indent=2, ensure_ascii=False)


def run_batch(args, expected):
    if args.demo_batch:
        inputs = {"targets": ["https://newsroom.example/", "https://second-newsroom.example/"],
                  "rejected_inputs": [], "duplicate_inputs": [], "input_file": None}
    else:
        inputs = load_targets(args.targets)
    if not inputs["targets"]:
        for rejection in inputs["rejected_inputs"]:
            print("INPUT line " + str(rejection["line"]) + ": " + rejection["error"], file=sys.stderr)
        raise ValueError("The target file contains no valid targets. No websites were contacted.")
    prefix = Path(args.out) if args.out else Path("reports") / (
        "wp-telltale-batch-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ"))
    prefix.parent.mkdir(parents=True, exist_ok=True)
    for suffix in (".json", ".html", ".csv"):
        if Path(str(prefix) + suffix).is_dir():
            raise OSError("A report output path is a directory: " + str(prefix) + suffix)
    if args.evidence_dir:
        root = Path(args.evidence_dir)
        if root.exists() and (not root.is_dir() or any(root.iterdir())):
            raise ValueError("Batch evidence directory must be new or empty; prior evidence is never overwritten.")
        root.mkdir(parents=True, exist_ok=True)
    batch = {"tool": "WP Telltale", "version": VERSION, "mode": "batch", "schema_version": "1.0",
             "observed_at": utc_now(), "completed_at": None, "demo": args.demo_batch,
             "attribution_assessed": False, "limitation": LIMITATION, **inputs,
             "results": [], "pending_targets": list(inputs["targets"]), "shared_identifiers": [],
             "interrupted": False, "local_errors": [], "baseline": {
                 "expected_languages": expected, "expected_timezone": args.expected_timezone},
             "parameters": {"sample": args.sample, "quick": args.quick, "timeout": args.timeout,
                            "delay": args.delay, "max_requests_per_target": args.max_requests,
                            "concurrency": 1}}
    for rejection in batch["rejected_inputs"]:
        print("INPUT line " + str(rejection["line"]) + ": " + rejection["error"], file=sys.stderr)
    client, item, target = None, None, None
    try:
        for number, target in enumerate(batch["targets"], 1):
            print(terminal_text(f"[{number}/{len(batch['targets'])}] {target}"), flush=True)
            client = None
            item = {"target": target, "status": "error", "report": None, "error": None}
            try:
                evidence = evidence_subdirectory(args.evidence_dir, number, target) if args.evidence_dir else None
                client = Client(target, args.timeout, args.delay, args.max_requests, evidence, args.demo_batch)
                report = collect(target, client, expected, args.expected_timezone, args.sample, args.quick)
                item.update(status="observed" if report_observed(report) else "unknown", report=report)
            except KeyboardInterrupt:
                batch["interrupted"] = True
                item.update(status="interrupted", error="Interrupted during this target; completed request records are retained.")
            except OSError as exc:
                item["error"] = type(exc).__name__ + ": " + str(exc)
                batch["local_errors"].append({"target": target, "error": item["error"]})
            except Exception as exc:
                # A malformed site or unexpected parser failure must not discard other targets.
                item["error"] = type(exc).__name__ + ": " + str(exc)
            finally:
                if client:
                    if item["report"] is None:
                        item["partial_requests"] = client.records
                    try:
                        write_evidence_manifest(client)
                    except OSError as exc:
                        message = "Evidence manifest write failed: " + str(exc)
                        batch["local_errors"].append({"target": target, "error": message})
                        item["error"] = message if not item["error"] else item["error"] + "; " + message
                        if item["status"] != "interrupted":
                            item["status"] = "error"
            batch["results"].append(item)
            batch["pending_targets"] = batch["targets"][number:]
            detail = item["status"]
            if item["report"]:
                detail += "; translations: " + (", ".join(item["report"]["interface_locales"]["advertised"]) or "unknown")
                detail += "; review cues: " + str(len(item["report"]["review_cues"]))
            if item["error"]:
                detail += "; " + item["error"]
            print(terminal_text("  " + detail), flush=True)
            if batch["interrupted"]:
                break
    except KeyboardInterrupt:
        # Also preserve work if interrupted between targets or during progress output.
        batch["interrupted"] = True
        completed = {result["target"] for result in batch["results"]}
        if client is not None and client.target == target and target not in completed:
            partial = item if item and item.get("target") == target else {"target": target, "report": None}
            partial.update(status="interrupted", error="Interrupted; completed request records are retained.")
            partial["partial_requests"] = client.records
            batch["results"].append(partial)
            completed.add(target)
        batch["pending_targets"] = [value for value in batch["targets"] if value not in completed]
    try:
        outputs = write_batch_reports(batch, prefix)
    except KeyboardInterrupt:
        batch["interrupted"] = True
        outputs = write_batch_reports(batch, prefix)
    counts = batch["counts"]
    print(terminal_text(
        f"WP Telltale {VERSION} batch: {counts['observed_targets']} observed, "
        f"{counts['unknown_targets']} unknown, {counts['error_targets']} errors, "
        f"{counts['pending_targets']} pending; {counts['shared_identifiers']} shared identifier(s)."))
    print(terminal_text("Reports: " + ", ".join(str(path) for path in outputs.values())))
    if batch["interrupted"]:
        return 130
    if batch["local_errors"]:
        return 2
    return 1 if batch["rejected_inputs"] or any(item["status"] != "observed" for item in batch["results"]) else 0


def main(argv=None):
    # Windows terminals may use a legacy code page; reports themselves remain UTF-8.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", nargs="?", help="Domain or site/installation URL")
    parser.add_argument("--targets", "--target-file", metavar="FILE", help="Read domains/base URLs from a UTF-8 text file; one target per line")
    parser.add_argument("--demo", action="store_true", help="Run fictional built-in fixtures without network access")
    parser.add_argument("--demo-batch", action="store_true", help="Run two fictional offline targets and generate a combined report")
    parser.add_argument("--expected-languages", default="", help="Comma-separated audience language baseline, e.g. en,es")
    parser.add_argument("--expected-timezone", help="Expected configured zone, e.g. America/New_York")
    parser.add_argument("--out", metavar="PREFIX", help="Report prefix: JSON and HTML; batch also writes CSV (batch default: timestamped reports/ prefix)")
    parser.add_argument("--evidence-dir", help="Save bounded raw response bodies; batch uses one subdirectory per target; use a fresh directory")
    parser.add_argument("--quick", action="store_true", help="Skip public author/post/media samples")
    parser.add_argument("--sample", type=int, default=10, help="Records per public collection, 1–100 (default: 10); oldest posts capped at 5")
    parser.add_argument("--timeout", type=float, default=12, help="Per-socket timeout seconds (default: 12)")
    parser.add_argument("--delay", type=float, default=0.35, help="Minimum seconds between requests (default: 0.35)")
    parser.add_argument("--max-requests", type=int, default=20, help="Hard request budget per target, including redirects (default: 20)")
    parser.add_argument("--version", action="version", version=VERSION)
    args = parser.parse_args(argv)
    if sum(bool(value) for value in (args.target, args.targets, args.demo, args.demo_batch)) != 1:
        parser.error("Choose exactly one input: a target URL, --targets FILE, --demo, or --demo-batch.")
    if not 1 <= args.sample <= 100 or not 1 <= args.max_requests <= 100:
        parser.error("--sample and --max-requests must be between 1 and 100.")
    if not 0 < args.timeout <= 60 or not 0 <= args.delay <= 10:
        parser.error("--timeout must be >0 and <=60; --delay must be >=0 and <=10.")
    expected = [x.strip() for x in args.expected_languages.split(",") if x.strip()]
    if any(not LOCALE_RE.fullmatch(x) for x in expected):
        parser.error("Expected languages must be language/locale codes such as en, en_US, or zh-Hant.")
    try:
        if args.targets or args.demo_batch:
            return run_batch(args, expected)
        target = normalize_target("https://newsroom.example" if args.demo else args.target)
        client = Client(target, args.timeout, args.delay, args.max_requests, args.evidence_dir, args.demo)
        report = collect(target, client, expected, args.expected_timezone, args.sample, args.quick)
        print(console_summary(report))
        if args.out:
            prefix = Path(args.out)
            prefix.parent.mkdir(parents=True, exist_ok=True)
            json_path, html_path = Path(str(prefix) + ".json"), Path(str(prefix) + ".html")
            atomic_write_text(json_path, json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            atomic_write_text(html_path, render_html(report))
            print(terminal_text("Reports: " + str(json_path) + " and " + str(html_path)))
        write_evidence_manifest(client)
        # A readable response is not proof of WordPress. Unknown collection is nonzero.
        return 0 if report["wordpress"]["rest_api_observed"] or report["interface_locales"]["advertised"] else 1
    except (ValueError, OSError) as exc:
        print(terminal_text("Error: " + str(exc)), file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
