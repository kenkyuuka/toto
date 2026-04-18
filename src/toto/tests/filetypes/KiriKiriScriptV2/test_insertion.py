from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.KiriKiriScriptV2 import KiriKiriScript
from toto.util import TextLine

TEST_DATA = Path(__file__).parent / "samples"


def extract(filename, **kwargs):
    """Read a .ks file and call extract_lines with default grouping enabled."""
    data = (TEST_DATA / filename).read_bytes()
    return KiriKiriScript.extract_lines(BytesIO(data), **kwargs)


def extract_and_insert_identity(filename, **kwargs):
    """Extract then insert with identity translation (original text back)."""
    inter, textlines, meta = extract(filename, **kwargs)
    trans_dict = {tl.key: tl for tl in textlines}
    output = KiriKiriScript.insert_lines(inter, trans_dict, codec=meta['codec'], bom=meta.get('bom', b''))
    return output.read().decode(meta['codec'], errors='backslashreplace')


def extract_and_insert_translated(filename, translations, width=None, wrap=None, **kwargs):
    """Extract then insert with provided translations.

    translations: list of (text, eol) tuples, one per TextLine in extraction order.
    """
    inter, textlines, meta = extract(filename, **kwargs)
    assert len(translations) == len(textlines), f"Expected {len(textlines)} translations, got {len(translations)}"
    trans_dict = {}
    for tl, (text, eol) in zip(textlines, translations, strict=True):
        trans_dict[tl.key] = TextLine(tl.key, text, eol)
    output = KiriKiriScript.insert_lines(inter, trans_dict, width=width, wrap=wrap, codec=meta['codec'])
    return output.read().decode(meta['codec'], errors='backslashreplace')


# ---------------------------------------------------------------------------
# basic_grouping.ks — single line, two-line group, three-line group
# ---------------------------------------------------------------------------


class TestSingleLine:
    def test_extract(self):
        _, textlines, _ = extract('basic_grouping.ks', codec='ascii')
        tl = textlines[0]
        assert tl.text == 'Single line text.'
        assert tl.eol == '[p][cm]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('basic_grouping.ks', codec='ascii')
        lines = output.splitlines(keepends=True)
        assert lines[2] == 'Single line text.[p][cm]\r\n'


class TestTwoLineGroup:
    def test_extract(self):
        _, textlines, _ = extract('basic_grouping.ks', codec='ascii')
        tl = textlines[1]
        assert tl.text == 'Two linegroup here.'
        assert tl.eol == '[p][cm]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('basic_grouping.ks', codec='ascii')
        lines = output.splitlines(keepends=True)
        # Two input lines collapsed to one output line
        assert lines[4] == 'Two linegroup here.[p][cm]\r\n'
        # Total output has fewer lines than the 11-line input (10 lines + trailing)
        assert len(lines) == 7


class TestThreeLineGroup:
    def test_extract(self):
        _, textlines, _ = extract('basic_grouping.ks', codec='ascii')
        tl = textlines[2]
        assert tl.text == 'Three linegroup withfinal part.'
        assert tl.eol == '[p][cm]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('basic_grouping.ks', codec='ascii')
        lines = output.splitlines(keepends=True)
        # Three input lines collapsed to one output line
        assert lines[6] == 'Three linegroup withfinal part.[p][cm]\r\n'


# ---------------------------------------------------------------------------
# group_edges.ks — command interruption, [l][r] closing
# ---------------------------------------------------------------------------


class TestGroupInterrupted:
    def test_extract(self):
        _, textlines, _ = extract('group_edges.ks', codec='ascii')
        # First text line is flushed by the command with eol='[r]'
        assert textlines[0].text == 'Started group'
        assert textlines[0].eol == '[r]'
        # Second text line is standalone after the command
        assert textlines[1].text == 'After command.'
        assert textlines[1].eol == '[p]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('group_edges.ks', codec='ascii')
        lines = output.splitlines(keepends=True)
        # Flushed line, command, standalone — all preserved
        assert lines[2] == 'Started group[r]\r\n'
        assert lines[3] == '[wait time=500]\r\n'
        assert lines[4] == 'After command.[p]\r\n'


class TestLrClosesGroup:
    def test_extract(self):
        _, textlines, _ = extract('group_edges.ks', codec='ascii')
        # First part[r] + Second part.[l][r] grouped together
        assert textlines[2].text == 'First partSecond part.'
        assert textlines[2].eol == '[l][r]'
        # Third part is standalone
        assert textlines[3].text == 'Third part.'
        assert textlines[3].eol == '[p][cm]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('group_edges.ks', codec='ascii')
        lines = output.splitlines(keepends=True)
        # Two input lines collapsed to one output line
        assert lines[6] == 'First partSecond part.[l][r]\r\n'
        assert lines[7] == 'Third part.[p][cm]\r\n'
        assert len(lines) == 8


