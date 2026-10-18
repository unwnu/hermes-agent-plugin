# Hermes Agent skill plugin

This plugin packages the [official Hermes Agent skill](https://github.com/NousResearch/hermes-agent/tree/main/skills/autonomous-ai-agents/hermes-agent) for Codex, ChatGPT, and Claude Code. A skill gives instructions to an assistant. This package keeps the original skill, reference files, and templates unchanged.

Nous Research publishes the skill. This package is not an official Nous Research release. It adds the guide to your assistant without installing the Hermes runtime or connecting your accounts.

## Install

A marketplace lists plugins that you can install. Run these commands in a terminal with Codex installed:

```sh
codex plugin marketplace add unwnu/hermes-agent-plugin --ref main
codex plugin add hermes-agent@hermes-agent-marketplace
```

For Claude Code, run these commands in a terminal with Claude Code installed:

```sh
claude plugin marketplace add unwnu/hermes-agent-plugin
claude plugin install hermes-agent@hermes-agent-marketplace
```

In Claude Code, use `/hermes-agent:hermes-agent` to call the skill. In a supported ChatGPT desktop client, open the Plugins Directory after you register the marketplace. Make sure that the plugin is installed and enabled.

These commands install the plugin in your local client. They do not install it into a cloud conversation. Cloud import depends on your client and workspace policy.

You can also download the plugin ZIP from [Releases](https://github.com/unwnu/hermes-agent-plugin/releases). Each release includes a SHA-256 checksum. A checksum detects changes to file contents.

## Get updates

The [GitHub workflow](.github/workflows/sync-upstream.yml) looks for changes at minute 17 of every hour. You can also start it from the Actions page. It reads the official skill folder and its license from one exact source revision.

When the skill changes, the workflow completes these steps:

1. Copy the changed files without editing them
2. Run the tests and make sure that all file hashes match
3. Increase the plugin patch version and publish a ZIP with its checksum

Changes outside the official skill folder do not create a release. The plugin has its own version number. It can receive an update even when the version inside the official skill stays the same.

To get the latest files in Codex, run this command:

```sh
codex plugin marketplace upgrade hermes-agent-marketplace
```

For Claude Code, use its marketplace and plugin update controls. To keep a fixed Codex version, replace `--ref main` with `--ref v1.0.0` during registration. A fixed version does not receive later changes.

GitHub can delay scheduled runs. It disables schedules in public repositories after 60 days without repository activity. If that happens, [enable the workflow again](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows).

## Maintain this package

Use Python 3.10 or later. The scripts need only the Python standard library. Run these commands from the root of this repository:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/validate.py
python3 scripts/package.py
python3 scripts/sync_upstream.py
```

The source record is `plugins/hermes-agent/upstream-lock.json`. It stores the exact source revision, file paths, modes, hashes, and license hash. Do not edit `plugins/hermes-agent/skills/hermes-agent/` directly. Send changes to the official repository instead.

The workflow uses a temporary GitHub token. Only the release job gets `contents: write`. It needs no personal access token, external secret, server, or paid runner. Pull requests run tests with read-only access.

The workflow never runs code from the downloaded skill. It stops for unsafe paths, unsupported file types, large downloads, incorrect hashes, or license changes. Before publication, it makes sure that the version tag points to the same plugin files.

The release job never replaces an uploaded file. If GitHub leaves an empty failed upload in a draft release, remove that file from the draft. Then run the workflow again.

## License and formats

The skill and its resources use the [original MIT license](plugins/hermes-agent/LICENSE), copyright 2025 Nous Research. The skill names Hermes Agent + Teknium as its authors. The packaging scripts use the [MIT license](LICENSE).

The package includes separate files for these plugin formats:

- [OpenAI plugins and marketplaces](https://developers.openai.com/plugins/build/plugins)
- [Agent Plugins](https://agent-plugins.org/schemas/1.0.0/plugin.schema.json)
- [Claude Code plugins and marketplaces](https://code.claude.com/docs/en/plugin-marketplaces)
