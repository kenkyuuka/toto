# Changelog

## [Unreleased]

### Fixed

- **KiriKiri**: Lines that begin with an inline tag (e.g. `[ruby …]text`) are now extracted. Only lines made up entirely of tags are skipped.
- **KiriKiri**: Text after a mid-line tag (e.g. `[ruby]`, `[emb]`, `[r]`) is no longer silently dropped; only tags at the end of the line are treated as end-of-line macros.
- **KiriKiri**: Extracted dialogue (`[「]…`) and choice (`[select link=…]`) lines no longer include the trailing line break.
- **KiriKiri**: A byte-order mark is no longer extracted as part of the first line when `--codec` is given explicitly (e.g. `utf-16-le`).

## [1.0.0] — 2026-04-17

Initial public release. CLI tool for extracting and reinserting translatable text in visual novel scripts.

### Supported engines

- **KiriKiri**
- **DxLib**
- **Anim**
- **mgos** (μ-GameOperationSystem)
- **AGSD** (NicotineSoft)
- **AdvHD** (Willplus)

### Features

- Plugin-based format handler system via Python entry points
- `extract` command with custom regex to ignore certain lines, and ability to unwrap text on extraction
- `insert` command with support for automatic wrapping of text
