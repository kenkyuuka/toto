# Changelog

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
