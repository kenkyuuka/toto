# toto

A visual novel script localization tool. toto extracts translatable text from various VN engine script formats, manages translations, and reinserts translated text back into script files.

## Supported engines

| Engine | Handler | File types |
|--------|---------|------------|
| KiriKiri | `kirikiri` | `.ks`, `.soc` |
| DxLib | `dxlib` | binary scripts |
| Anim | `anim` | `.dat` |
| mgos (μ-GameOperationSystem) | `mgos` | `.o` |
| AGSD (NicotineSoft) | `agsd` | `.spt` |
| AdvHD (Willplus) | `advhd` | `.ws2` |
| ADVDX (BLACKRAINBOW) | `blackrainbow` | `.spt` (unpacked from `*_spt.dat` / `script.dat` with tamago) |
| AI6WIN (elf, Silky's) | `ai6win` | `.mes` (unpacked from `mes.arc` with tamago ≥ 1.7) |

## Installation

```bash
pip install toto-script-tool
```

Or for development:

```bash
pip install hatch
hatch env create
```

## Usage

### Extract

Extract translatable text from script files:

```bash
toto extract --filetype=kirikiri path/to/scripts/
```

Options:
- `--filetype` (required) — engine format (see table above)
- `--outpath` — where to write translation files (default: `./project/source/`)
- `--workpath` — where to store intermediate files (default: `./working/`)
- `--codec` — force a specific text encoding
- `--ignore-line-regex` — regex pattern to skip matching lines (repeatable)
- `--unwrap` — remove inline line breaks from extracted text for re-wrapping on insertion (supported formats: `agsd`, `ai6win`, `blackrainbow`, `dxlib`; on by default for `ai6win`, `blackrainbow` and `dxlib`)
- `--key` — hex encryption key for formats that need one (`blackrainbow`: the message key; by default the known keys are tried)
- `--output-format` — `txt` (default): `<script>.transNNN.txt` files with one string per line, in batches of 10000; `po`: one gettext `<script>.po` file per script (see [PO output](#po-output))

#### PO output

With `--output-format po`, each string becomes a PO entry whose `msgctxt` is its placeholder key (so `insert` matches translations by key, not by position) and whose `msgid` is exactly the line the txt output would contain. Translation memories made with txt files therefore still match after switching formats.

In OmegaT, turn on the PO filter's "Skip PO header" option; otherwise the header shows up as two segments to translate. To switch an existing project to PO, re-extract with `--output-format po` and delete the old `.trans*.txt` files from both `source/` and `target/`: `insert` stops with an error if it finds both formats for one script.

### Insert

Reinsert translated text into script files:

```bash
toto insert --filetype=kirikiri path/to/translations/
```

Options:
- `--filetype` (required) — engine format
- `--outpath` — where to write patched scripts (default: `./patch/`)
- `--workpath` — intermediate file directory (default: `./working/`)
- `--width` — line width for text wrapping (default: the handler's own, usually 60; supported formats: `agsd`, `ai6win`, `blackrainbow`, `dxlib`, `kirikiri`)
- `--wrap` — wrapping mode (supported formats: `agsd`, `kirikiri`)
- `--codec` — force output encoding
- `--skip-identical` — skip files where all translations match the original

Translations are read from `<script>.po` when it exists and from `<script>.transNNN.txt` otherwise. In a PO file, an entry with an empty `msgstr`, or a string with no entry, keeps its source text; entries whose key the script doesn't have are skipped with a warning.

## Workflow

1. **Extract** translatable text from original scripts. This produces translation files (plain text with one line per string, or PO) and intermediate files with placeholder tokens.
2. **Translate** the extracted text files — by hand, with MT, or however you like.
3. **Insert** the translations back. toto substitutes translated text into the intermediate files to produce patched scripts ready for use.

## Related tools

[tamago](https://github.com/kenkyuuka/tamago) can create and extract archive files for VN engines supported by toto, completing the translation workflow: unpack archives with tamago, extract and translate scripts with toto, then repack with tamago.

## Adding format handlers

toto uses a plugin system based on Python entry points. To add support for a new engine format:

1. Create a handler class that extends `toto.filetypes.TranslatableFile.TranslatableFile`
2. Implement `get_paths()`, `extract_lines()`, and `insert_lines()`
3. Register it as an entry point under `toto.filetypes` in `pyproject.toml`

## Development

```bash
# Run tests
hatch run test

# Run a specific test
hatch run test -- -k "test_name"

# Lint and format
hatch run lint:style     # check
hatch run lint:fmt       # auto-format
hatch run lint:typing    # mypy
```

## License

MIT
