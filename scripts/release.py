#!/usr/bin/env python3
"""Publish or recover a release using only the job's ephemeral GitHub token."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from sync_upstream import ROOT, PLUGIN, LOCK, open_request


def request(method, url, body=None, content_type="application/json"):
    token = os.environ.get("GH_TOKEN") or os.environ["GITHUB_TOKEN"]
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"api.github.com", "uploads.github.com"} or parsed.port not in {None, 443}:
        raise ValueError("Unexpected release API destination")
    headers = {"Authorization": "Bearer " + token, "Accept": "application/vnd.github+json",
               "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "hermes-agent-plugin-release",
               "Content-Type": content_type}
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    with open_request(req, timeout=90) as response:
        return json.load(response)


def ensure_tag(base, tag, commit, expected_tree):
    """Pin a missing tag and verify existing tags before any release writes."""
    url = base + "/git/ref/tags/" + tag
    try:
        ref = request("GET", url)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        try:
            ref = request("POST", base + "/git/refs", json.dumps({"ref": "refs/tags/" + tag, "sha": commit}).encode())
        except urllib.error.HTTPError as collision:
            if collision.code != 422:
                raise
            # Another actor may have created the tag. Re-read; never force it.
            ref = request("GET", url)
    obj = ref["object"]
    for _ in range(5):
        if obj["type"] != "tag":
            break
        obj = request("GET", base + "/git/tags/" + obj["sha"])["object"]
    if obj["type"] != "commit":
        raise ValueError("Release tag does not resolve to a commit")
    target_commit = obj["sha"]
    tree_sha = target_commit
    for part in ("plugins", "hermes-agent"):
        tree = request("GET", base + "/git/trees/" + tree_sha)
        if tree.get("truncated"):
            raise ValueError("Truncated release tree")
        matches = [e for e in tree["tree"] if e["path"] == part and e["type"] == "tree"]
        if len(matches) != 1:
            raise ValueError("Release tag is missing the plugin directory")
        tree_sha = matches[0]["sha"]
    if tree_sha != expected_tree:
        raise ValueError("Release tag points to a different plugin; refusing to publish or overwrite")
    return target_commit


def main():
    repository = os.environ["GITHUB_REPOSITORY"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
        raise ValueError("Invalid repository")
    version = json.loads((ROOT / PLUGIN / "plugin.json").read_text())["version"]
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError("Invalid version")
    tag = "v" + version
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    lock = json.loads((ROOT / LOCK).read_text())
    base = "https://api.github.com/repos/" + repository
    expected_tree = subprocess.check_output(["git", "rev-parse", "HEAD:plugins/hermes-agent"], cwd=ROOT, text=True).strip()
    target_commit = ensure_tag(base, tag, commit, expected_tree)
    notes = (f"Community package of the official NousResearch Hermes Agent skill.\n\n"
             f"- Upstream commit: `{lock['commit']}`\n"
             f"- [Exact upstream skill]({lock['source_url']})\n"
             f"- Content fingerprint: `{lock['content_sha256']}`\n"
             f"- {len(lock['files'])} upstream files preserved verbatim\n\n"
             "The package version is independent of the upstream skill's version. "
             "The ZIP includes MIT license and per-file provenance. No Hermes runtime, credentials, MCP server, or hooks are installed.")
    try:
        release = request("GET", base + "/releases/tags/" + tag)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        # A previous attempt may have created an unpublished draft.
        drafts = request("GET", base + "/releases?per_page=100")
        release = next((r for r in drafts if r["tag_name"] == tag), None)
        if release is None:
            payload = {"tag_name": tag, "target_commitish": commit, "name": "Hermes Agent plugin " + tag,
                       "body": notes, "draft": True, "prerelease": False}
            release = request("POST", base + "/releases", json.dumps(payload).encode())
    assets = {a["name"]: a for a in release["assets"]}
    for suffix in ("zip", "sha256"):
        artifact = ROOT / "dist" / f"hermes-agent-{version}.{suffix}"
        data = artifact.read_bytes()
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        if artifact.name in assets:
            existing = assets[artifact.name]
            if existing.get("digest") != digest:
                raise ValueError("Existing release asset has different or unverifiable content; refusing overwrite")
            continue
        if not release["draft"]:
            raise ValueError("Published release is incomplete; manual review required")
        upload = release["upload_url"].split("{", 1)[0] + "?name=" + urllib.parse.quote(artifact.name)
        request("POST", upload, data, "application/zip" if suffix == "zip" else "text/plain")
    if release["draft"]:
        release = request("PATCH", base + "/releases/" + str(release["id"]), json.dumps({"draft": False, "make_latest": "true", "target_commitish": target_commit, "body": notes}).encode())
    print(release["html_url"])


if __name__ == "__main__":
    main()
