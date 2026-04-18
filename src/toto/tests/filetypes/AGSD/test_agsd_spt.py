"""Tests for AGSD SPT format handler."""

import struct
from io import BytesIO

import pytest

from toto.filetypes.AgsdSpt import (
    TAG_BREAK,
    TAG_CHAR,
    AgsdSpt,
    _decode_text_block,
    _encode_text_entries,
    _find_text_blocks,
    _write_text_node,
)
from toto.util import TextLine

# ------------------------------------------------------------------ helpers --


def _make_entry(tag, char_bytes=b'\x00\x00'):
    """Build one 12-byte character entry."""
    return struct.pack('<II', tag, 0) + char_bytes + b'\x00\x00'


def _make_text_node(speaker, chars, *, trailing_break=True):
    """Build a complete binary text display node.

    *chars* is a string whose characters are encoded as TAG_CHAR entries
    (including ``\\n`` which becomes a TAG_CHAR with byte ``0x0A``).
    A final TAG_BREAK is appended if *trailing_break* is True (matching
    the real format's end-of-text marker).
    """
    entries_data = bytearray()
    count = 0
    for ch in chars:
        encoded = ch.encode('cp932')
        if len(encoded) == 1:
            cb = encoded + b'\x00'
        else:
            cb = encoded[:2]
        entries_data.extend(_make_entry(TAG_CHAR, cb))
        count += 1
    if trailing_break:
        entries_data.extend(_make_entry(TAG_BREAK))
        count += 1
    header = struct.pack('<IiIII', 0xFFFFFFFF, speaker, count, 0, 0)
    return header + bytes(entries_data)


def _make_raw_filler(size=44):
    """Build opaque filler bytes (simulates inter-node data)."""
    data = bytearray(size)
    # Mark with a recognisable pattern
    data[0:4] = struct.pack('<I', 1)
    return bytes(data)


def _make_spt_header(name='alice_01', node_count=3):
    """Build a minimal SPT file header."""
    name_bytes = name.encode('ascii')
    header = bytearray()
    header.extend(b'\xf0\xf0\x00\x00')  # identifier (dummy)
    header.extend(struct.pack('<I', 0))  # reserved
    header.extend(struct.pack('<I', len(name_bytes)))
    header.extend(name_bytes)
    header.extend(struct.pack('<I', node_count))
    return bytes(header)


def make_minimal_spt():
    """Build a minimal SPT file with one narration text node.

    Text: 「不思議の国のアリス」 (Alice in Wonderland)
    """
    header = _make_spt_header('alice_01', node_count=1)
    filler = _make_raw_filler(44)
    text_node = _make_text_node(-1, '「不思議の国のアリス」')
    return header + filler + text_node


def make_dialogue_spt():
    """Build an SPT file with dialogue and narration.

    Contains:
    - Raw header + initial filler
    - Dialogue by speaker #1: 「うさぎの穴に落ちたわ」
    - Filler gap
    - Narration: アリスは暗い穴の中を落ちていった。
    - Filler gap
    - Dialogue by speaker #2: 「ここはどこかしら？」
    - Trailing filler
    """
    header = _make_spt_header('alice_02', node_count=3)
    filler = _make_raw_filler(44)

    text1 = _make_text_node(1, '「うさぎの穴に落ちたわ」')
    text2 = _make_text_node(-1, 'アリスは暗い穴の中を落ちていった。')
    text3 = _make_text_node(2, '「ここはどこかしら？」')

    return header + filler + text1 + filler + text2 + filler + text3 + filler


def make_multiline_spt():
    """Build an SPT file with a multi-line text node.

    Contains a single narration node with an internal line break:
    "不思議の国のアリスは、\\nうさぎの穴に落ちた。"
    """
    header = _make_spt_header('alice_03', node_count=1)
    filler = _make_raw_filler(44)
    text_node = _make_text_node(-1, '不思議の国のアリスは、\nうさぎの穴に落ちた。')
    return header + filler + text_node


def make_no_text_spt():
    """Build an SPT file with no text nodes (utility script)."""
    header = _make_spt_header('bgm_play', node_count=0)
    filler = _make_raw_filler(200)
    return header + filler


# ------------------------------------------------------------------- tests --


class TestFindTextBlocks:
    @pytest.mark.unit
    def test_finds_single_block(self):
        data = make_minimal_spt()
        blocks = _find_text_blocks(data)
        assert len(blocks) == 1
        assert blocks[0]['speaker'] == -1

    @pytest.mark.unit
    def test_finds_multiple_blocks(self):
        data = make_dialogue_spt()
        blocks = _find_text_blocks(data)
        assert len(blocks) == 3
        assert blocks[0]['speaker'] == 1
        assert blocks[1]['speaker'] == -1
        assert blocks[2]['speaker'] == 2

    @pytest.mark.unit
    def test_no_text_file(self):
        data = make_no_text_spt()
        blocks = _find_text_blocks(data)
        assert len(blocks) == 0


