"""Unit tests for DxLib identity roundtrip.

Uses synthetic DxLib-format files with known content to verify that
extract → identity-insert produces byte-identical output.
"""

import struct
from io import BytesIO

import pytest

from toto.filetypes.DxLib import DxLib


def _make_dxlib_file(strings, header_prefix=b"\xde\xad\xbe\xef", pre_string_body=b""):
    """Build a minimal DxLib-format file.

    Layout:
        bytes 0-3:  total_file_size - 0x10  (LE uint32)
        bytes 4-7:  strings_offset  - 0x10  (LE uint32)
        bytes 8-15: padding (zeros)
        bytes 16+:  pre_string_body (non-string data, if any)
        then:       null-terminated CP932 strings

    If *header_prefix* is given it replaces the default bytes 0-3, allowing
    tests to verify that arbitrary header content is preserved.
    """
    header_size = 0x10
    pre_string_data = pre_string_body
    strings_offset = header_size + len(pre_string_data)

    # Build string section
    string_data = b""
    for s in strings:
        string_data += s.encode("cp932") + b"\x00"

    total_size = strings_offset + len(string_data)

    header = bytearray(header_size)
    # bytes 0-3: total_size - 0x10
    struct.pack_into("<I", header, 0, total_size - header_size)
    # bytes 4-7: strings_offset - 0x10
    struct.pack_into("<I", header, 4, strings_offset - header_size)

    if header_prefix is not None:
        header[0 : len(header_prefix)] = header_prefix

    return bytes(header) + pre_string_data + string_data


def _roundtrip(data):
    """Extract then identity-insert, return output bytes."""
    intermediate, textlines, _ = DxLib.extract_lines(BytesIO(data))
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = DxLib.insert_lines(intermediate, trans)
    return output.read()


class TestIdentityRoundtrip:
    @pytest.mark.unit
    def test_plain_japanese_strings(self):
        """Basic roundtrip with ordinary translatable strings."""
        original = _make_dxlib_file(
            [
                "\u30a2\u30ea\u30b9\u306f\u4e0d\u601d\u8b70\u306e\u56fd\u306b\u3044\u305f",  # Alice was in Wonderland
                "nontranslatable",
                "\u767d\u3046\u3055\u304e\u304c\u8d70\u3063\u3066\u3044\u305f",  # The White Rabbit was running
            ],
            header_prefix=None,
        )
        assert _roundtrip(original) == original

    @pytest.mark.unit
    def test_leading_ideographic_space_preserved(self):
        """Strings with leading U+3000 must survive roundtrip intact."""
        original = _make_dxlib_file(
            [
                "\u3000\u30a2\u30ea\u30b9\u306f\u4e0d\u601d\u8b70\u306e\u56fd\u306b\u3044\u305f",  # U+3000 + Alice...
                "\u3000\u767d\u3046\u3055\u304e\u304c\u8d70\u3063\u3066\u3044\u305f",  # U+3000 + Rabbit...
            ],
            header_prefix=None,
        )
        assert _roundtrip(original) == original

    @pytest.mark.unit
    def test_trailing_space_preserved(self):
        """Strings with trailing ASCII space must survive roundtrip intact."""
        original = _make_dxlib_file(
            [
                "\u30a2\u30ea\u30b9 ",  # trailing space
            ],
            header_prefix=None,
        )
        assert _roundtrip(original) == original

    @pytest.mark.unit
    def test_header_bytes_preserved(self):
        """Bytes 0-3 of the header must not be corrupted by insert_lines."""
        # Use a distinctive magic number in bytes 0-3 that differs from
        # the standard total_size - 0x10 value.
        magic = b"\xca\xfe\xba\xbe"
        original = _make_dxlib_file(
            [
                "\u30a2\u30ea\u30b9\u306f\u4e0d\u601d\u8b70\u306e\u56fd\u306b\u3044\u305f",
            ],
            header_prefix=magic,
        )
        result = _roundtrip(original)
        assert result[:4] == magic, f"Header bytes 0-3 were overwritten: expected {magic!r}, got {result[:4]!r}"
        assert result == original


def _translate(data, suffix):
    """Extract, append *suffix* to every translatable line, insert; return output bytes."""
    intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(data))
    trans = {t.key: t._replace(text=t.text + suffix) for t in textlines}
    intermediate.seek(0)
    output = DxLib.insert_lines(intermediate, trans, **metadata)
    return output.read()


class TestBodySize:
    ALICE = "アリスは不思議の国にいた"  # Alice was in Wonderland
    RABBIT = "白うさぎが走っていた"  # The White Rabbit was running

    @pytest.mark.unit
    @pytest.mark.parametrize("suffix", ["、そして穴に落ちた", ""], ids=["longer", "same"])
    def test_body_size_tracks_output_length(self, suffix):
        """bytes 0-3 must equal len(output) - 0x10 after insertion."""
        original = _make_dxlib_file([self.ALICE, "#cue", self.RABBIT], header_prefix=None, pre_string_body=b"\x01\x02")
        result = _translate(original, suffix)
        assert struct.unpack_from("<I", result, 0)[0] == len(result) - 0x10

    @pytest.mark.unit
    def test_shorter_translation(self):
        original = _make_dxlib_file([self.ALICE], header_prefix=None)
        intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(original))
        trans = {t.key: t._replace(text="猫") for t in textlines}  # Cat
        intermediate.seek(0)
        result = DxLib.insert_lines(intermediate, trans, **metadata).read()
        assert struct.unpack_from("<I", result, 0)[0] == len(result) - 0x10

    @pytest.mark.unit
    def test_nonconforming_header_left_alone(self):
        """If bytes 0-3 weren't a body size in the input, they are not rewritten."""
        magic = b"\xca\xfe\xba\xbe"
        original = _make_dxlib_file([self.ALICE], header_prefix=magic)
        result = _translate(original, "、そして")
        assert result[:4] == magic


def _make_labelled_script(text, label_offset=0x104):
    """A script with one label: the pool starts with its u16 code offset, before the strings."""
    code = (struct.pack("<HB", 1, 1) + b"\x03") * (label_offset // 4)  # blank-line records
    code += struct.pack("<HB", 2, 2) + b"\x07\x00"  # label 0 is defined here
    code += struct.pack("<HB", 3, 3) + b"\x01\x00\x00"  # text, string 0
    code += struct.pack("<HB", 4, 1) + b"\x04" + b"\x00"  # click, code terminator
    pool = struct.pack("<H", label_offset) + text.encode("cp932") + b"\0" + b"start\0"
    header = struct.pack("<IIHHI", len(code) + len(pool), len(code), 1, 1, 4)
    return header + code + pool


class TestLabelTable:
    ALICE = "アリスは不思議の国にいた"  # Alice was in Wonderland

    @pytest.mark.unit
    def test_label_offsets_not_extracted(self):
        """The label offset table before the strings must not leak into the first string."""
        _, textlines, _ = DxLib.extract_lines(BytesIO(_make_labelled_script(self.ALICE)))
        assert [t.text for t in textlines] == [self.ALICE]

    @pytest.mark.unit
    def test_label_offsets_survive_translation(self):
        original = _make_labelled_script(self.ALICE)
        result = _translate(original, "、そして")
        pool = struct.unpack_from("<I", original, 4)[0] + 0x10
        assert result[pool : pool + 2] == original[pool : pool + 2]
        assert result == _make_labelled_script(self.ALICE + "、そして")

    @pytest.mark.unit
    def test_roundtrip(self):
        original = _make_labelled_script(self.ALICE)
        assert _roundtrip(original) == original
