#!/usr/bin/env python3
"""Mirror only the official Hermes skill. Never execute downloaded content."""
from __future__ import annotations

import argparse
import concurrent.futures
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import http.client
import json
import os
from pathlib import Path, PurePosixPath
import random
import re
import shutil
import tempfile
import time
import urllib.parse
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "NousResearch/hermes-agent"
REF = "main"
SOURCE_PATH = "skills/autonomous-ai-agents/hermes-agent"
PLUGIN = Path("plugins/hermes-agent")
DEST = PLUGIN / "skills/hermes-agent"
LOCK = PLUGIN / "upstream-lock.json"
MAX_FILE = 2 * 1024 * 1024
MAX_TOTAL = 20 * 1024 * 1024
MAX_FILES = 500
REQUEST_BUDGET = 75
SYNC_BUDGET = 300
MAX_ATTEMPTS = 4
SHA = re.compile(r"^[0-9a-f]{40}$")
VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


class SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    """Reject redirects before urllib can forward an Authorization header."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        if (new.scheme, new.hostname, new.port) != (old.scheme, old.hostname, old.port):
            raise ValueError("Cross-origin redirect rejected")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_request(req, timeout):
    return urllib.request.build_opener(SameOriginRedirect()).open(req, timeout=timeout)


def read_json(path):
    return json.loads(Path(path).read_text())


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def retry_delay(error, attempt):
    """Use a server delay when supplied, otherwise exponential backoff with jitter."""
    retry_after = (error.headers or {}).get("Retry-After") if isinstance(error, urllib.error.HTTPError) else None
    if retry_after:
        try:
            return max(0, int(retry_after))
        except ValueError:
            try:
                date = parsedate_to_datetime(retry_after)
                return max(0, (date - datetime.now(timezone.utc)).total_seconds())
            except (TypeError, ValueError, OverflowError):
                pass
    return min(2 ** attempt, 8) * random.uniform(0.8, 1.2)


def request(url, limit=MAX_FILE, deadline=None):
    headers = {"User-Agent": "hermes-agent-plugin-sync", "Accept": "application/vnd.github+json"}
    # Ephemeral Actions token is used only at the fixed GitHub API origin.
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token and url.startswith("https://api.github.com/"):
        headers["Authorization"] = "Bearer " + token
    end = min(time.monotonic() + REQUEST_BUDGET, deadline if deadline is not None else float("inf"))
    for attempt in range(MAX_ATTEMPTS):
        remaining = end - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Upstream download time budget exhausted")
        req = urllib.request.Request(url, headers=headers)
        try:
            with open_request(req, timeout=min(45, remaining)) as response:
                if urllib.parse.urlsplit(response.url).hostname not in {"api.github.com", "raw.githubusercontent.com"}:
                    raise ValueError("Unexpected download destination")
                # HTTPResponse.read1 returns after one socket read; check the
                # shared deadline between chunks even for a trickling server.
                read_chunk = getattr(response, "read1", response.read)
                data = bytearray()
                while len(data) <= limit:
                    if time.monotonic() >= end:
                        raise TimeoutError("Upstream download time budget exhausted")
                    chunk = read_chunk(min(65536, limit + 1 - len(data)))
                    if not chunk:
                        break
                    data.extend(chunk)
            if len(data) > limit:
                raise ValueError("Upstream response exceeds size limit")
            return bytes(data)
        except urllib.error.HTTPError as error:
            rate_limited = error.code == 403 and (error.headers or {}).get("Retry-After") is not None
            if not rate_limited and error.code not in {408, 429} and not 500 <= error.code <= 599:
                raise
            failure = error
        except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.IncompleteRead) as error:
            failure = error
        if attempt == MAX_ATTEMPTS - 1:
            raise failure
        delay = retry_delay(failure, attempt)
        if delay >= end - time.monotonic():
            raise failure
        time.sleep(delay)


def api(path, deadline=None):
    return json.loads(request("https://api.github.com/repos/" + REPOSITORY + "/" + path, deadline=deadline))


def safe_path(name):
    path = PurePosixPath(name)
    if (not name or path.is_absolute() or str(path) != name or "\\" in name
            or any(p in {".", "..", ".git"} for p in path.parts)
            or any(ord(c) < 32 for c in name)):
        raise ValueError("Unsafe upstream path: " + repr(name))
    return path


def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fingerprint(files):
    # Paths, modes and bytes all affect the release identity.
    canonical = [{k: f[k] for k in ("path", "mode", "sha256")} for f in sorted(files, key=lambda f: f["path"])]
    return hashlib.sha256(json.dumps(canonical, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def next_version(version):
    match = VERSION.fullmatch(version)
    if not match:
        raise ValueError("Expected a three-part package version")
    major, minor, patch = map(int, match.groups())
    return f"{major}.{minor}.{patch + 1}"


def collect_entries(commit, deadline=None):
    tree_sha = commit
    for part in SOURCE_PATH.split("/"):
        tree = api("git/trees/" + tree_sha, deadline=deadline)
        if tree.get("truncated"):
            raise ValueError("Truncated upstream tree")
        matches = [e for e in tree["tree"] if e["path"] == part and e["type"] == "tree"]
        if len(matches) != 1:
            raise ValueError("Official skill directory disappeared or changed type")
        tree_sha = matches[0]["sha"]
    tree = api("git/trees/" + tree_sha + "?recursive=1", deadline=deadline)
    if tree.get("truncated"):
        raise ValueError("Truncated upstream tree")
    entries = [e for e in tree["tree"] if e["type"] != "tree"]
    if not 1 <= len(entries) <= MAX_FILES:
        raise ValueError("Unexpected upstream file count")
    names = set()
    for entry in entries:
        safe_path(entry["path"])
        if entry["path"] in names or entry["type"] != "blob" or entry["mode"] not in {"100644", "100755"}:
            raise ValueError("Duplicate path or unsupported upstream file type")
        names.add(entry["path"])
        if not SHA.fullmatch(entry["sha"]):
            raise ValueError("Invalid upstream blob SHA")
    if "SKILL.md" not in names:
        raise ValueError("Upstream SKILL.md disappeared")
    return sorted(entries, key=lambda e: e["path"])


def fetch_snapshot(commit, deadline=None):
    if not SHA.fullmatch(commit):
        raise ValueError("An immutable 40-character upstream commit is required")
    entries = collect_entries(commit, deadline=deadline)
    def download(entry):
        relative = entry["path"]
        url = "https://raw.githubusercontent.com/" + REPOSITORY + "/" + commit + "/" + SOURCE_PATH + "/" + urllib.parse.quote(relative)
        data = request(url, deadline=deadline)
        if git_blob_sha(data) != entry["sha"]:
            raise ValueError("Upstream blob integrity mismatch: " + relative)
        return entry, data
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        downloaded = list(pool.map(download, entries))
    if sum(len(data) for _, data in downloaded) > MAX_TOTAL:
        raise ValueError("Upstream skill exceeds total size limit")
    license_data = request("https://raw.githubusercontent.com/" + REPOSITORY + "/" + commit + "/LICENSE", deadline=deadline)
    records = [{"path": e["path"], "mode": e["mode"], "git_blob_sha": e["sha"],
                "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)} for e, data in downloaded]
    lock = {"repository": REPOSITORY, "ref": REF, "commit": commit, "source_path": SOURCE_PATH,
            "source_url": "https://github.com/" + REPOSITORY + "/tree/" + commit + "/" + SOURCE_PATH,
            "content_sha256": fingerprint(records), "license_sha256": hashlib.sha256(license_data).hexdigest(),
            "files": records}
    return downloaded, license_data, lock


def apply_snapshot(root, downloaded, license_data, lock, initialize=False):
    root = Path(root)
    previous = read_json(root / LOCK) if (root / LOCK).exists() else None
    version = read_json(root / PLUGIN / "plugin.json")["version"]
    if previous:
        if previous["license_sha256"] != lock["license_sha256"]:
            raise ValueError("Upstream license changed; manual review is required")
        if previous["content_sha256"] == lock["content_sha256"]:
            return {"changed": False, "version": version, "commit": previous["commit"]}
        version = next_version(version)
    elif not initialize:
        raise ValueError("Missing provenance; use --initialize for the first import")
    # Stage all files before replacing the existing vendored subtree.
    with tempfile.TemporaryDirectory(prefix="hermes-sync-", dir=root) as temporary:
        staged = Path(temporary) / "skill"
        staged.mkdir()
        for entry, data in downloaded:
            target = staged / str(safe_path(entry["path"]))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if entry["mode"] == "100755" else 0o644)
        skill = (staged / "SKILL.md").read_text()
        if not skill.startswith("---\n") or "name: hermes-agent" not in skill.split("---", 2)[1]:
            raise ValueError("Unexpected upstream skill identity or frontmatter")
        if (root / DEST).exists():
            shutil.rmtree(root / DEST)
        shutil.move(str(staged), root / DEST)
    (root / PLUGIN / "LICENSE").write_bytes(license_data)
    write_json(root / LOCK, lock)
    for manifest in ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json"):
        path = root / PLUGIN / manifest
        value = read_json(path)
        value["version"] = version
        write_json(path, value)
    return {"changed": True, "version": version, "commit": lock["commit"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", help="Immutable upstream revision; defaults to main resolved once")
    parser.add_argument("--initialize", action="store_true")
    parser.add_argument("--output-metadata", type=Path)
    args = parser.parse_args()
    deadline = time.monotonic() + SYNC_BUDGET
    commit = args.commit or api("commits/" + REF, deadline=deadline)["sha"]
    downloaded, license_data, lock = fetch_snapshot(commit, deadline=deadline)
    result = apply_snapshot(ROOT, downloaded, license_data, lock, args.initialize)
    if args.output_metadata:
        write_json(args.output_metadata, result)
    print(json.dumps(result))


if __name__ == "__main__":
    main()
