# Changelog

## [Unreleased]

### Added

- AdvHD engine: new format handler for `.ws2` script files (Willplus/Will AdvHD engine) — supports dialogue, speaker names, and choice text extraction/insertion with encryption detection and pointer fixup
- AGSD engine: new format handler for `.spt` script files (NicotineSoft/AGSD engine)

### Changed

- Replace legacy `extract`/`insert` commands with the plugin-based system (formerly `newextract`/`newinsert`)
- Remove old `KiriKiriScript` V1 handler (superseded by `KiriKiriScriptV2`)

### Removed

- Legacy `extract` and `insert` commands that used hardcoded handler dictionary
- `src/toto/KiriKiriScript.py` — old KiriKiri V1 format handler

## [0.2.2] — 2026-03-29

### Fixed

- Anim: narrow `.strip()` in `insert_lines` to only strip `\n\r`, avoiding data loss
- Anim: preserve encryption key through extract/insert roundtrip
- DxLib: preserve whitespace and header bytes in identity roundtrip

### Changed

- Share CP932 IBM-extended byte fixup across all CP932-using format handlers (Mgos, DxLib, Anim)
- Restructure tests into per-filetype packages with nonfree roundtrip tests
- Tighten ruff and mypy configuration; fix lint and type-check issues
- Add pytest-xdist for parallel test execution; switch coverage collection to pytest-cov for xdist compatibility

## [0.2.1] — 2026-03-28

### Fixed

- Normalize codec names returned by `detect_encoding`

## [0.2.0] — 2026-03-28

### Added

- **KiriKiriScriptV2** format handler — binary-aware KiriKiri `.ks`/`.soc` support with chardet-based encoding detection and BOM handling
- **Mgos** (μ-GameOperationSystem) format handler — bytecode-walking `.o` script support with CP932 string extraction, `「」` and `（direction）` stripping, and IBM-extended byte preservation
- **Anim** format handler — XOR-cipher encrypted `.dat` file support, including `_define.dat` files without `【】` markers
- `--ignore-line-regex` option for `newextract`
- Preserve directory structure on extract/insert

### Fixed

- Fix pipx install by removing legacy `setup.py` and adding explicit package discovery

### Changed

- Modernize project structure to match tamago conventions

## [0.1.0] — 2025-04-15

Initial release. CLI with `extract`/`insert` commands, plugin-based format handler system, KiriKiriScript V1 handler, DxLib handler, and SilkyEngine MES handler.
