# Changelog

## [Unreleased]

### Added

- `extract --output-format po` writes one gettext PO file per script instead of `.trans*.txt` batches. Each entry is keyed by `msgctxt`, so `insert` matches translations by key rather than by line position; the `msgid` is the same text as the txt line, so existing translation memories still match. `insert` reads `<script>.po` when present (an empty `msgstr` keeps the source text) and stops with an error if a script has both PO and txt translations.
- PO entries carry a `#. Speaker: <name>` comment, shown in OmegaT's Comments pane, for dialogue in the `advhd`, `ai6win`, `blackrainbow`, `dxlib` (unwrapped), `mgos` and `systemnnn` handlers.
- **AI6WIN**: New `ai6win` handler for AI6WIN `.mes` scripts (elf and Silky's; tested with 麻呂の患者はガテン系 1–3), as unpacked from `mes.arc` by tamago 1.7 or later. Extracts dialogue, speaker names (once per script, from the `〈name〉：` tag) and choices. Translations can be any length: the script's jumps, choice links and message table are updated to match. Because the engine's own wrapping doesn't suit English, translated dialogue is wrapped on insert (default width 60, counting the name tag) using the engine's line-break instruction. Untranslated text, and dialogue where only the speaker name changed, keeps its original form. Choices are never wrapped.

## [1.2.0] — 2026-11-20

### Added

- **System-NNN**: New `systemnnn` handler for System-NNN `.spt` scripts, as found loose in a game's `spt/` folder (変態露出調教妻 慶子, 監禁強襲 and other Cyc/CYCLET titles). Extracts dialogue, speaker names (once per script) and choices; labels and file names are left alone. Scripts are decrypted and re-encrypted automatically. Dialogue is unwrapped by default and rewrapped on insert (default width 48, since the engine doesn't wrap on its own), keeping the full-width hanging indent inside quotes; a warning is logged for messages longer than the window's 4 lines. Spaces in translated speaker names become full-width spaces so they can match `namecol.xtx`.
- **System-NNN**: New `systemnnn-xtx` handler for the `.xtx` lists in `spt/` and `nya/` (`namecol.xtx`, `charaname.xtx`, `selist.xtx` …). Each Japanese entry is extracted on its own. Spaces and commas in a translation are made full-width so the entry stays one item for the engine, and entries over the engine's 127-byte limit are rejected.

### Fixed

- **BLACKRAINBOW**: Each speaker display name is now extracted once per script instead of once per line of dialogue.
- **BLACKRAINBOW**, **DxLib**: `insert` ignores spaces and tabs around a translation, such as the leading space OmegaT puts before many segments, when deciding whether a message changed. Untranslated messages were being rewrapped at the translation width (too wide for Japanese text) and are now written back unchanged.

## [1.1.0] — 2026-11-20

### Added

- **BLACKRAINBOW**: New `blackrainbow` handler for ADVDX `.spt` scripts (催眠術２, 裏催眠術２DL, 黒虹本2, 催眠術FullVoice), as unpacked from `*_spt.dat` / `script.dat` by tamago. Extracts dialogue, speaker display names, choices and scene titles. Message text is decrypted and re-encrypted automatically using the key of each known game; for other games pass the key with the new `extract --key` option. Dialogue is unwrapped by default and rewrapped on insert (default width 55, the engine doesn't wrap on its own), keeping the full-width hanging indent inside quotes. A message that needs more than two lines is continued on extra pages that repeat the speaker name and keep the message's read-tracking ID.
- **DxLib**: `extract` now joins each message's lines into one line to translate (use `--no-unwrap` for the old one-string-at-a-time extraction), and `insert` rewraps the translation to `--width` (default 45 for DxLib). Messages can grow or shrink: line records are added or removed and every label, jump and menu offset is updated. A message that needs more than three lines is split across several clicks, with the speaker name repeated on each page. Dialogue continuation lines keep their full-width hanging indent, and messages left unchanged are written back exactly as they were. Speaker names and menu options are still extracted separately. Rewrapped scripts probably won't load saves made with the original scripts.

### Changed

- `insert --width` now defaults to each handler's own default width (60 unless the handler sets one) instead of always 60. The "handler does not support text wrapping" warning now only appears when `--width` is given explicitly.

### Fixed

- `insert` no longer writes the translation file's line break into the output for lines that aren't wrapped. Previously KiriKiri scripts with CRLF line endings gained a stray LF on each such line (`text\n\r\n`), and DxLib and AdvHD strings gained a trailing newline.
- **KiriKiri**: Lines that begin with an inline tag (e.g. `[ruby …]text`) are now extracted. Only lines made up entirely of tags are skipped.
- **KiriKiri**: Text after a mid-line tag (e.g. `[ruby]`, `[emb]`, `[r]`) is no longer silently dropped; only tags at the end of the line are treated as end-of-line macros.
- **KiriKiri**: Extracted dialogue (`[「]…`) and choice (`[select link=…]`) lines no longer include the trailing line break.
- **KiriKiri**: A byte-order mark is no longer extracted as part of the first line when `--codec` is given explicitly (e.g. `utf-16-le`).
- **KiriKiri**: Text at the end of a script is no longer lost when the file ends mid-group (e.g. after a `[r]` line or a trailing `「…」` line).
- **KiriKiri**: TJS code inside `[iscript]`…`[endscript]` (or `@iscript`…`@endscript`) blocks is no longer extracted as text.
- **KiriKiri**: Lines containing inline tags (e.g. `[☆]`, `[ruby …]`) are now wrapped to `--width` on insert; previously they were left unwrapped. Tags are never split, formatting tags count as zero width, symbol macros such as `[☆]` count as one character, and an explicit `[r]` starts a new line.
- **DxLib**: Scripts with labels no longer have stray control bytes at the start of their first extracted string. The pool's label offset table was being read as part of that string, and deleting the junk corrupted the labels.
- **DxLib**: The script header's body-size field is now updated when a translation changes the script's length. Previously it kept the original size, so archive tools such as `tamago med create` didn't recognize translated files as scripts and stored them unencrypted. This only applies to scripts extracted with this version or later.

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
