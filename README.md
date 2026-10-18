# Hermes Agent skill plugin

The [official NousResearch Hermes Agent skill](https://github.com/NousResearch/hermes-agent/tree/main/skills/autonomous-ai-agents/hermes-agent), preserved **byte-for-byte**, packaged for Codex / ChatGPT and Claude Code.

**Community distribution by [unwnu](https://github.com/unwnu).** The skill is official upstream content; this plugin wrapper and marketplace are independent, not an official Nous Research release or endorsement.

## What is included

- The original `hermes-agent` skill, its references, and its templates
- Portable Agent Plugins, Codex, and Claude Code manifests
- Codex and Claude Code marketplace catalogs
- The original MIT license and a per-file integrity/provenance lock

This is a knowledge/operating-guide plugin. Installing it does **not** install the Hermes runtime, start an agent, configure an MCP server, add hooks, or grant access to accounts. Commands in the official guide still require the tools, environment, and permissions appropriate to the requested task.

## Install with Codex

```sh
codex plugin marketplace add unwnu/hermes-agent-plugin --ref main
codex plugin add hermes-agent@hermes-agent-marketplace
```

On supported ChatGPT desktop clients, the marketplace is also available through the Plugins Directory after registration. Restart or refresh the client if needed, then verify the plugin is installed and enabled in the intended account/environment. A GitHub repository by itself does not install a plugin into a cloud conversation.

Refresh the tracked marketplace:

```sh
codex plugin marketplace upgrade hermes-agent-marketplace
```

For a fixed version, register the repository with `--ref v1.0.0` instead of `--ref main`. A pinned tag deliberately does not track subsequent releases.

## Install with Claude Code

```sh
claude plugin marketplace add unwnu/hermes-agent-plugin
claude plugin install hermes-agent@hermes-agent-marketplace
```

The skill is available as `/hermes-agent:hermes-agent`. Use Claude Code's marketplace/plugin update controls to fetch new releases.

## Download a portable package

[GitHub Releases](https://github.com/unwnu/hermes-agent-plugin/releases) contains `hermes-agent-VERSION.zip` and its SHA-256 checksum. The ZIP has one plugin root and includes all three supported manifests. Import/upload support depends on the client and workspace policy; publication here does not enroll the plugin in OpenAI's or Anthropic's public directories.

## Automatic updates and releases

The [sync workflow](.github/workflows/sync-upstream.yml) checks upstream at minute 17 of every hour and can be run manually from Actions. GitHub may delay scheduled runs and may disable schedules in inactive public repositories after 60 days; re-enable the workflow if that happens.

1. Resolve upstream `main` once to an immutable commit
2. Read only `skills/autonomous-ai-agents/hermes-agent` and the root license
3. Verify every downloaded file against its upstream Git blob SHA
4. Compare paths, modes, and content hashes with the committed provenance lock
5. If skill content changed, mirror it verbatim and increment the plugin patch version
6. Run tests and validation, commit the update, then publish a versioned ZIP and checksum

Unrelated upstream commits do not create a new version or rewrite the lock. The plugin package version is independent of upstream's declared skill version, so content changes still receive a release even if upstream does not bump its metadata. Initial publication creates `v1.0.0`; interrupted draft publication can be resumed without replacing existing assets.

The workflow uses GitHub's built-in, job-scoped token with `contents: write` only in the publish job. No PAT, external secret, server, or paid runner is required. Pull requests run validation with read-only permissions. The upstream source is never executed by CI. Symlinks, submodules, unsafe paths, oversized content, integrity failures, and license changes stop publication for review.

Automatic GitHub releases and automatic client installation are separate: clients must refresh their marketplace or enable their own supported update setting.

## Validate or sync locally

Python 3.10+ and the standard library are sufficient:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate.py
python3 scripts/package.py
python3 scripts/sync_upstream.py
```

`plugins/hermes-agent/upstream-lock.json` records the exact upstream commit, path, SHA-256 fingerprint, license hash, and each file's Git blob hash. Do not edit the vendored skill: make wrapper changes outside `skills/hermes-agent/`, or contribute corrections upstream.

## Format references

- [OpenAI plugin packaging and marketplaces](https://developers.openai.com/plugins/build/plugins)
- [Agent Plugins manifest schema](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json)
- [Claude Code marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
- [Claude Code plugin manifest reference](https://code.claude.com/docs/en/plugins-reference)

## License and attribution

The original skill and accompanying resources are MIT-licensed, copyright © 2025 Nous Research; see [the preserved upstream license](plugins/hermes-agent/LICENSE). The upstream skill identifies its authors as Hermes Agent + Teknium. Packaging and synchronization code are MIT-licensed; see [LICENSE](LICENSE).
