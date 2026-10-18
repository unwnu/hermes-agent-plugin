import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import sync_upstream as sync
from validate import validate
from package import package


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


if __name__ == "__main__":
    unittest.main()
