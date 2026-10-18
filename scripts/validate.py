#!/usr/bin/env python3
"""Offline checks for manifests, provenance, paths and byte-for-byte mirroring."""
import hashlib
import json
from pathlib import Path
import re
from sync_upstream import ROOT, PLUGIN, DEST, LOCK, fingerprint, git_blob_sha, safe_path


def validate(root=ROOT):
    root = Path(root)
    manifests = [json.loads((root / PLUGIN / p).read_text()) for p in
                 ("plugin.json", ".codex-plugin/plugin.json", ".claude-plugin/plugin.json")]
    versions = {m["version"] for m in manifests}
    assert len(versions) == 1 and re.fullmatch(r"\d+\.\d+\.\d+", next(iter(versions)))
    assert all(m["name"] == "hermes-agent" and m["license"] == "MIT" for m in manifests)
    assert manifests[0]["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifests[1]["skills"] == "./skills/"
    assert not any(k in m for m in manifests for k in ("hooks", "mcpServers", "apps"))
    for path in (".agents/plugins/marketplace.json", ".claude-plugin/marketplace.json"):
        marketplace = json.loads((root / path).read_text())
        assert marketplace["name"] == "hermes-agent-marketplace"
        assert len(marketplace["plugins"]) == 1
        entry = marketplace["plugins"][0]
        assert entry["name"] == "hermes-agent"
        source = entry["source"]
        if isinstance(source, dict):
            assert source["source"] == "local"
            assert entry["policy"] == {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}
            source = source["path"]
        assert source == "./plugins/hermes-agent"
    lock = json.loads((root / LOCK).read_text())
    assert lock["repository"] == "NousResearch/hermes-agent"
    assert lock["source_path"] == "skills/autonomous-ai-agents/hermes-agent"
    assert re.fullmatch(r"[0-9a-f]{40}", lock["commit"])
    assert len(lock["files"]) == len({f["path"] for f in lock["files"]})
    expected = {f["path"] for f in lock["files"]}
    actual = set()
    for path in (root / DEST).rglob("*"):
        assert not path.is_symlink(), f"Symlink: {path}"
        if path.is_file():
            actual.add(path.relative_to(root / DEST).as_posix())
    assert actual == expected, f"Vendored file mismatch: {actual ^ expected}"
    for record in lock["files"]:
        path = root / DEST / str(safe_path(record["path"]))
        data = path.read_bytes()
        assert len(data) == record["bytes"]
        assert hashlib.sha256(data).hexdigest() == record["sha256"], str(path)
        assert git_blob_sha(data) == record["git_blob_sha"], str(path)
        assert bool(path.stat().st_mode & 0o111) == (record["mode"] == "100755")
    assert fingerprint(lock["files"]) == lock["content_sha256"]
    license_data = (root / PLUGIN / "LICENSE").read_bytes()
    assert hashlib.sha256(license_data).hexdigest() == lock["license_sha256"]
    assert b"MIT License" in license_data and b"Nous Research" in license_data
    text = (root / DEST / "SKILL.md").read_text()
    assert text.startswith("---\n") and "name: hermes-agent" in text.split("---", 2)[1]
    for relative in re.findall(r"\]\(((?:references|templates)/[^)#]+)(?:#[^)]*)?\)", text):
        assert (root / DEST / relative).is_file(), relative
    print(f"Validated Hermes plugin {next(iter(versions))}: {len(expected)} untouched upstream files")


if __name__ == "__main__":
    validate()
