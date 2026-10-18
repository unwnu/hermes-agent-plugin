import json
from pathlib import Path
import sys
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import release
from sync_upstream import SameOriginRedirect


class ReleaseTests(unittest.TestCase):
    def test_cross_origin_redirect_rejected_before_token_forwarding(self):
        req = urllib.request.Request("https://api.github.com/repos/example/repo", headers={"Authorization": "Bearer test-only"})
        with self.assertRaisesRegex(ValueError, "Cross-origin"):
            SameOriginRedirect().redirect_request(req, None, 302, "Found", {}, "https://outside.invalid/receive")
        with self.assertRaisesRegex(ValueError, "Cross-origin"):
            SameOriginRedirect().redirect_request(req, None, 302, "Found", {}, "http://api.github.com/receive")

    def test_same_origin_redirect_allowed(self):
        req = urllib.request.Request("https://api.github.com/old", headers={"Authorization": "Bearer test-only"})
        redirected = SameOriginRedirect().redirect_request(req, None, 302, "Found", {}, "https://api.github.com/new")
        self.assertEqual(redirected.full_url, "https://api.github.com/new")

    def tree_responses(self, sha="expected-tree"):
        return [{"tree": [{"path": "plugins", "type": "tree", "sha": "plugins-tree"}]},
                {"tree": [{"path": "hermes-agent", "type": "tree", "sha": sha}]}]

    def test_existing_tag_with_same_plugin_is_accepted(self):
        responses = [{"object": {"type": "commit", "sha": "old-commit"}}] + self.tree_responses()
        with patch.object(release, "request", side_effect=responses) as req:
            self.assertEqual(release.ensure_tag("https://api.github.com/repos/a/b", "v1.0.0", "new-commit", "expected-tree"), "old-commit")
            self.assertTrue(all(c.args[0] == "GET" for c in req.call_args_list))

    def test_existing_wrong_tag_is_rejected_without_mutation(self):
        responses = [{"object": {"type": "commit", "sha": "wrong-commit"}}] + self.tree_responses("wrong-tree")
        with patch.object(release, "request", side_effect=responses) as req, self.assertRaisesRegex(ValueError, "different plugin"):
            release.ensure_tag("https://api.github.com/repos/a/b", "v1.0.0", "new-commit", "expected-tree")
        self.assertTrue(all(c.args[0] == "GET" for c in req.call_args_list))

    def test_missing_tag_is_created_at_exact_commit(self):
        missing = urllib.error.HTTPError("https://api.github.com/test", 404, "Not found", {}, None)
        responses = [missing, {"object": {"type": "commit", "sha": "exact-commit"}}] + self.tree_responses()
        with patch.object(release, "request", side_effect=responses) as req:
            result = release.ensure_tag("https://api.github.com/repos/a/b", "v1.0.0", "exact-commit", "expected-tree")
            self.assertEqual(result, "exact-commit")
            self.assertEqual(json.loads(req.call_args_list[1].args[2]), {"ref": "refs/tags/v1.0.0", "sha": "exact-commit"})

    def test_annotated_tag_is_dereferenced(self):
        responses = [{"object": {"type": "tag", "sha": "annotation"}},
                     {"object": {"type": "commit", "sha": "exact-commit"}}] + self.tree_responses()
        with patch.object(release, "request", side_effect=responses):
            self.assertEqual(release.ensure_tag("https://api.github.com/repos/a/b", "v1.0.0", "head", "expected-tree"), "exact-commit")


if __name__ == "__main__":
    unittest.main()
