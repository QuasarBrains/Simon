# Changelog

Notable changes to Simon, starting with [PR #6](https://github.com/QuasarBrains/Simon/pull/6). Earlier history is not backfilled.

Earlier versions were experimental alpha work. The next planned release is 1.0.0:
a renewed foundation for building and customizing agents, as coding models become
capable of sustaining longer tasks and making the original agent shell practical.

Versions use `MAJOR.MINOR.PATCH`: major releases introduce breaking changes,
minor releases add compatible features, and patch releases fix bugs. Unreleased
entries describe work in progress; released entries include a version and date.

<!--
Maintaining this file:
- Add concise, reader-facing notes under the unreleased version as part of each relevant PR.
- Explain what changed and why it matters; link to the PR and detailed docs.
- Use Added, Changed, Deprecated, Removed, Fixed, or Security as needed.
- Put upgrade steps and compatibility changes under Migration notes.
- Omit empty sections and changes that do not need a reader-facing note.
- At release time, change "Unreleased" to the actual release date (YYYY-MM-DD).
  Add a fresh "## [Unreleased]" section above it for subsequent work, and name
  its target version once decided.
- Keep version.py, pyproject.toml, and the simple-agent entry in uv.lock aligned
  when changing the project version. A version bump does not publish a release.
- This initial entry is a writing scaffold. Expand the prompts below and remove
  them and the draft notice before publishing the release notes.
-->

## [0.2.1] - Codebase Cleanup & Modernization

> This change is in reference to [PR #6](https://github.com/QuasarBrains/Simon/pull/6).

### Added

- Optional built-in SQLite memory with independent embedding adapters and a shared memory service interface. See [memory setup](documentation/adding-memory.md).
- Agent-specific setup documentation for those using coding agents to interact with this repository
- Session stateful agentic workspaces called Workbenches for tracking context outside of the direct context window
- General SQLite engine for persistent storage in future feature addition

### Changed

- Renamed from "Simple Agent" to Simon to shift branding away from a single goal of "Simplicity" (although of course this remains primary)
- Python 3.12 setup and dependencies are managed with uv, `pyproject.toml`, and a committed lockfile. See [setup instructions](Readme.md#how-to-use-it).
- Tools are organized into capability libraries for filesystem, search, web retrieval, Python, and shell operations. See [tooling](documentation/tooling.md).
- General codebase structural cleanup into modular monolithic architecture
- LLM provider compatibility updates and refinements to the general adapter interface
- Deprecated legacy tools that no longer reliable for agentic workflows (keeping in codebase for historical purposes)
