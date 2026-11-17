"""Offline regression tests for public WordPress metadata interpretation.

These examples are synthetic. No test contacts a real website.
Run from the project directory: python3 -m unittest discover -s tests -v
"""

import copy
import hashlib
import io
import json
import sys
import tempfile
import threading
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import wp_telltale as wt


def user_route(*values):
    return {
        "namespace": "wp/v2",
        "endpoints": [
            {"methods": ["GET"], "args": {"context": {"enum": ["view", "embed", "edit"]}}},
            {"methods": ["POST"], "args": {"locale": {"type": "string", "enum": list(values)}}},
        ],
    }


class TargetAndAPIURLTests(unittest.TestCase):
    def test_bare_domain_defaults_to_https(self):
        parts = urlsplit(wt.normalize_target("example.com"))
        self.assertEqual((parts.scheme, parts.netloc), ("https", "example.com"))

    def test_wordpress_subdirectory_is_preserved(self):
        parts = urlsplit(wt.normalize_target("https://example.com/news/"))
        self.assertEqual(parts.path.rstrip("/"), "/news")

    def test_explicit_http_for_local_lab_is_preserved(self):
        parts = urlsplit(wt.normalize_target("http://127.0.0.1:8123/blog/"))
        self.assertEqual((parts.scheme, parts.netloc), ("http", "127.0.0.1:8123"))

    def test_embedded_credentials_are_rejected(self):
        for target in ("https://user:password@example.com", "https://user@example.com"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                wt.normalize_target(target)

    def test_non_http_schemes_are_rejected(self):
        for target in ("file:///etc/passwd", "ftp://example.com", "javascript:alert(1)"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                wt.normalize_target(target)

    def test_query_and_fragment_are_not_silently_scanned(self):
        for target in ("https://example.com/?x=1", "https://example.com/#fragment"):
            with self.subTest(target=target), self.assertRaises(ValueError):
                wt.normalize_target(target)

    def test_pretty_rest_root_retains_installation_path(self):
        parts = urlsplit(wt.api_url("https://example.com/news/wp-json/", "/wp/v2/posts", {"per_page": 7}))
        self.assertEqual(parts.path, "/news/wp-json/wp/v2/posts")
        self.assertEqual(parse_qs(parts.query)["per_page"], ["7"])

    def test_query_rest_root_keeps_route_in_query(self):
        parts = urlsplit(wt.api_url("https://example.com/news/?rest_route=/", "/wp/v2/users", {"per_page": 3}))
        self.assertEqual(parts.path, "/news/")
        self.assertEqual(parse_qs(parts.query), {"rest_route": ["/wp/v2/users"], "per_page": ["3"]})

    def test_encoded_query_rest_root_and_fields(self):
        parts = urlsplit(wt.api_url("https://example.com/index.php?rest_route=%2F", "/wp/v2/media", {"_fields": "id,source_url"}))
        self.assertEqual(parts.path, "/index.php")
        self.assertEqual(parse_qs(parts.query)["rest_route"], ["/wp/v2/media"])
        self.assertEqual(parse_qs(parts.query)["_fields"], ["id,source_url"])


class LocaleSchemaTests(unittest.TestCase):
    def test_locale_enum_is_found_in_core_users_route(self):
        payload = {"routes": {"/wp/v2/users": user_route("", "en_US", "zh_CN", "ru_RU")}}
        hits = wt.extract_locale_enums(payload)
        self.assertTrue(hits)
        self.assertEqual(hits[0]["values"], ["", "en_US", "zh_CN", "ru_RU"])
        self.assertIn("locale", hits[0]["path"])
        self.assertIn("enum", hits[0]["path"])

    def test_builtin_and_default_values_survive_as_raw_evidence(self):
        payload = {"routes": {"/wp/v2/users": user_route("", "en_US")}}
        self.assertEqual(wt.extract_locale_enums(payload)[0]["values"], ["", "en_US"])

    def test_plugin_language_catalog_is_not_installed_pack_evidence(self):
        payload = {
            "locale": {"enum": ["zh_CN"]},
            "routes": {
                "/translate/v1/languages": user_route("zh_CN", "ru_RU"),
                "/wp/v2/posts": user_route("zh_CN"),
                "/wp/v2/users-extra": user_route("ru_RU"),
            },
        }
        self.assertEqual(wt.extract_locale_enums(payload), [])

    def test_core_user_detail_and_me_routes_are_recognized(self):
        for route in ("/wp/v2/users/me", r"/wp/v2/users/(?P<id>[\d]+)"):
            with self.subTest(route=route):
                hits = wt.extract_locale_enums({"routes": {route: user_route("", "en_US", "zh_HK")}})
                self.assertTrue(hits)
                self.assertIn("zh_HK", hits[0]["values"])

    def test_actual_user_records_are_not_schema_enumerations(self):
        self.assertEqual(wt.extract_locale_enums([{"id": 1, "locale": "ru_RU"}]), [])

    def test_options_schema_requires_explicit_options_mode(self):
        payload = {"schema": {"properties": {"locale": {"type": "string", "enum": ["", "en_US", "zh_CN"]}}}}
        self.assertEqual(wt.extract_locale_enums(payload), [])
        hits = wt.extract_locale_enums(payload, options=True)
        self.assertTrue(hits)
        self.assertIn("zh_CN", hits[0]["values"])
        self.assertIn("schema", hits[0]["path"])

    def test_malformed_untrusted_json_is_tolerated(self):
        malformed = [None, "not an object", [], {"routes": None}, {"routes": []}, {"routes": {"/wp/v2/users": None}}]
        for payload in malformed:
            with self.subTest(payload=payload):
                self.assertEqual(wt.extract_locale_enums(payload), [])

    def test_extraction_does_not_modify_evidence(self):
        payload = {"routes": {"/wp/v2/users": user_route("", "en_US", "zh_CN")}}
        original = copy.deepcopy(payload)
        wt.extract_locale_enums(payload)
        self.assertEqual(payload, original)

    def test_language_matching_uses_language_component(self):
        for locale, expected in (("zh_CN", "zh"), ("zh-Hant-TW", "zh"), ("ru-RU", "ru"), ("en", "en"), ("sr_Latn_RS", "sr")):
            with self.subTest(locale=locale):
                self.assertEqual(wt.language_base(locale), expected)


class HTMLMetadataTests(unittest.TestCase):
    def test_login_languages_do_not_include_unrelated_selects(self):
        html = """<!doctype html><html lang="en-GB"><body>
        <select name="shipping"><option value="ru_RU">Shipping destination</option></select>
        <select id="language-switcher-locales" name="wp_lang">
          <option value="en_US">English</option><option value="zh_CN" selected>简体中文</option>
        </select><select name="topic"><option value="de_DE">Other option</option></select>
        </body></html>"""
        info = wt.analyze_html(html, "https://example.com/wp-login.php")
        self.assertEqual(info["html_language"], "en-GB")
        self.assertIn("en_US", info["login_languages"])
        self.assertIn("zh_CN", info["login_languages"])
        self.assertNotIn("ru_RU", info["login_languages"])
        self.assertNotIn("de_DE", info["login_languages"])

    def test_language_selector_supports_wordpress_name_attribute(self):
        info = wt.analyze_html('<select name="wp_lang"><option value="ru_RU">Русский</option></select>', "https://example.com/wp-login.php")
        self.assertIn("ru_RU", info["login_languages"])

    def test_assets_and_references_are_extracted_without_fetching(self):
        html = """<html lang="en"><head>
        <link rel="stylesheet" href="/wp-content/themes/my-theme/style.css?ver=1">
        <script src="https://cdn.example.net/wp-content/plugins/example-plugin/main.js"></script>
        <link rel="https://api.w.org/" href="https://example.com/blog/wp-json/">
        </head><body><img src="/uploads/%E6%B5%8B%E8%AF%95.jpg"></body></html>"""
        info = wt.analyze_html(html, "https://example.com/blog/")
        self.assertIn("my-theme", info["asset_themes"])
        self.assertIn("example-plugin", info["asset_plugins"])
        refs = {entry["url"] for entry in info["references"]}
        self.assertIn("https://example.com/blog/wp-json/", refs)
        self.assertIn("https://cdn.example.net/wp-content/plugins/example-plugin/main.js", refs)

    def test_tracking_identifiers_are_captured_for_later_correlation(self):
        html = """<html><head>
        <script src="https://www.googletagmanager.com/gtag/js?id=G-AB12CD34EF"></script>
        <script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC1234"></script>
        <script>gtag('config', 'G-AB12CD34EF');ga('create', 'UA-12345678-9', 'auto');
        ym(12345678, "init", {clickmap:true});</script>
        <script src="https://hm.baidu.com/hm.js?0123456789abcdef0123456789abcdef"></script>
        </head><body><ins data-ad-client="ca-pub-1234567890123456"></ins></body></html>"""
        info = wt.analyze_html(html, "https://example.com/")
        ids = {str(value) for values in info["tracking_ids"].values() for value in values}
        for expected in ("G-AB12CD34EF", "GTM-ABC1234", "UA-12345678-9", "12345678", "0123456789abcdef0123456789abcdef"):
            with self.subTest(identifier=expected):
                self.assertIn(expected, ids)
        self.assertTrue({"ca-pub-1234567890123456", "pub-1234567890123456"} & ids)

    def test_empty_html_is_not_wordpress_language_evidence(self):
        info = wt.analyze_html("", "https://example.com/")
        self.assertFalse(info["login_languages"])
        self.assertFalse(info["asset_plugins"])
        self.assertFalse(info["asset_themes"])


@contextmanager
def fixture_server(responder):
    """Exercise urllib against a local HTTP peer, never a public target."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def serve(self):
            requests.append((self.command, self.path))
            status, headers, body = responder(self.command, self.path)
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = serve
        do_OPTIONS = serve
        do_POST = serve

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def json_reply(data, status=200):
    return status, {"Content-Type": "application/json"}, json.dumps(data).encode()


def core_index(locales=None):
    route = user_route(*locales) if locales is not None else {"endpoints": [{"methods": ["GET"], "args": {}}]}
    return {"name": "Synthetic local fixture", "routes": {"/wp/v2/users": route, "/wp/v2/posts": {"endpoints": []}}}


class ReportInterpretationTests(unittest.TestCase):
    def test_demo_cli_writes_reports_without_any_network(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = str(Path(directory) / "demo")
            with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Demo attempted network")), mock.patch("sys.stdout", new=io.StringIO()):
                status = wt.main(["--demo", "--out", prefix, "--expected-languages", "en"])
            self.assertEqual(status, 0)
            report = json.loads(Path(prefix + ".json").read_text(encoding="utf-8"))
            self.assertTrue(report["demo"])
            self.assertFalse(report["attribution_assessed"])
            self.assertIn("zh_CN", report["interface_locales"]["additional_packs_indicated"])
            self.assertNotIn("en_US", report["interface_locales"]["additional_packs_indicated"])
            self.assertIn("FICTIONAL OFFLINE DEMO", Path(prefix + ".html").read_text(encoding="utf-8"))

    def test_builtin_english_is_not_a_pack_or_baseline_mismatch(self):
        def responder(method, path):
            return json_reply(core_index(["", "en_US"])) if path == "/wp-json/" else (404, {}, b"")

        with fixture_server(responder) as (target, requests):
            report = wt.collect(target, wt.Client(target, delay=0), expected_languages=["ru"], quick=True)
        locales = report["interface_locales"]
        self.assertEqual(locales["advertised"], ["en_US"])
        self.assertEqual(locales["additional_packs_indicated"], [])
        self.assertFalse(any("Interface language differs" in cue["signal"] for cue in report["review_cues"]))

    def test_empty_locale_enum_does_not_claim_known_absence(self):
        for invalid_enum in ([], [123], [""]):
            with self.subTest(enum=invalid_enum):
                def responder(method, path):
                    return json_reply(core_index(invalid_enum)) if path == "/wp-json/" else (404, {}, b"")

                with fixture_server(responder) as (target, requests):
                    report = wt.collect(target, wt.Client(target, delay=0), quick=True)
                self.assertEqual(report["interface_locales"]["availability"], "unknown")
                self.assertIn(("OPTIONS", "/wp-json/wp/v2/users"), requests)

    def test_blocked_endpoints_leave_interface_languages_unknown(self):
        with fixture_server(lambda method, path: (403, {}, b"Forbidden")) as (target, requests):
            report = wt.collect(target, wt.Client(target, delay=0), quick=True)
        self.assertEqual(report["interface_locales"]["availability"], "unknown")
        self.assertFalse(report["interface_locales"]["additional_packs_indicated"])
        self.assertFalse(report["wordpress"]["rest_api_observed"])
        self.assertEqual(len(requests), len(set(requests)), "A denied endpoint should not be retried")

    def test_target_markup_is_escaped_in_all_report_sections(self):
        target = "https://newsroom.example/"
        report = wt.collect(target, wt.Client(target, demo=True), quick=True)
        malicious = '<script>alert("remote")</script><img src=x onerror=alert(1)>'
        report["target"] = malicious
        report["wordpress"]["name"] = malicious
        report["review_cues"].append({"signal": malicious, "detail": malicious})
        rendered = wt.render_html(report)
        self.assertNotIn(malicious, rendered)
        self.assertNotIn("<script>", rendered)
        self.assertIn("&lt;script&gt;", rendered)
        self.assertIn("Content-Security-Policy", rendered)

    def test_invalid_url_in_remote_html_does_not_abort_collection(self):
        info = wt.analyze_html('<html lang="en"><img src="http://[broken/"><a href="/about/">About</a></html>', "https://example.com/")
        self.assertEqual(info["html_language"], "en")
        self.assertIn("https://example.com/about/", {x["url"] for x in info["references"]})

    def test_malformed_media_url_and_post_id_do_not_abort_collection(self):
        def responder(method, path):
            if path == "/wp-json/":
                return json_reply(core_index(["", "en_US"]))
            if path.startswith("/wp-json/wp/v2/media"):
                return json_reply([{"id": 3, "source_url": "https://[broken/", "slug": "valid-slug"}])
            if path.startswith("/wp-json/wp/v2/posts"):
                return json_reply([{"id": {"unexpected": "object"}, "date": "2026-01-01T08:00:00", "date_gmt": "2026-01-01T00:00:00"}])
            if path.startswith("/wp-json/wp/v2/users"):
                return json_reply([])
            return 404, {}, b""

        with fixture_server(responder) as (target, requests):
            report = wt.collect(target, wt.Client(target, delay=0), quick=False)
        self.assertTrue(report["wordpress"]["rest_api_observed"])
        self.assertIn("completed_at", report)


class HTTPTransportTests(unittest.TestCase):
    def test_query_rest_fallback_keeps_subsequent_endpoints_in_query(self):
        def responder(method, path):
            parts = urlsplit(path)
            query = parse_qs(parts.query)
            route = query.get("rest_route", [None])[0]
            if route == "/":
                return json_reply(core_index(["", "en_US", "zh_CN"]))
            if route and route.startswith("/wp/v2/"):
                return json_reply([])
            if path == "/":
                return 200, {"Content-Type": "text/html"}, b'<html lang="en"></html>'
            return 404, {}, b""

        with fixture_server(responder) as (target, requests):
            report = wt.collect(target, wt.Client(target, delay=0))
        self.assertTrue(report["wordpress"]["rest_api_observed"])
        self.assertIn("rest_route=", report["wordpress"]["api_root"])
        sample_paths = [path for method, path in requests if parse_qs(urlsplit(path).query).get("rest_route", [""])[0].startswith("/wp/v2/")]
        self.assertEqual(len(sample_paths), 4)
        self.assertTrue(all(method == "GET" for method, path in requests))

    def test_options_fallback_recovers_schema_without_posting(self):
        def responder(method, path):
            if path == "/wp-json/":
                return json_reply(core_index())
            if path == "/wp-json/wp/v2/users" and method == "OPTIONS":
                return json_reply({"schema": {"properties": {"locale": {"enum": ["", "en_US", "ru_RU"]}}}})
            return 404, {}, b""

        with fixture_server(responder) as (target, requests):
            report = wt.collect(target, wt.Client(target, delay=0), quick=True)
        self.assertEqual(report["interface_locales"]["additional_packs_indicated"], ["ru_RU"])
        self.assertIn(("OPTIONS", "/wp-json/wp/v2/users"), requests)
        self.assertFalse(any(method == "POST" for method, path in requests))

    def test_redirects_stay_in_scope_and_preserve_options_method(self):
        def responder(method, path):
            return (302, {"Location": "/next"}, b"") if path == "/" else (302, {"Location": "https://outside.example/"}, b"")

        with fixture_server(responder) as (target, requests):
            client = wt.Client(target, delay=0)
            result = client.fetch(target, method="OPTIONS")
        self.assertEqual(requests, [("OPTIONS", "/"), ("OPTIONS", "/next")])
        self.assertIsNone(result)
        self.assertEqual(len(client.records), 2)
        self.assertTrue(any("outside target" in warning for warning in client.warnings))

    def test_request_budget_counts_every_redirect(self):
        with fixture_server(lambda method, path: (302, {"Location": "/again"}, b"")) as (target, requests):
            client = wt.Client(target, delay=0, max_requests=2)
            client.fetch(target)
            client.fetch(target + "never")
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(client.records), 2)
        self.assertTrue(any("budget" in warning for warning in client.warnings))

    def test_429_stops_all_remaining_network_requests(self):
        with fixture_server(lambda method, path: (429, {"Retry-After": "60"}, b"Rate limited")) as (target, requests):
            client = wt.Client(target, delay=0)
            report = wt.collect(target, client)
        self.assertEqual(len(requests), 1)
        self.assertEqual(report["requests"][0]["status"], 429)
        self.assertEqual(report["requests"][0]["headers"]["retry-after"], "60")
        self.assertTrue(any("429" in warning for warning in report["warnings"]))

    def test_truncated_json_is_not_interpreted_as_complete_evidence(self):
        body = json.dumps({"padding": "x" * 200}).encode()
        with fixture_server(lambda method, path: (200, {"Content-Type": "application/json"}, body)) as (target, requests), mock.patch.object(wt, "MAX_BYTES", 64):
            response = wt.Client(target, delay=0).fetch(target)
        self.assertTrue(response["truncated"])
        self.assertEqual(response["body"], body[:64])
        self.assertEqual(response["sha256"], hashlib.sha256(body[:64]).hexdigest())
        self.assertIsNone(wt.response_json(response))

    def test_non_read_only_methods_are_rejected_before_network(self):
        client = wt.Client("https://newsroom.example/", demo=True)
        with self.assertRaises(ValueError):
            client.fetch(client.target, method="POST")
        self.assertEqual(client.records, [])


if __name__ == "__main__":
    unittest.main()
