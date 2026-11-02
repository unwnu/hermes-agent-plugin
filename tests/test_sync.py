import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
import hashlib
import http.client
import io
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
import urllib.error
import urllib.response
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_upstream as sync
from validate import validate
from package import package


class Response:
    def __init__(self, url, data=b"ok"):
        self.url = url
        self.data = data
        self.offset = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def read(self, limit):
        chunk = self.data[self.offset:self.offset + limit]
        self.offset += len(chunk)
        return chunk


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://raw.githubusercontent.com/test", code, "error", headers or {}, None)


def framed_response(url, raw):
    class Socket:
        def makefile(self, *args):
            return io.BufferedReader(io.BytesIO(raw))

    response = http.client.HTTPResponse(Socket())
    response.begin()
    return urllib.response.addinfourl(response, response.headers, url)


class DownloadRetryTests(unittest.TestCase):
    URL = "https://raw.githubusercontent.com/NousResearch/hermes-agent/test/LICENSE"

    def test_recovers_from_rate_limit_server_error_and_network_error(self):
        errors = [http_error(429), http_error(503), urllib.error.URLError("connection reset")]
        with patch.object(sync, "open_request", side_effect=errors + [Response(self.URL)]) as opener, \
                patch.object(sync.time, "sleep") as sleep, patch.object(sync.random, "uniform", return_value=1):
            self.assertEqual(sync.request(self.URL), b"ok")
        self.assertEqual(opener.call_count, 4)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2, 4])

    def test_retry_after_is_honored_but_cannot_exceed_budget(self):
        with patch.object(sync, "open_request", side_effect=[http_error(429, {"Retry-After": "3"}), Response(self.URL)]) as opener, \
                patch.object(sync.time, "sleep") as sleep:
            self.assertEqual(sync.request(self.URL), b"ok")
        sleep.assert_called_once_with(3)
        self.assertEqual(opener.call_count, 2)

        with patch.object(sync, "open_request", side_effect=http_error(429, {"Retry-After": "300"})) as opener, \
                patch.object(sync.time, "sleep") as sleep:
            with self.assertRaises(urllib.error.HTTPError):
                sync.request(self.URL)
        opener.assert_called_once()
        sleep.assert_not_called()

    def test_retry_after_http_date(self):
        now = datetime(2026, 11, 2, 12, tzinfo=timezone.utc)
        header = format_datetime(now + timedelta(seconds=30), usegmt=True)

        class Clock:
            @staticmethod
            def now(tz):
                return now

        with patch.object(sync, "datetime", Clock):
            self.assertEqual(sync.retry_delay(http_error(429, {"Retry-After": header}), 0), 30)

    def test_malformed_retry_after_falls_back_and_incomplete_read_recovers(self):
        with patch.object(sync, "open_request", side_effect=[http_error(429, {"Retry-After": "invalid"}),
                                                           http.client.IncompleteRead(b"partial"), Response(self.URL)]) as opener, \
                patch.object(sync.time, "sleep") as sleep, patch.object(sync.random, "uniform", return_value=1):
            self.assertEqual(sync.request(self.URL), b"ok")
        self.assertEqual(opener.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [1, 2])

    def test_rate_limit_403_retries_only_with_retry_after(self):
        with patch.object(sync, "open_request", side_effect=[http_error(403, {"Retry-After": "1"}), Response(self.URL)]) as opener, \
                patch.object(sync.time, "sleep") as sleep:
            self.assertEqual(sync.request(self.URL), b"ok")
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_retries_exhaust_and_permanent_errors_do_not_retry(self):
        for code, expected in ((429, 4), (500, 4), (401, 1), (403, 1), (404, 1)):
            with self.subTest(code=code), patch.object(sync, "open_request", side_effect=http_error(code)) as opener, \
                    patch.object(sync.time, "sleep"), patch.object(sync.random, "uniform", return_value=1):
                with self.assertRaises(urllib.error.HTTPError):
                    sync.request(self.URL)
                self.assertEqual(opener.call_count, expected)

    def test_shared_deadline_stops_requests(self):
        with patch.object(sync, "open_request") as opener:
            with self.assertRaises(TimeoutError):
                sync.request(self.URL, deadline=sync.time.monotonic() - 1)
        opener.assert_not_called()

    def test_budget_exhausted_during_retry(self):
        now = [0]

        def slow_failure(req, timeout):
            now[0] += 2
            raise http_error(503)

        with patch.object(sync.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(sync.time, "sleep", side_effect=lambda seconds: now.__setitem__(0, now[0] + seconds)) as sleep, \
                patch.object(sync.random, "uniform", return_value=1), patch.object(sync, "open_request", side_effect=slow_failure) as opener:
            with self.assertRaises(urllib.error.HTTPError):
                sync.request(self.URL, deadline=4)
        self.assertEqual(opener.call_count, 2)
        sleep.assert_called_once_with(1)

    def test_slow_stream_stops_at_shared_deadline(self):
        now = [0]

        class SlowResponse(Response):
            fp = None

            def read1(self, limit):
                now[0] += 2
                return b"x"

        response = SlowResponse(self.URL)
        response.fp = response
        with patch.object(sync.time, "monotonic", side_effect=lambda: now[0]), \
                patch.object(sync, "open_request", return_value=response) as opener, patch.object(sync.time, "sleep") as sleep:
            with self.assertRaises(TimeoutError):
                sync.request(self.URL, deadline=4)
        opener.assert_called_once()
        sleep.assert_not_called()

    def test_real_http_response_framing_is_preserved(self):
        bodies = (b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive\r\n\r\nok",
                  b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n2\r\nok\r\n0\r\n\r\n")
        for raw in bodies:
            with self.subTest(raw=raw), patch.object(sync, "open_request", return_value=framed_response(self.URL, raw)):
                self.assertEqual(sync.request(self.URL), b"ok")

    def test_invalid_destination_and_oversized_response_are_not_retried(self):
        for response, limit in ((Response("https://elsewhere.example/test"), sync.MAX_FILE),
                                (Response(self.URL, b"long"), 2)):
            with self.subTest(response=response.url), patch.object(sync, "open_request", return_value=response) as opener:
                with self.assertRaises(ValueError):
                    sync.request(self.URL, limit=limit)
                opener.assert_called_once()

    def test_token_only_sent_to_api_origin(self):
        seen = []

        def respond(req, timeout):
            seen.append((req.full_url, req.get_header("Authorization")))
            return Response(req.full_url)

        with patch.dict(sync.os.environ, {"GH_TOKEN": "test-token"}), patch.object(sync, "open_request", side_effect=respond):
            sync.request("https://api.github.com/repos/NousResearch/hermes-agent/commits/main")
            sync.request(self.URL)
        self.assertEqual(seen[0][1], "Bearer test-token")
        self.assertIsNone(seen[1][1])


class MirrorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        shutil.copytree(ROOT / "plugins", self.root / "plugins")
        shutil.copytree(ROOT / ".agents", self.root / ".agents")
        shutil.copytree(ROOT / ".claude-plugin", self.root / ".claude-plugin")
        self.lock = sync.read_json(self.root / sync.LOCK)
        self.license = (self.root / sync.PLUGIN / "LICENSE").read_bytes()
        self.files = [({"path": f["path"], "mode": f["mode"]},
                       (self.root / sync.DEST / f["path"]).read_bytes()) for f in self.lock["files"]]

    def tearDown(self):
        self.temp.cleanup()

    def changed_snapshot(self):
        files = copy.deepcopy(self.files)
        index = next(i for i, (f, _) in enumerate(files) if f["path"] == "SKILL.md")
        entry, data = files[index]
        files[index] = (entry, data + b"\nTest upstream addition.\n")
        lock = copy.deepcopy(self.lock)
        lock["commit"] = "a" * 40
        for record in lock["files"]:
            if record["path"] == "SKILL.md":
                data = files[index][1]
                record.update(sha256=hashlib.sha256(data).hexdigest(), git_blob_sha=sync.git_blob_sha(data), bytes=len(data))
        lock["content_sha256"] = sync.fingerprint(lock["files"])
        return files, lock

    def test_current_package_validates(self):
        validate(self.root)

    def test_unrelated_upstream_commit_does_not_change_version_or_lock(self):
        lock = copy.deepcopy(self.lock)
        lock["commit"] = "b" * 40
        before = (self.root / sync.LOCK).read_bytes()
        result = sync.apply_snapshot(self.root, self.files, self.license, lock)
        self.assertFalse(result["changed"])
        self.assertEqual(before, (self.root / sync.LOCK).read_bytes())

    def test_skill_change_bumps_all_manifests_once(self):
        files, lock = self.changed_snapshot()
        current = sync.read_json(self.root / sync.PLUGIN / "plugin.json")["version"]
        result = sync.apply_snapshot(self.root, files, self.license, lock)
        self.assertEqual(result["version"], sync.next_version(current))
        validate(self.root)
        again = sync.apply_snapshot(self.root, files, self.license, lock)
        self.assertFalse(again["changed"])
        self.assertEqual(result["version"], again["version"])

    def test_removed_file_is_removed_from_mirror(self):
        files, lock = self.changed_snapshot()
        removed = next(f["path"] for f in lock["files"] if f["path"].startswith("references/"))
        files = [(f, d) for f, d in files if f["path"] != removed]
        lock["files"] = [f for f in lock["files"] if f["path"] != removed]
        lock["content_sha256"] = sync.fingerprint(lock["files"])
        sync.apply_snapshot(self.root, files, self.license, lock)
        self.assertFalse((self.root / sync.DEST / removed).exists())

    def test_license_change_fails_before_writing(self):
        files, lock = self.changed_snapshot()
        lock["license_sha256"] = "c" * 64
        before = (self.root / sync.LOCK).read_bytes()
        with self.assertRaisesRegex(ValueError, "license changed"):
            sync.apply_snapshot(self.root, files, b"Different terms", lock)
        self.assertEqual(before, (self.root / sync.LOCK).read_bytes())

    def test_path_traversal_and_special_paths_rejected(self):
        for path in ("../x", "/tmp/x", "a/../../x", "a\\b", ".git/config", "a//b", "a/./b", "a\nb"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                sync.safe_path(path)

    def test_symlink_and_submodule_rejected(self):
        for mode, kind in (("120000", "blob"), ("160000", "commit")):
            parents = [{"tree": [{"path": p, "type": "tree", "sha": "a" * 40}]} for p in sync.SOURCE_PATH.split("/")]
            final = {"tree": [{"path": "SKILL.md", "type": kind, "mode": mode, "sha": "b" * 40}]}
            with patch.object(sync, "api", side_effect=parents + [final]), self.assertRaises(ValueError):
                sync.collect_entries("c" * 40)

    def test_truncated_tree_rejected(self):
        with patch.object(sync, "api", return_value={"truncated": True}), self.assertRaises(ValueError):
            sync.collect_entries("c" * 40)

    def test_corruption_is_detected(self):
        path = self.root / sync.DEST / "SKILL.md"
        path.write_bytes(path.read_bytes() + b"corrupt")
        with self.assertRaises(AssertionError):
            validate(self.root)

    def test_archive_is_deterministic_and_plugin_only(self):
        import zipfile
        archive, _ = package(self.root)
        first = archive.read_bytes()
        package(self.root)
        self.assertEqual(first, archive.read_bytes())
        with zipfile.ZipFile(archive) as z:
            self.assertIn("hermes-agent/.codex-plugin/plugin.json", z.namelist())
            self.assertIn("hermes-agent/upstream-lock.json", z.namelist())
            self.assertTrue(all(p.startswith("hermes-agent/") for p in z.namelist()))
            self.assertFalse(any("scripts/sync_upstream" in p for p in z.namelist()))

    def test_failed_license_download_does_not_touch_package(self):
        entry, data = next((entry, data) for entry, data in self.files if entry["path"] == "SKILL.md")
        entry = {**entry, "sha": sync.git_blob_sha(data)}
        before = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "plugins").rglob("*") if p.is_file()}
        with patch.object(sync, "ROOT", self.root), patch.object(sync, "collect_entries", return_value=[entry]), \
                patch.object(sync, "open_request", side_effect=[Response("https://raw.githubusercontent.com/test", data)] + [http_error(429)] * 4), \
                patch.object(sync.time, "sleep"), patch.object(sync.random, "uniform", return_value=1), \
                patch.object(sync, "apply_snapshot") as apply, patch.object(sys, "argv", ["sync_upstream.py", "--commit", "a" * 40]):
            with self.assertRaises(urllib.error.HTTPError):
                sync.main()
        after = {p.relative_to(self.root): p.read_bytes() for p in (self.root / "plugins").rglob("*") if p.is_file()}
        self.assertEqual(before, after)
        apply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
