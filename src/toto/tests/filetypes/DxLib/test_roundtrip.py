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