class TestDecodeEncodeRoundtrip:
    @pytest.mark.unit
    def test_decode_simple(self):
        """TAG_BREAK is the end marker and is not included in decoded text."""
        entries = [
            (TAG_CHAR, '「'.encode('cp932')),
            (TAG_CHAR, 'ア'.encode('cp932')),
            (TAG_CHAR, '」'.encode('cp932')),
            (TAG_BREAK, b'\x00\x00'),
        ]
        assert _decode_text_block(entries) == '「ア」'

    @pytest.mark.unit
    def test_encode_simple(self):
        """Encoding always appends a trailing TAG_BREAK."""
        entries, raw = _encode_text_entries('「ア」')
        assert len(entries) == 4  # 3 chars + 1 trailing break
        assert entries[0] == (TAG_CHAR, '「'.encode('cp932'))
        assert entries[3] == (TAG_BREAK, b'\x00\x00')

    @pytest.mark.unit
    def test_roundtrip_text(self):
        original = '不思議の国のアリス'
        entries, _ = _encode_text_entries(original)
        decoded = _decode_text_block(entries)
        assert decoded == original

    @pytest.mark.unit
    def test_multiline_roundtrip(self):
        """Inline newlines are TAG_CHAR entries with CP932 0x0A."""
        original = '不思議の国の\nアリス'
        entries, _ = _encode_text_entries(original)
        decoded = _decode_text_block(entries)
        assert decoded == original

    @pytest.mark.unit
    def test_inline_newline_is_char_not_break(self):
        """Inline \\n encodes as TAG_CHAR, not TAG_BREAK."""
        entries, _ = _encode_text_entries('ア\nイ')
        assert entries[0] == (TAG_CHAR, 'ア'.encode('cp932'))
        assert entries[1] == (TAG_CHAR, b'\x0a\x00')  # newline as TAG_CHAR
        assert entries[2] == (TAG_CHAR, 'イ'.encode('cp932'))
        assert entries[3] == (TAG_BREAK, b'\x00\x00')  # trailing end marker


class TestExtractLines:
    @pytest.mark.unit
    def test_minimal_extract(self):
        data = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data))
        assert len(textlines) == 1
        assert textlines[0].text == '「不思議の国のアリス」'
        assert textlines[0].key == '<<<TRANS:0>>>'

    @pytest.mark.unit
    def test_dialogue_extract(self):
        data = make_dialogue_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data))
        assert len(textlines) == 3
        assert textlines[0].text == '「うさぎの穴に落ちたわ」'
        assert textlines[1].text == 'アリスは暗い穴の中を落ちていった。'
        assert textlines[2].text == '「ここはどこかしら？」'

    @pytest.mark.unit
    def test_multiline_extract(self):
        data = make_multiline_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data))
        assert len(textlines) == 1
        assert textlines[0].text == '不思議の国のアリスは、\nうさぎの穴に落ちた。'

    @pytest.mark.unit
    def test_no_text_extract(self):
        data = make_no_text_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data))
        assert len(textlines) == 0

    @pytest.mark.unit
    def test_ignore_pattern(self):
        """Lines matching ignore patterns are not extracted."""
        import re

        data = make_minimal_spt()
        pattern = re.compile(r'不思議')
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data), ignore_patterns=(pattern,))
        assert len(textlines) == 0


class TestIdentityRoundtrip:
    """Extract → identity insert (no translation changes) → compare."""

    @pytest.mark.unit
    def test_minimal_roundtrip(self):
        original = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
        assert output.read() == original

    @pytest.mark.unit
    def test_dialogue_roundtrip(self):
        original = make_dialogue_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
        assert output.read() == original

    @pytest.mark.unit
    def test_multiline_roundtrip(self):
        original = make_multiline_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
        assert output.read() == original

    @pytest.mark.unit
    def test_no_text_roundtrip(self):
        original = make_no_text_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
        assert output.read() == original