# ---------------------------------------------------------------------------
# leading_whitespace.ks
# ---------------------------------------------------------------------------


class TestLeadingWhitespace:
    def test_preserved(self):
        """Single indented line keeps its leading whitespace in the intermediate."""
        inter, textlines, meta = extract('leading_whitespace.ks', codec='ascii')
        inter_text = inter.read().decode('ascii')
        # The intermediate should have the indent before the key
        assert '    <<<TRANS:2>>>' in inter_text
        assert textlines[0].text == 'Indented single.'
        assert textlines[0].eol == '[p][cm]'

    def test_grouped(self):
        """First line's indent is used as group_leading in output."""
        inter, textlines, _ = extract('leading_whitespace.ks', codec='ascii')
        inter_text = inter.read().decode('ascii')
        assert '    <<<TRANS:3>>>' in inter_text
        assert textlines[1].text == 'Indent AIndent B.'
        assert textlines[1].eol == '[p][cm]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('leading_whitespace.ks', codec='ascii')
        lines = output.splitlines(keepends=True)
        assert lines[2] == '    Indented single.[p][cm]\r\n'
        assert lines[3] == '    Indent AIndent B.[p][cm]\r\n'


# ---------------------------------------------------------------------------
# realistic_japanese.ks — Shift-JIS, labels, comments, calls, grouping
# ---------------------------------------------------------------------------


class TestJapanese:
    def test_grouping_extract(self):
        _, textlines, meta = extract('realistic_japanese.ks')
        assert meta['codec'] == 'cp932'
        assert len(textlines) == 4

        # Two-line [r] group
        assert textlines[0].text == 'バスが止まっている。雨が降り続いている。'
        assert textlines[0].eol == '[p][cm]'

        # Single line
        assert textlines[1].text == '一人で待っていた。'
        assert textlines[1].eol == '[p][cm]'

        # [l][r] closes group (single line)
        assert textlines[2].text == '今日はいい天気だ。'
        assert textlines[2].eol == '[l][r]'

        # Remaining [r] group ending with [p][cm]
        assert textlines[3].text == '来年の夏は暑かった。エルニーニョのせいだ。'
        assert textlines[3].eol == '[p][cm]'

    def test_identity_insert(self):
        output = extract_and_insert_identity('realistic_japanese.ks')
        lines = output.splitlines(keepends=True)

        # Non-text lines preserved exactly
        assert lines[0] == '*scene01|バスが止まっている。\r\n'
        assert lines[1] == '[cm]\r\n'
        assert lines[2] == ';「(主人公)」\r\n'
        assert lines[3] == '\t[call storage="sub_rou.ks" target=*sub_ore_k]\r\n'

        # Grouped text collapsed
        assert lines[4] == 'バスが止まっている。雨が降り続いている。[p][cm]\r\n'

        # Second scene
        assert lines[5] == '*scene02|一人で待っていた。\r\n'
        assert lines[9] == '一人で待っていた。[p][cm]\r\n'

        # [l][r] line + following group
        assert lines[12] == '今日はいい天気だ。[l][r]\r\n'
        assert lines[13] == '来年の夏は暑かった。エルニーニョのせいだ。[p][cm]\r\n'

        assert len(lines) == 14

    def test_translated_insert(self):
        translations = [
            ('The bus has stopped. The rain keeps falling.', '[p][cm]'),
            ('I was waiting alone.', '[p][cm]'),
            ("It's nice weather today.", '[l][r]'),
            ("Last summer was hot. It's El Nino's fault.", '[p][cm]'),
        ]
        output = extract_and_insert_translated('realistic_japanese.ks', translations)
        lines = output.splitlines(keepends=True)

        # Labels and commands preserved
        assert lines[0] == '*scene01|バスが止まっている。\r\n'
        assert lines[3] == '\t[call storage="sub_rou.ks" target=*sub_ore_k]\r\n'

        # Translated text with correct eol
        assert lines[4] == 'The bus has stopped. The rain keeps falling.[p][cm]\r\n'
        assert lines[9] == 'I was waiting alone.[p][cm]\r\n'
        assert lines[12] == "It's nice weather today.[l][r]\r\n"
        assert lines[13] == "Last summer was hot. It's El Nino's fault.[p][cm]\r\n"


# ---------------------------------------------------------------------------
# Translation insertion with basic_grouping.ks
# ---------------------------------------------------------------------------


