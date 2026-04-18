"""Tests for Anim format handler."""

from io import BytesIO

from toto.filetypes.Anim import Anim


def make_define_with_nonzero_key():
    """Create a minimal encrypted _define.dat with a non-zero encryption key."""
    content = bytearray()
    content += b'\x04\x00\x00\x00'
    content += b'SomeData\x00'
    content += '不思議の国のアリス'.encode('cp932') + b'\x00'
    # Encrypt with a non-zero key
    key = b'\xb5\x1c\x65\xbd\x0f\x38\xd0\x84\xe2\x88\xc5\x32\x72\x4e\x51\x2f'
    return bytes(Anim.encrypt(content, key=key))


def make_define_with_brackets():
    """Create a minimal encrypted _define.dat with 【】-delimited strings."""
    content = bytearray()
    # Binary preamble
    content += b'\x04\x00\x00\x00'
    content += b'SomeData\x00'
    content += b'\x01\x02\x03\x00'
    # Translatable strings with 【】 markers
    content += '\u3010テスト\u3011'.encode('cp932') + b'\x00'
    content += '\u3010名前\u3011'.encode('cp932') + b'\x00'
    return bytes(Anim.encrypt(content))


def make_define_without_brackets():
    """Create a minimal encrypted _define.dat with plain Japanese strings (no 【】)."""
    content = bytearray()
    # Binary preamble
    content += b'\x04\x00\x00\x00'
    content += b'SomeData\x00'
    content += b'\x01\x02\x03\x00'
    # Translatable strings without bracket markers
    content += '憧れの義姉さんとの秘め事。'.encode('cp932') + b'\x00'
    content += '俺は決断する。義姉さんを俺のものにするために。'.encode('cp932') + b'\x00'
    return bytes(Anim.encrypt(content))


def make_sce_with_fullwidth_space():
    """Create a minimal encrypted _sce.dat with a string ending in U+3000 (fullwidth space)."""
    strings_section = bytearray()
    strings_section += 'アリスは不思議の国へ\u3000'.encode('cp932') + b'\x00'
    strings_section += '帽子屋のお茶会'.encode('cp932') + b'\x00'

    content = bytearray()
    offset = 8  # 4 bytes padding + 4 bytes offset field
    content += b'\x00' * 4
    content += offset.to_bytes(4, byteorder='little')
    content += strings_section
    return bytes(Anim.encrypt(content))


def make_define_with_leading_tab():
    """Create a minimal encrypted _define.dat with strings starting with a tab."""
    content = bytearray()
    content += b'\x04\x00\x00\x00'
    content += b'SomeData\x00'
    content += ('\t不思議の国のアリス').encode('cp932') + b'\x00'
    content += ('\tチェシャ猫の微笑み').encode('cp932') + b'\x00'
    return bytes(Anim.encrypt(content))


class TestAnimDefine:
    def test_extract_with_brackets(self):
        f = BytesIO(make_define_with_brackets())
        f.name = 'test_define.dat'

        _, textlines, _ = Anim.extract_lines(f)

        assert len(textlines) == 2
        assert textlines[0].text == '\u3010テスト\u3011'
        assert textlines[1].text == '\u3010名前\u3011'

    def test_extract_without_brackets(self):
        f = BytesIO(make_define_without_brackets())
        f.name = 'test_define.dat'

        _, textlines, _ = Anim.extract_lines(f)

        assert len(textlines) == 2
        assert textlines[0].text == '憧れの義姉さんとの秘め事。'
        assert textlines[1].text == '俺は決断する。義姉さんを俺のものにするために。'

    def test_skips_ascii_strings(self):
        """ASCII-only strings like 'SkipAnime' should not be extracted."""
        content = bytearray()
        content += b'\x04\x00\x00\x00'
        content += b'SkipAnime\x00'
        content += b'AnotherThing\x00'
        content += '日本語のテキスト。'.encode('cp932') + b'\x00'
        encrypted = bytes(Anim.encrypt(content))

        f = BytesIO(encrypted)
        f.name = 'test_define.dat'

        _, textlines, _ = Anim.extract_lines(f)

        assert len(textlines) == 1
        assert textlines[0].text == '日本語のテキスト。'

    def test_roundtrip_with_brackets(self):
        encrypted = make_define_with_brackets()
        f = BytesIO(encrypted)
        f.name = 'test_define.dat'

        intermediate, textlines, metadata = Anim.extract_lines(f)
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Anim.insert_lines(intermediate, trans, encryption_key=metadata.get('encryption_key'))

        assert output.read() == encrypted

    def test_roundtrip_preserves_nonzero_key(self):
        """Roundtrip must preserve the original encryption key, not reset to zeros."""
        encrypted = make_define_with_nonzero_key()
        f = BytesIO(encrypted)
        f.name = 'test_define.dat'

        intermediate, textlines, metadata = Anim.extract_lines(f)
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Anim.insert_lines(intermediate, trans, encryption_key=metadata.get('encryption_key'))

        result = output.read()
        assert result == encrypted
        # Verify the key bytes are preserved, not zeroed
        assert result[4:20] == encrypted[4:20]

    def test_roundtrip_without_brackets(self):
        encrypted = make_define_without_brackets()
        f = BytesIO(encrypted)
        f.name = 'test_define.dat'

        intermediate, textlines, metadata = Anim.extract_lines(f)
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Anim.insert_lines(intermediate, trans, encryption_key=metadata.get('encryption_key'))

        assert output.read() == encrypted


class TestAnimWhitespacePreservation:
    """Regression tests for TOTO-8: .strip() must not remove meaningful whitespace."""

    def test_roundtrip_preserves_trailing_fullwidth_space(self):
        """U+3000 IDEOGRAPHIC SPACE at end of _sce.dat strings must survive roundtrip."""
        encrypted = make_sce_with_fullwidth_space()
        f = BytesIO(encrypted)
        f.name = 'test_sce.dat'

        intermediate, textlines, metadata = Anim.extract_lines(f)

        # Verify the fullwidth space was extracted
        assert textlines[0].text.endswith('\u3000')

        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Anim.insert_lines(intermediate, trans, encryption_key=metadata.get('encryption_key'))

        assert output.read() == encrypted

    def test_roundtrip_preserves_leading_tab(self):
        """Leading tab on _define.dat strings must survive roundtrip."""
        encrypted = make_define_with_leading_tab()
        f = BytesIO(encrypted)
        f.name = 'test_define.dat'

        intermediate, textlines, metadata = Anim.extract_lines(f)

        # Verify the tab was extracted
        assert textlines[0].text.startswith('\t')
        assert textlines[1].text.startswith('\t')

        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Anim.insert_lines(intermediate, trans, encryption_key=metadata.get('encryption_key'))

        assert output.read() == encrypted