class TestTranslationInsertion:
    @pytest.mark.unit
    def test_replace_text(self):
        """Translated text replaces the original in the output."""
        original = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))

        # Create a translation
        trans = {
            textlines[0].key: TextLine(textlines[0].key, '「ワンダーランド」', b''),
        }
        intermediate.seek(0)
        output_data = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup')).read()

        # Re-extract from the output and check the text
        intermediate2, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output_data))
        assert len(textlines2) == 1
        assert textlines2[0].text == '「ワンダーランド」'

    @pytest.mark.unit
    def test_shorter_translation(self):
        """A shorter translation produces a smaller file."""
        original = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        trans = {
            textlines[0].key: TextLine(textlines[0].key, 'テスト', b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup')).read()
        assert len(output) < len(original)

        # Verify content
        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        assert textlines2[0].text == 'テスト'

    @pytest.mark.unit
    def test_longer_translation(self):
        """A longer translation produces a larger file."""
        original = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        long_text = '「不思議の国のアリスは、とても長い冒険に出かけました」'
        trans = {
            textlines[0].key: TextLine(textlines[0].key, long_text, b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup')).read()
        assert len(output) > len(original)

        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        assert textlines2[0].text == long_text

    @pytest.mark.unit
    def test_multiline_translation(self):
        """Multi-line text with internal breaks roundtrips correctly."""
        original = make_multiline_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        new_text = 'アリスは不思議な\n世界に迷い込んだ。'
        trans = {
            textlines[0].key: TextLine(textlines[0].key, new_text, b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup')).read()

        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        assert textlines2[0].text == new_text

    @pytest.mark.unit
    def test_multiline_file_roundtrip(self):
        """Newlines survive the extract→file→insert cycle.

        The CLI escapes actual newlines to ``\\n`` in translation files.
        The handler must unescape them back during insertion.
        This simulates that file round-trip.
        """
        original = make_multiline_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))

        # Simulate what toto.py extract writes to the translation file:
        #   t.text.replace('\n', '\\n') + '\n'
        file_line = textlines[0].text.replace('\n', '\\n') + '\n'

        # Simulate what toto.py insert reads back (no unescaping — that's
        # the handler's job):
        escaped_text = file_line  # includes trailing file newline

        trans = {
            textlines[0].key: TextLine(textlines[0].key, escaped_text, b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup')).read()

        # The output must be byte-identical to the original
        assert output == original

    @pytest.mark.unit
    def test_partial_translation(self):
        """Only some lines translated; others keep originals."""
        original = make_dialogue_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
        # Only translate the first line
        trans = {
            textlines[0].key: TextLine(textlines[0].key, '「テスト」', b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup')).read()

        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        assert textlines2[0].text == '「テスト」'
        assert textlines2[1].text == 'アリスは暗い穴の中を落ちていった。'
        assert textlines2[2].text == '「ここはどこかしら？」'


class TestUnwrapAndWrap:
    @pytest.mark.unit
    def test_unwrap_removes_newlines(self):
        """unwrap=True strips inline newlines from extracted text."""
        data = make_multiline_spt()
        _, textlines, _ = AgsdSpt.extract_lines(BytesIO(data), unwrap=True)
        assert len(textlines) == 1
        assert '\n' not in textlines[0].text
        assert textlines[0].text == '不思議の国のアリスは、うさぎの穴に落ちた。'

    @pytest.mark.unit
    def test_unwrap_false_preserves_newlines(self):
        """unwrap=False (default) keeps inline newlines."""
        data = make_multiline_spt()
        _, textlines, _ = AgsdSpt.extract_lines(BytesIO(data))
        assert '\n' in textlines[0].text

    @pytest.mark.unit
    def test_wrap_on_insert(self):
        """Translated text is wrapped at the given width."""
        data = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data))
        # Use a long translation that exceeds width
        long_text = 'アリスはうさぎの穴に落ちて不思議の国に辿り着いた'
        trans = {
            textlines[0].key: TextLine(textlines[0].key, long_text, b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(
            intermediate,
            trans,
            cp932_fixup=metadata.get('cp932_fixup'),
            width=15,
        ).read()

        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        # The re-extracted text should contain newlines from wrapping
        assert '\n' in textlines2[0].text

    @pytest.mark.unit
    def test_no_wrap_when_width_none(self):
        """No wrapping when width is None."""
        data = make_minimal_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data))
        long_text = 'アリスはうさぎの穴に落ちて不思議の国に辿り着いた'
        trans = {
            textlines[0].key: TextLine(textlines[0].key, long_text, b''),
        }
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(
            intermediate,
            trans,
            cp932_fixup=metadata.get('cp932_fixup'),
        ).read()

        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        assert textlines2[0].text == long_text

    @pytest.mark.unit
    def test_unwrap_extract_then_wrap_insert_roundtrip(self):
        """Unwrap on extract, re-wrap on insert with same width reproduces structure."""
        data = make_multiline_spt()
        intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(data), unwrap=True)
        # The unwrapped text has no newlines
        assert '\n' not in textlines[0].text
        # Re-insert with wrapping at a width that will cause re-wrapping
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = AgsdSpt.insert_lines(
            intermediate,
            trans,
            cp932_fixup=metadata.get('cp932_fixup'),
            width=12,
        ).read()
        # Re-extract and verify text was wrapped
        _, textlines2, _ = AgsdSpt.extract_lines(BytesIO(output))
        assert '\n' in textlines2[0].text
