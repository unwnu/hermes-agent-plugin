#!/usr/bin/env python3
"""Create deterministic skill-only ZIP and SHA-256 checksum artifacts."""
import hashlib
import json
from pathlib import Path
import zipfile
from sync_upstream import ROOT, PLUGIN
from validate import validate


def package(root=ROOT):
    root = Path(root)
    validate(root)
    plugin = root / PLUGIN
    version = json.loads((plugin / "plugin.json").read_text())["version"]
    output = root / "dist"
    output.mkdir(exist_ok=True)
    archive = output / f"hermes-agent-{version}.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as target:
        for path in sorted(plugin.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo("hermes-agent/" + path.relative_to(plugin).as_posix(), (2026, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = (0o100755 if path.stat().st_mode & 0o111 else 0o100644) << 16
                target.writestr(info, path.read_bytes())
    checksum = output / f"hermes-agent-{version}.sha256"
    checksum.write_text(hashlib.sha256(archive.read_bytes()).hexdigest() + "  " + archive.name + "\n")
    print(archive)
    return archive, checksum


if __name__ == "__main__":
    package()
