"""Behavioral regression tests for v0.2 batch reports and observed parser bugs.

All domains, account identifiers and responses below are fictional. Tests use
in-memory fixtures or the existing loopback HTTP server; no public site is used.
"""

import copy
import csv
import hashlib
import io
import json
import os
import re
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from test_wp_telltale import core_index, fixture_server, json_reply, wt


def run_cli(arguments):
    """Return argparse and normal exit statuses without emitting test chatter."""
    stdout, stderr = io.StringIO(), io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            status = wt.main(arguments)
        except SystemExit as exc:
            status = exc.code
    return status, stdout.getvalue(), stderr.getvalue()


def synthetic_report(target="https://newsroom.example/"):
    report = wt.collect("https://newsroom.example/", wt.Client("https://newsroom.example/", demo=True), quick=True)
    report["target"] = target
    report["homepage"]["tracking_ids"] = {}
    report["homepage"]["tracking_evidence"] = []
    return report


def result_with_ids(target, identifiers):
    report = synthetic_report(target)
    report["homepage"]["tracking_ids"] = identifiers
    report["homepage"]["tracking_evidence"] = [
        {"kind": kind, "value": value, "source_url": target,
         "snippet": "Fictional configuration: " + value}
        for kind, values in identifiers.items() for value in values
    ]
    report["homepage"]["source"] = {
        "url": target, "request_id": 1,
        "observed_at": "2026-01-01T00:00:00+00:00", "sha256": "0" * 64,
    }
    return {"target": target, "status": "observed", "report": report, "error": None}