class TestInsertWithTranslations:
    def test_insert_with_translations(self):
        translations = [
            ('A single translated line.', '[p][cm]'),
            ('First half, second half.', '[p][cm]'),
            ('Part one, part two, part three.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('basic_grouping.ks', translations, codec='ascii')
        lines = output.splitlines(keepends=True)

        assert lines[2] == 'A single translated line.[p][cm]\r\n'
        assert lines[4] == 'First half, second half.[p][cm]\r\n'
        assert lines[6] == 'Part one, part two, part three.[p][cm]\r\n'
        assert len(lines) == 7


# ---------------------------------------------------------------------------
# single_line_dialogue.ks — dialogue line closed by [en], not by [r]/[p]/etc.
# ---------------------------------------------------------------------------


class TestSingleLineDialogue:
    def test_identity_insert(self):
        """A 「…」 line followed by [en] should roundtrip without changes."""
        output = extract_and_insert_identity('single_line_dialogue.ks')
        lines = output.splitlines(keepends=True)
        assert lines[1] == '「テスト」\r\n'


# ---------------------------------------------------------------------------
# utf16_bom.ks — UTF-16 LE file with BOM
# ---------------------------------------------------------------------------


class TestUtf16Bom:
    def test_no_bom_per_line(self):
        """Intermediate file should not have a BOM before every line."""
        inter, textlines, meta = extract('utf16_bom.ks')
        raw = inter.read()
        # Only one BOM allowed (none, since intermediate uses endian-specific codec)
        assert raw.count(b'\xff\xfe') == 0

    def test_output_has_single_bom(self):
        """Identity roundtrip output should have exactly one BOM at the start."""
        inter, textlines, meta = extract('utf16_bom.ks')
        trans_dict = {tl.key: tl for tl in textlines}
        output = KiriKiriScript.insert_lines(inter, trans_dict, codec=meta['codec'], bom=meta.get('bom', b''))
        raw = output.read()
        assert raw[:2] == b'\xff\xfe'
        assert raw[2:].count(b'\xff\xfe') == 0

    def test_identity_roundtrip(self):
        """Content should survive an identity roundtrip."""
        output = extract_and_insert_identity('utf16_bom.ks')
        # Strip the decoded BOM character; its byte-level correctness
        # is verified by test_output_has_single_bom.
        output = output.lstrip('\ufeff')
        lines = output.splitlines(keepends=True)
        assert lines[0] == '[cm]\r\n'
        assert lines[1] == 'Single line.[p][cm]\r\n'


# ---------------------------------------------------------------------------
# Text wrapping — width and wrap parameters in insert_lines
# ---------------------------------------------------------------------------


class TestWrapping:
    def test_long_text_wrapped_at_width(self):
        """Long translated text should be wrapped at the specified width."""
        translations = [
            ('This is a long translated sentence that should be wrapped at the specified width.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('wrapping.ks', translations, width=30, wrap='[r]', codec='ascii')
        lines = output.splitlines(keepends=True)
        # The translated text should be split into multiple lines joined by [r]
        assert '[r]\r\n' in output
        # The final line should end with the eol macro
        text_lines = [line for line in lines if not line.startswith(';') and not line.startswith('[')]
        assert text_lines[-1].endswith('[p][cm]\r\n')

    def test_short_text_not_wrapped(self):
        """Text shorter than width should not be wrapped."""
        translations = [
            ('Short text.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('wrapping.ks', translations, width=60, wrap='[r]', codec='ascii')
        lines = output.splitlines(keepends=True)
        assert lines[2] == 'Short text.[p][cm]\r\n'

    def test_no_wrap_when_text_has_commands(self):
        """Text containing [ should not be wrapped (it has inline commands)."""
        translations = [
            ('Text with [ruby text=command] inside.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('wrapping.ks', translations, width=10, wrap='[r]', codec='ascii')
        lines = output.splitlines(keepends=True)
        # Should be a single unwrapped line despite very small width
        assert lines[2] == 'Text with [ruby text=command] inside.[p][cm]\r\n'

    def test_no_wrap_when_width_is_none(self):
        """Without width, text should not be wrapped (default behavior)."""
        translations = [
            ('This is a long translated sentence that should not be wrapped at all.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('wrapping.ks', translations, width=None, codec='ascii')
        lines = output.splitlines(keepends=True)
        assert lines[2] == 'This is a long translated sentence that should not be wrapped at all.[p][cm]\r\n'

    def test_default_wrap_is_r(self):
        """When wrap is None but width is set, default wrap character should be [r]."""
        translations = [
            ('This is a long translated sentence that should be wrapped.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('wrapping.ks', translations, width=30, wrap=None, codec='ascii')
        assert '[r]\r\n' in output

    def test_custom_wrap_character(self):
        """Custom wrap string should be used between wrapped lines."""
        translations = [
            ('This is a long translated sentence that should be wrapped.', '[p][cm]'),
        ]
        output = extract_and_insert_translated('wrapping.ks', translations, width=30, wrap='[l][r]', codec='ascii')
        assert '[l][r]\r\n' in output
        # The custom wrap should appear, not the default [r]
        text_part = output.split('[cm]\r\n', 1)[1]  # skip header lines
        # Verify the wrap character appears between wrapped segments (not as eol)
        assert '[l][r]\r\n' in text_part
