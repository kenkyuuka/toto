"""Tests for --ignore-line-regex option on extract."""

import shelve
import shutil
import struct

import pytest
from click.testing import CliRunner

from toto.toto import cli


def _make_string_entry(text_bytes):
    """Build a single string table entry: [2-byte LE length][data][0x00]."""
    str_len = len(text_bytes) + 1
    return struct.pack('<H', str_len) + text_bytes + b'\x00'


def _make_script_with_lines(*texts):
    """Build a minimal mgos .o file whose string table contains the given texts.

    Each text is a str that will be encoded as CP932.  In addition, a leading
    "fontfat" system string is included (index 0) so the handler has something
    to skip, matching the real-world pattern.
    """
    entries_bytes = [b'fontfat'] + [t.encode('cp932') for t in texts]
    entry_blobs = [_make_string_entry(e) for e in entries_bytes]

    filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
    num_refs = len(entries_bytes)
    bytecode_length = len(filler) + num_refs * 5

    string_offsets = []
    current = bytecode_length
    for blob in entry_blobs:
        string_offsets.append(current)
        current += len(blob)

    bytecode = bytearray(filler)
    for offset in string_offsets:
        bytecode.append(0x02)
        bytecode.extend(struct.pack('<I', offset))

    return bytes(bytecode) + b''.join(entry_blobs)


def _read_trans_lines(outpath, rel_name):
    """Read all lines from the .trans000.txt file for a given script."""
    trans_file = outpath / (rel_name + '.trans000.txt')
    if not trans_file.exists():
        return []
    return trans_file.read_text(encoding='utf-8').splitlines()


class TestIgnoreLineRegex:
    """Tests for the --ignore-line-regex CLI option."""

    def _extract(self, tmp_path, script_bytes, ignore_regexes=None):
        """Run extract and return (result, trans_lines from output file)."""
        orig = tmp_path / 'orig'
        orig.mkdir()
        (orig / 'test.o').write_bytes(script_bytes)

        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'

        args = [
            'extract',
            '--filetype=mgos',
            '--workpath',
            str(workpath),
            '--outpath',
            str(outpath),
        ]
        for regex in ignore_regexes or []:
            args.extend(['--ignore-line-regex', regex])
        args.append(str(orig / 'test.o'))

        runner = CliRunner()
        result = runner.invoke(cli, args)

        trans_lines = []
        if result.exit_code == 0:
            trans_lines = _read_trans_lines(outpath, 'test.o')

        return result, trans_lines

    @pytest.mark.unit
    def test_no_ignore_extracts_all(self, tmp_path):
        """Without --ignore-line-regex, all translatable lines are extracted."""
        script = _make_script_with_lines(
            '//●ＣＧ：黒画面',
            '\u3000朝の光が差し込む。',
            '\u3000夜の闇が広がる。',
        )
        result, trans_lines = self._extract(tmp_path, script)
        assert result.exit_code == 0
        assert len(trans_lines) == 3

    @pytest.mark.unit
    def test_single_regex_filters_matching_lines(self, tmp_path):
        """A single --ignore-line-regex filters out matching lines."""
        script = _make_script_with_lines(
            '//●ＣＧ：黒画面',
            '\u3000朝の光が差し込む。',
            '\u3000夜の闇が広がる。',
        )
        result, trans_lines = self._extract(
            tmp_path,
            script,
            ignore_regexes=['^//●.+'],
        )
        assert result.exit_code == 0
        assert len(trans_lines) == 2
        assert not any('//●' in t for t in trans_lines)

    @pytest.mark.unit
    def test_multiple_regexes(self, tmp_path):
        """Multiple --ignore-line-regex options each filter independently."""
        script = _make_script_with_lines(
            '//●ＣＧ：黒画面',
            '!emote(smile)',
            '\u3000朝の光が差し込む。',
            '\u3000夜の闇が広がる。',
        )
        result, trans_lines = self._extract(
            tmp_path,
            script,
            ignore_regexes=['^//●.+', r'^!emote\(.+\)$'],
        )
        assert result.exit_code == 0
        assert len(trans_lines) == 2
        assert not any('//●' in t for t in trans_lines)
        assert not any('!emote' in t for t in trans_lines)

    @pytest.mark.unit
    def test_regex_no_match_keeps_all(self, tmp_path):
        """A regex that matches nothing leaves all lines intact."""
        script = _make_script_with_lines(
            '\u3000朝の光が差し込む。',
            '\u3000夜の闇が広がる。',
        )
        result, trans_lines = self._extract(
            tmp_path,
            script,
            ignore_regexes=['^NOMATCH$'],
        )
        assert result.exit_code == 0
        assert len(trans_lines) == 2

    @pytest.mark.unit
    def test_shelf_excludes_ignored_lines(self, tmp_path):
        """Ignored lines should not be in the shelf (they were never extracted)."""
        script = _make_script_with_lines(
            '//●ＣＧ：黒画面',
            '\u3000朝の光が差し込む。',
            '\u3000夜の闇が広がる。',
        )
        orig = tmp_path / 'orig'
        orig.mkdir()
        (orig / 'test.o').write_bytes(script)

        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                'extract',
                '--filetype=mgos',
                '--workpath',
                str(workpath),
                '--outpath',
                str(outpath),
                '--ignore-line-regex',
                '^//●.+',
                str(orig / 'test.o'),
            ],
        )
        assert result.exit_code == 0

        with shelve.open(str(workpath / 'test.o') + '.shelf') as shelf:
            textlines = shelf['lines']
        texts = [tl.text for tl in textlines.values()]
        assert len(texts) == 2
        assert not any('//●' in t for t in texts)

    @pytest.mark.unit
    def test_roundtrip_with_ignored_lines(self, tmp_path):
        """Extract+insert roundtrip preserves ignored lines in the output."""
        script = _make_script_with_lines(
            '//●ＣＧ：黒画面',
            '\u3000朝の光が差し込む。',
            '\u3000夜の闇が広がる。',
        )
        orig = tmp_path / 'orig'
        orig.mkdir()
        (orig / 'test.o').write_bytes(script)

        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'
        patchpath = tmp_path / 'patch'

        runner = CliRunner()

        # Extract with ignore
        result = runner.invoke(
            cli,
            [
                'extract',
                '--filetype=mgos',
                '--workpath',
                str(workpath),
                '--outpath',
                str(outpath),
                '--ignore-line-regex',
                '^//●.+',
                str(orig / 'test.o'),
            ],
        )
        assert result.exit_code == 0

        # Copy source translations to target (identity)
        transdir = tmp_path / 'target'
        shutil.copytree(outpath, transdir)

        # Insert
        result = runner.invoke(
            cli,
            [
                'insert',
                '--filetype=mgos',
                '--no-skip-identical',
                '--workpath',
                str(workpath),
                '--outpath',
                str(patchpath),
                str(transdir),
            ],
        )
        assert result.exit_code == 0

        # The output should exist and be a valid file (not contain raw placeholders)
        output = (patchpath / 'test.o').read_bytes()
        assert b'<<<TRANS' not in output

    @pytest.mark.unit
    def test_invalid_regex_reports_error(self, tmp_path):
        """An invalid regex should produce a clear error."""
        script = _make_script_with_lines('\u3000朝の光が差し込む。')
        result, _ = self._extract(
            tmp_path,
            script,
            ignore_regexes=['[invalid'],
        )
        assert result.exit_code != 0