class TargetFileTests(unittest.TestCase):
    def test_windows_bom_comments_normalization_duplicates_and_bad_lines(self):
        text = ("\ufeff# Investigation targets\r\n\r\nExample.COM\r\n"
                "https://example.com/\r\n https://example.com/blog \r\n"
                "https://analyst:do-not-print-this@example.net/\r\n"
                "ftp://bad.example/\r\nnot a domain\r\nhttps://second.example/\r\n")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.txt"
            path.write_bytes(text.encode("utf-8"))
            loaded = wt.load_targets(path)
        self.assertEqual(loaded["targets"], [
            "https://example.com/", "https://example.com/blog/", "https://second.example/",
        ])
        self.assertEqual([item["line"] for item in loaded["rejected_inputs"]], [6, 7, 8])
        self.assertEqual(len(loaded["duplicate_inputs"]), 1)
        duplicate = loaded["duplicate_inputs"][0]
        self.assertEqual((duplicate["line"], duplicate["first_line"]), (4, 3))
        self.assertEqual(duplicate["target"], "https://example.com/")
        serialized = json.dumps(loaded)
        self.assertNotIn("do-not-print-this", serialized)
        self.assertNotIn("analyst", serialized)

    def test_different_installation_paths_are_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.txt"
            path.write_text("https://example.com/\nhttps://example.com/blog/\n", encoding="utf-8")
            loaded = wt.load_targets(path)
        self.assertEqual(len(loaded["targets"]), 2)

    def test_empty_or_invalid_only_file_exits_before_collection(self):
        for content in ("# no targets\n\n", "file:///tmp/local\nhttps://person:secret@example.com/\n"):
            with self.subTest(content=content), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "targets.txt"
                path.write_text(content, encoding="utf-8")
                with mock.patch.object(wt, "collect") as collect:
                    status, stdout, stderr = run_cli(["--targets", str(path), "--out", str(Path(directory) / "run")])
                self.assertEqual(status, 2)
                collect.assert_not_called()
                self.assertNotIn("person:secret", stdout + stderr)

    def test_missing_target_file_is_input_error_without_network(self):
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(wt, "collect") as collect:
            status, _, _ = run_cli(["--targets", str(Path(directory) / "missing.txt")])
        self.assertEqual(status, 2)
        collect.assert_not_called()

    def test_target_modes_are_mutually_exclusive(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "targets.txt"
            path.write_text("https://example.com/\n", encoding="utf-8")
            for arguments in (
                ["https://example.com/", "--targets", str(path)],
                ["--demo", "--targets", str(path)],
                ["--demo-batch", "--targets", str(path)],
                ["--demo", "--demo-batch"],
            ):
                with self.subTest(arguments=arguments), mock.patch.object(wt, "collect") as collect:
                    status, _, _ = run_cli(arguments)
                self.assertEqual(status, 2)
                collect.assert_not_called()


class ParserRegressionTests(unittest.TestCase):
    def test_sitekit_platform_account_is_not_a_publisher_pivot(self):
        markup = '''<meta name="google-adsense-platform-account" content="ca-host-pub-1111111111111111">
          <meta name="google-adsense-platform-domain" content="sitekit.withgoogle.com">
          <script src="https://ads.example/ads.js?client=ca-pub-2222222222222222"></script>
          <div data-publisher="pub-3333333333333333"></div>'''
        info = wt.analyze_html(markup, "https://newsroom.example/")
        ids = info["tracking_ids"].get("google_adsense", [])
        self.assertIn("ca-pub-2222222222222222", ids)
        self.assertTrue({"pub-3333333333333333", "ca-pub-3333333333333333"}.intersection(ids))
        self.assertFalse(any("1111111111111111" in value for value in ids))
        self.assertFalse(any("1111111111111111" in item["value"] for item in info["tracking_evidence"]))

    def test_wildcard_paths_do_not_become_plugin_or_theme_names(self):
        markup = '''<script>const excludes=["/wp-content/plugins/*", "/wp-content/themes/*"];</script>
          <script src="/wp-content/plugins/example-plugin/app.js"></script>
          <link href="/wp-content/themes/example-theme/style.css" rel="stylesheet">'''
        info = wt.analyze_html(markup, "https://newsroom.example/")
        self.assertEqual(info["asset_plugins"], ["example-plugin"])
        self.assertEqual(info["asset_themes"], ["example-theme"])

    def test_inline_css_urls_are_recorded_and_non_http_urls_ignored(self):
        markup = '''<html><style>
          .hero { background: url("https://old-brand.example/uploads/header.jpg"); }
          .small { background-image: url(/images/small.png); }
          .inline { background-image: url(data:image/png;base64,AAAA); }
          </style><div style="background:url('../images/card.jpg')"></div></html>'''
        with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Parser attempted network")):
            info = wt.analyze_html(markup, "https://newsroom.example/blog/")
        refs = {item["url"] for item in info["references"]}
        self.assertIn("https://old-brand.example/uploads/header.jpg", refs)
        self.assertIn("https://newsroom.example/images/small.png", refs)
        self.assertIn("https://newsroom.example/images/card.jpg", refs)
        self.assertFalse(any(value.startswith("data:") for value in refs))

    def test_collection_does_not_follow_css_asset_links(self):
        markup = b'<html lang="en"><style>.hero{background:url(https://outside.example/collect)}</style></html>'

        def responder(method, path):
            if path == "/":
                return 200, {"Content-Type": "text/html"}, markup
            if path == "/wp-json/":
                return json_reply(core_index(["", "en_US"]))
            return 404, {}, b""

        with fixture_server(responder) as (target, requests):
            report = wt.collect(target, wt.Client(target, delay=0), quick=True)
        self.assertIn("https://outside.example/collect", {x["url"] for x in report["homepage"]["references"]})
        self.assertFalse(any("outside.example" in item["url"] for item in report["requests"]))
        self.assertNotIn(("GET", "/collect"), requests)

    def test_console_does_not_claim_observed_dashboard_language(self):
        report = synthetic_report()
        summary = wt.console_summary(report)
        self.assertNotIn("Interface and homepage language differ:", summary)
        self.assertIn("Selected dashboard language: unknown", summary)
        self.assertIn("zh_CN", summary)
        self.assertEqual(report["interface_locales"]["admin_selected_language"], "not observed")


class SharedIdentifierTests(unittest.TestCase):
    def test_exact_identifier_match_carries_both_targets_and_evidence(self):
        results = [result_with_ids("https://one.example/", {"google_analytics_ga4": ["G-AB12CD34EF"]}),
                   result_with_ids("https://two.example/", {"google_analytics_ga4": ["G-AB12CD34EF", "G-ZZ98YY76XX"]})]
        matches = wt.find_shared_identifiers(results)
        self.assertEqual(len(matches), 1)
        match = matches[0]
        self.assertEqual((match["kind"], match["value"]), ("google_analytics_ga4", "G-AB12CD34EF"))
        self.assertEqual(set(match["targets"]), {"https://one.example/", "https://two.example/"})
        self.assertTrue(match["evidence"])

    def test_www_aliases_paths_ports_and_schemes_do_not_multiply_hosts(self):
        targets = ["https://one.example/", "https://www.one.example/", "https://one.example/blog/", "http://one.example:8080/"]
        results = [result_with_ids(target, {"yandex_metrica": ["12345678"]}) for target in targets]
        self.assertEqual(wt.find_shared_identifiers(results), [])

    def test_same_digits_from_different_identifier_types_are_not_a_match(self):
        results = [result_with_ids("https://one.example/", {"yandex_metrica": ["12345678"]}),
                   result_with_ids("https://two.example/", {"meta_pixel": ["12345678"]})]
        self.assertEqual(wt.find_shared_identifiers(results), [])

    def test_missing_reports_do_not_hide_a_match_between_completed_targets(self):
        results = [result_with_ids("https://one.example/", {"yandex_metrica": ["12345678"]}),
                   {"target": "https://blocked.example/", "status": "error", "report": None, "error": "Synthetic failure"},
                   result_with_ids("https://two.example/", {"yandex_metrica": ["12345678"]})]
        self.assertEqual(len(wt.find_shared_identifiers(results)), 1)


class BatchCLITests(unittest.TestCase):
    def test_batch_without_output_prefix_creates_report_files(self):
        with tempfile.TemporaryDirectory() as directory:
            original = Path.cwd()
            try:
                os.chdir(directory)
                status, _, _ = run_cli(["--demo-batch"])
            finally:
                os.chdir(original)
            self.assertEqual(status, 0)
            reports = Path(directory) / "reports"
            paths = list(reports.glob("*"))
            self.assertEqual({p.suffix for p in paths}, {".html", ".json", ".csv"})
            self.assertEqual(len({p.stem for p in paths}), 1)

    def test_batch_demo_writes_one_combined_report_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "demo"
            with mock.patch("urllib.request.OpenerDirector.open", side_effect=AssertionError("Demo attempted network")):
                status, stdout, _ = run_cli(["--demo-batch", "--out", str(prefix)])
            self.assertEqual(status, 0)
            batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(batch["mode"], "batch")
            self.assertGreaterEqual(len(batch["results"]), 2)
            self.assertTrue(all(item["report"]["demo"] for item in batch["results"]))
            self.assertTrue(batch["shared_identifiers"])
            rendered = prefix.with_suffix(".html").read_text(encoding="utf-8")
            self.assertIn("FICTIONAL", rendered)
            self.assertIn("Content-Security-Policy", rendered)
            self.assertTrue(prefix.with_suffix(".csv").is_file())
            self.assertEqual(len(list(Path(directory).glob("*.html"))), 1)

    def test_mixed_results_continue_and_preserve_unknown_and_error_states(self):
        targets = ["https://good.example/", "https://broken.example/", "https://blocked.example/", "https://last.example/"]
        good = synthetic_report()
        unknown = copy.deepcopy(good)
        unknown["wordpress"] = {"rest_api_observed": False}
        unknown["interface_locales"].update(availability="unknown", advertised=[], additional_packs_indicated=[], evidence=[])

        def collect(target, client, *args, **kwargs):
            if "broken" in target:
                raise ValueError("Synthetic parser failure")
            report = copy.deepcopy(unknown if "blocked" in target else good)
            report["target"] = target
            return report

        with tempfile.TemporaryDirectory() as directory:
            target_file, prefix = Path(directory) / "targets.txt", Path(directory) / "combined"
            target_file.write_text("\n".join(targets) + "\n", encoding="utf-8")
            with mock.patch.object(wt, "collect", side_effect=collect) as mocked:
                status, stdout, _ = run_cli(["--targets", str(target_file), "--out", str(prefix)])
            batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(status, 1)
            self.assertEqual(mocked.call_count, 4)
            self.assertEqual([item["status"] for item in batch["results"]], ["observed", "error", "unknown", "observed"])
            self.assertIn("Synthetic parser failure", batch["results"][1]["error"])
            self.assertEqual(batch["results"][2]["report"]["interface_locales"]["availability"], "unknown")
            self.assertIn("[4/4]", stdout)
            self.assertTrue(prefix.with_suffix(".html").exists())
            self.assertTrue(prefix.with_suffix(".csv").exists())

    def test_target_file_drives_separate_real_loopback_collections(self):
        def responder(method, path):
            if path == "/":
                return 200, {"Content-Type": "text/html"}, b'<html lang="en-US"></html>'
            if path == "/wp-json/":
                return json_reply(core_index(["", "en_US", "ru_RU"]))
            return 404, {}, b""

        with fixture_server(responder) as (good, good_requests), fixture_server(lambda method, path: (403, {}, b"Denied")) as (blocked, blocked_requests):
            with tempfile.TemporaryDirectory() as directory:
                target_file, prefix = Path(directory) / "targets.txt", Path(directory) / "local"
                target_file.write_text(good + "\n" + blocked + "\n", encoding="utf-8")
                status, _, _ = run_cli(["--targets", str(target_file), "--quick", "--delay", "0", "--out", str(prefix)])
                batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
        self.assertEqual(status, 1)
        self.assertEqual([item["target"] for item in batch["results"]], [good, blocked])
        self.assertEqual([item["status"] for item in batch["results"]], ["observed", "unknown"])
        self.assertEqual(batch["results"][0]["report"]["interface_locales"]["additional_packs_indicated"], ["ru_RU"])
        self.assertEqual(batch["results"][1]["report"]["interface_locales"]["availability"], "unknown")
        self.assertTrue(good_requests)
        self.assertTrue(blocked_requests)
        self.assertTrue(all(method in {"GET", "OPTIONS"} for method, _ in good_requests + blocked_requests))

    def test_valid_targets_continue_after_rejected_credential_line(self):
        with tempfile.TemporaryDirectory() as directory:
            target_file, prefix = Path(directory) / "targets.txt", Path(directory) / "combined"
            target_file.write_text("https://person:never-display@example.com/\nhttps://newsroom.example/\n", encoding="utf-8")
            report = synthetic_report()
            with mock.patch.object(wt, "collect", return_value=report) as mocked:
                status, stdout, stderr = run_cli(["--target-file", str(target_file), "--out", str(prefix)])
            self.assertEqual(status, 1)
            mocked.assert_called_once()
            batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(batch["rejected_inputs"][0]["line"], 1)
            output = stdout + stderr + "".join(prefix.with_suffix(ext).read_text(encoding="utf-8") for ext in (".html", ".json", ".csv"))
            self.assertNotIn("never-display", output)
            self.assertNotIn("person:", output)

    def test_keyboard_interrupt_saves_completed_results_and_pending_targets(self):
        targets = ["https://first.example/", "https://second.example/", "https://third.example/"]
        report = synthetic_report(targets[0])
        with tempfile.TemporaryDirectory() as directory:
            target_file, prefix = Path(directory) / "targets.txt", Path(directory) / "partial"
            target_file.write_text("\n".join(targets) + "\n", encoding="utf-8")
            with mock.patch.object(wt, "collect", side_effect=[report, KeyboardInterrupt()]) as mocked:
                status, _, _ = run_cli(["--targets", str(target_file), "--out", str(prefix)])
            self.assertEqual(status, 130)
            self.assertEqual(mocked.call_count, 2)
            batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertTrue(batch["interrupted"])
            self.assertEqual(batch["results"][0]["status"], "observed")
            self.assertEqual(batch["results"][0]["target"], targets[0])
            self.assertIn(targets[2], batch["pending_targets"])
            self.assertTrue(prefix.with_suffix(".html").is_file())
            self.assertTrue(prefix.with_suffix(".csv").is_file())

    def test_batch_evidence_paths_are_separate_portable_and_never_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            evidence = root / "evidence"
            status, _, _ = run_cli(["--demo-batch", "--out", str(root / "first"), "--evidence-dir", str(evidence)])
            self.assertEqual(status, 0)
            batch = json.loads((root / "first.json").read_text(encoding="utf-8"))
            manifests = list(evidence.glob("*/manifest.json"))
            self.assertEqual(len(manifests), len(batch["results"]))
            for folder in evidence.iterdir():
                if folder.is_dir():
                    self.assertNotRegex(folder.name, r'[<>:"/\\|?*]')
                    self.assertFalse(folder.name.endswith((" ", ".")))
            before = {str(p.relative_to(evidence)): p.read_bytes() for p in evidence.rglob("*") if p.is_file()}
            self.assertTrue(any(name.endswith(".bin") for name in before))
            for item in batch["results"]:
                for request in item["report"]["requests"]:
                    if request.get("evidence_file"):
                        self.assertEqual(hashlib.sha256(Path(request["evidence_file"]).read_bytes()).hexdigest(), request["sha256"])
            run_cli(["--demo-batch", "--out", str(root / "second"), "--evidence-dir", str(evidence)])
            for name, content in before.items():
                self.assertEqual((evidence / name).read_bytes(), content)

    def test_batch_html_escapes_remote_values_outside_fixed_ui_script(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "demo"
            status, _, _ = run_cli(["--demo-batch", "--out", str(prefix)])
            self.assertEqual(status, 0)
            batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
        malicious = '</script><script>alert("remote")</script><img src=x onerror=alert(1)>'
        report = batch["results"][0]["report"]
        report["wordpress"]["name"] = malicious
        report["review_cues"].append({"signal": malicious, "detail": malicious})
        report["warnings"].append(malicious)
        rendered = wt.render_batch_html(batch)
        self.assertNotIn(malicious, rendered)
        self.assertIn("&lt;script&gt;", rendered)
        for script in re.findall(r"<script\b[^>]*>(.*?)</script\s*>", rendered, flags=re.I | re.S):
            self.assertNotIn("remote", script)
            self.assertNotIn("onerror", script)

    def test_csv_remote_error_values_cannot_start_spreadsheet_formulas(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix = Path(directory) / "demo"
            status, _, _ = run_cli(["--demo-batch", "--out", str(prefix)])
            self.assertEqual(status, 0)
            batch = json.loads(prefix.with_suffix(".json").read_text(encoding="utf-8"))
        formulas = ['=HYPERLINK("https://example.org/","example")', "+SUM(1,2)", "-SUM(1,2)", "@SUM(1,2)", " \t=1+1"]
        for formula in formulas:
            with self.subTest(formula=formula):
                batch["results"] = [{"target": "https://error.example/", "status": "error", "report": None, "error": formula}]
                rows = list(csv.reader(io.StringIO(wt.render_batch_csv(batch))))
                cells = [cell for row in rows[1:] for cell in row]
                matching = [cell for cell in cells if formula in cell]
                self.assertTrue(matching, "The error value must remain reviewable in the CSV")
                self.assertTrue(all(cell.startswith("'") for cell in matching))


if __name__ == "__main__":
    unittest.main()
