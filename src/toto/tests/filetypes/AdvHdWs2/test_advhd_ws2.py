"""Tests for AdvHD WS2 format handler."""

import struct
from io import BytesIO

import pytest

from toto.filetypes.AdvHdWs2 import (
    AdvHdWs2,
    _detect_encrypted,
    _encode_str,
    _encrypt,
    _parse_file,
    _read_str,
)
from toto.util import TextLine

# ------------------------------------------------------------------ helpers --


def _str(text):
    """Encode text as UTF-16LE + null terminator."""
    return text.encode('utf-16-le') + b'\x00\x00'


def _u32(val):
    return struct.pack('<I', val)


def _u16(val):
    return struct.pack('<H', val)


def _make_display_name(name):
    """Build a SetDisplayName (0x15) instruction for the given name."""
    return b'\x15' + _str('%LF' + name) + b'\x00'


def _make_dialogue(seq, text, varname='char'):
    """Build a DisplayMessage (0x14) instruction."""
    return b'\x14' + _u32(seq) + _str(varname) + _str(text) + b'\x00'


def _make_choice_block(choices, branch_params=(11, 2, 1)):
    """Build a BranchSetup (0x0e) + ShowChoice (0x0f) block.

    *choices* is a list of ``(id, text, jump_target_filename)`` tuples.
    """
    # BranchSetup: word, word, byte
    data = b'\x0e' + _u16(branch_params[0]) + _u16(branch_params[1]) + bytes([branch_params[2]])
    # ShowChoice
    data += b'\x0f' + bytes([len(choices)])
    for choice_id, text, target in choices:
        data += _u16(choice_id) + _str(text)
        data += b'\x00\x0b\x00'  # op1, op2, op3
        data += b'\x07'  # opJump = 7 (file jump)
        data += _str(target)
    return data


def _make_file_end():
    """Build a FileEnd (0xFF) instruction."""
    return b'\xff' + _u32(128) + bytes([128, 0, 0, 0])


def _make_minimal_ws2():
    """Build a minimal WS2 file with one speaker + one dialogue line.

    Speaker: アリス
    Dialogue: 「不思議の国へようこそ」%K
    """
    return _make_display_name('アリス') + _make_dialogue(0, '「不思議の国へようこそ」%K') + _make_file_end()


def _make_choice_ws2():
    """Build a WS2 file with dialogue and a two-choice branch.

    Speaker: アリス
    Dialogue: 「どちらに行きましょう？」%K
    Choice 1: うさぎを追いかける
    Choice 2: 家に帰る
    Dialogue 2: (just %P, non-translatable)
    """
    return (
        _make_display_name('アリス')
        + _make_dialogue(0, '「どちらに行きましょう？」%K')
        + _make_choice_block(
            [
                (1, 'うさぎを追いかける', '00_SCN002A'),
                (2, '家に帰る', '00_SCN002B'),
            ]
        )
        + _make_display_name('アリス')
        + _make_dialogue(3, '%P')
        + _make_file_end()
    )


def _make_multi_speaker_ws2():
    """Build a WS2 with multiple speakers and dialogue lines."""
    return (
        _make_display_name('アリス')
        + _make_dialogue(0, '「ここはどこかしら？」%K')
        + _make_display_name('白うさぎ')
        + _make_dialogue(1, '「遅刻だ！遅刻だ！」%K%P')
        + _make_display_name('アリス')
        + _make_dialogue(2, '「待って！」%K')
        + _make_file_end()
    )


def extract(data):
    """Run extract_lines on raw WS2 data."""
    intermediate, textlines, metadata = AdvHdWs2.extract_lines(BytesIO(data))
    return intermediate, textlines, metadata


def extract_and_insert_identity(data):
    """Extract and re-insert with the original text (round-trip)."""
    intermediate, textlines, metadata = extract(data)
    translation_dict = {tl.key: tl for tl in textlines}
    output = AdvHdWs2.insert_lines(intermediate, translation_dict)
    return output.read()


def extract_and_insert_translated(data, translations):
    """Extract, then insert with custom translations.

    *translations* is a list of ``(text, eol)`` tuples, one per extracted line,
    in extraction order.
    """
    intermediate, textlines, metadata = extract(data)
    translation_dict = {}
    for tl, (text, eol) in zip(textlines, translations, strict=True):
        translation_dict[tl.key] = TextLine(tl.key, text, eol)
    output = AdvHdWs2.insert_lines(intermediate, translation_dict)
    return output.read()


# ------------------------------------------------------------------ unit tests --


class TestReadStr:
    @pytest.mark.unit
    def test_simple_ascii(self):
        data = _str('hello')
        text, pos = _read_str(data, 0)
        assert text == 'hello'
        assert pos == len(data)

    @pytest.mark.unit
    def test_japanese(self):
        data = _str('アリス')
        text, pos = _read_str(data, 0)
        assert text == 'アリス'

    @pytest.mark.unit
    def test_with_offset(self):
        data = b'\xff\xff' + _str('test')
        text, pos = _read_str(data, 2)
        assert text == 'test'


class TestEncodeStr:
    @pytest.mark.unit
    def test_roundtrip(self):
        original = 'アリスは不思議の国にいた'
        encoded = _encode_str(original)
        decoded, _ = _read_str(encoded, 0)
        assert decoded == original


class TestDetectEncrypted:
    @pytest.mark.unit
    def test_plaintext(self):
        data = _make_minimal_ws2()
        assert not _detect_encrypted(data)

    @pytest.mark.unit
    def test_encrypted(self):
        data = _make_minimal_ws2()
        encrypted = _encrypt(data)
        assert _detect_encrypted(encrypted)


# ---------------------------------------------------------- extraction tests --


class TestExtractMinimal:
    @pytest.mark.unit
    def test_extracts_name_and_dialogue(self):
        data = _make_minimal_ws2()
        _, textlines, _ = extract(data)
        assert len(textlines) == 2
        assert textlines[0].text == 'アリス'
        assert textlines[0].eol == ''
        assert textlines[1].text == '「不思議の国へようこそ」'
        assert textlines[1].eol == '%K'

    @pytest.mark.unit
    def test_keys_are_sequential(self):
        data = _make_minimal_ws2()
        _, textlines, _ = extract(data)
        assert textlines[0].key == '<<<TRANS:0>>>'
        assert textlines[1].key == '<<<TRANS:1>>>'


class TestExtractChoice:
    @pytest.mark.unit
    def test_extracts_choices(self):
        data = _make_choice_ws2()
        _, textlines, _ = extract(data)
        texts = [tl.text for tl in textlines]
        assert 'うさぎを追いかける' in texts
        assert '家に帰る' in texts

    @pytest.mark.unit
    def test_skips_empty_dialogue(self):
        """Dialogue with only %P (no text body) should not be extracted."""
        data = _make_choice_ws2()
        _, textlines, _ = extract(data)
        texts = [tl.text for tl in textlines]
        # %P-only dialogue is not extracted
        assert '' not in texts
        assert '%P' not in texts

    @pytest.mark.unit
    def test_choice_eol_is_empty(self):
        data = _make_choice_ws2()
        _, textlines, _ = extract(data)
        choice_lines = [tl for tl in textlines if tl.text in ('うさぎを追いかける', '家に帰る')]
        for tl in choice_lines:
            assert tl.eol == ''


class TestExtractMultiSpeaker:
    @pytest.mark.unit
    def test_extracts_all_lines(self):
        data = _make_multi_speaker_ws2()
        _, textlines, _ = extract(data)
        assert len(textlines) == 6  # 3 names + 3 dialogues

    @pytest.mark.unit
    def test_eol_variants(self):
        data = _make_multi_speaker_ws2()
        _, textlines, _ = extract(data)
        # Find the dialogue with %K%P
        kp_lines = [tl for tl in textlines if tl.eol == '%K%P']
        assert len(kp_lines) == 1
        assert kp_lines[0].text == '「遅刻だ！遅刻だ！」'


# ---------------------------------------------------------- roundtrip tests --


class TestRoundtrip:
    @pytest.mark.unit
    def test_minimal_roundtrip(self):
        data = _make_minimal_ws2()
        result = extract_and_insert_identity(data)
        assert result == data

    @pytest.mark.unit
    def test_choice_roundtrip(self):
        data = _make_choice_ws2()
        result = extract_and_insert_identity(data)
        assert result == data

    @pytest.mark.unit
    def test_multi_speaker_roundtrip(self):
        data = _make_multi_speaker_ws2()
        result = extract_and_insert_identity(data)
        assert result == data


# -------------------------------------------------------- insertion tests --


class TestInsertTranslated:
    @pytest.mark.unit
    def test_translate_dialogue(self):
        data = _make_minimal_ws2()
        result = extract_and_insert_translated(
            data,
            [
                ('Alice', ''),  # name
                ('"Welcome to Wonderland"', '%K'),  # dialogue
            ],
        )
        # Verify the translated text appears in the output
        assert '"Welcome to Wonderland"%K'.encode('utf-16-le') in result
        assert 'Alice'.encode('utf-16-le') in result
        # Verify the %LF prefix is preserved for the name
        assert '%LFAlice'.encode('utf-16-le') in result

    @pytest.mark.unit
    def test_translate_choices(self):
        data = _make_choice_ws2()
        _, textlines, _ = extract(data)
        translations = []
        for tl in textlines:
            if tl.text == 'アリス':
                translations.append(('Alice', ''))
            elif tl.text == '「どちらに行きましょう？」':
                translations.append(('"Which way shall we go?"', '%K'))
            elif tl.text == 'うさぎを追いかける':
                translations.append(('Chase the rabbit', ''))
            elif tl.text == '家に帰る':
                translations.append(('Go home', ''))
            else:
                translations.append((tl.text, tl.eol))

        result = extract_and_insert_translated(data, translations)
        assert 'Chase the rabbit'.encode('utf-16-le') in result
        assert 'Go home'.encode('utf-16-le') in result

    @pytest.mark.unit
    def test_translated_file_parses_cleanly(self):
        """A translated file should still parse without errors."""
        data = _make_minimal_ws2()
        result = extract_and_insert_translated(
            data,
            [
                ('Alice', ''),
                ('"Welcome to Wonderland"', '%K'),
            ],
        )
        # Re-extract the translated file — should not raise
        _, textlines, _ = extract(result)
        assert len(textlines) == 2
        assert textlines[0].text == 'Alice'
        assert textlines[1].text == '"Welcome to Wonderland"'
        assert textlines[1].eol == '%K'


class TestInsertEncrypted:
    @pytest.mark.unit
    def test_encrypted_roundtrip(self):
        data = _make_minimal_ws2()
        encrypted = _encrypt(data)
        result = extract_and_insert_identity(encrypted)
        assert result == encrypted

    @pytest.mark.unit
    def test_encrypted_translation(self):
        data = _make_minimal_ws2()
        encrypted = _encrypt(data)
        _, textlines, _ = extract(encrypted)
        # Should extract the same text regardless of encryption
        assert textlines[0].text == 'アリス'
        assert textlines[1].text == '「不思議の国へようこそ」'


# -------------------------------------------------------- pointer fixup tests --


class TestPointerFixup:
    @pytest.mark.unit
    def test_jump_fixup_with_longer_text(self):
        """When translated text is longer, jump targets should be adjusted."""
        # Build: name, dialogue, jump(target), name2, dialogue2, end
        # The jump points to the second name instruction.
        name1 = _make_display_name('ア')  # short name
        dialogue1 = _make_dialogue(0, 'テスト%K')

        name2 = _make_display_name('イ')
        dialogue2 = _make_dialogue(1, 'テスト2%K')
        end = _make_file_end()

        # Compute where name2 starts
        jump_target = len(name1) + len(dialogue1) + 5  # +5 for jump instruction
        jump_insn = b'\x02' + _u32(jump_target)

        data = name1 + dialogue1 + jump_insn + name2 + dialogue2 + end

        # Verify the original parses and round-trips
        result = extract_and_insert_identity(data)
        assert result == data

        # Now translate with longer text — the jump target should shift
        _, textlines, _ = extract(data)
        translations = []
        for tl in textlines:
            if tl.text == 'ア':
                translations.append(('Alice in Wonderland', ''))
            elif tl.text == 'テスト':
                translations.append(('This is a much longer test string for testing', '%K'))
            elif tl.text == 'イ':
                translations.append(('White Rabbit', ''))
            elif tl.text == 'テスト2':
                translations.append(('Second test', '%K'))
            else:
                translations.append((tl.text, tl.eol))

        result = extract_and_insert_translated(data, translations)

        # The result should still parse cleanly
        _, result_textlines, _ = extract(result)
        result_texts = [tl.text for tl in result_textlines]
        assert 'Alice in Wonderland' in result_texts
        assert 'This is a much longer test string for testing' in result_texts
        assert 'White Rabbit' in result_texts
        assert 'Second test' in result_texts

        # Verify the jump pointer was updated
        # Find the 0x02 opcode in the result
        jump_pos = result.index(b'\x02')
        new_ptr = struct.unpack_from('<I', result, jump_pos + 1)[0]
        # The pointer should point to where name2 now starts
        expected_target = jump_pos + 5  # right after the jump instruction
        assert new_ptr == expected_target


# -------------------------------------------------------- edge cases --


class TestEdgeCases:
    @pytest.mark.unit
    def test_no_translatable_content(self):
        """A file with only non-translatable opcodes should produce empty textlines."""
        # Just a FileEnd instruction
        data = _make_file_end()
        _, textlines, _ = extract(data)
        assert textlines == []

    @pytest.mark.unit
    def test_no_translatable_roundtrip(self):
        data = _make_file_end()
        result = extract_and_insert_identity(data)
        assert result == data

    @pytest.mark.unit
    def test_name_without_lf_prefix(self):
        """A SetDisplayName without %LF should not be extracted."""
        data = b'\x15' + _str('NoPrefix') + b'\x00' + _make_file_end()
        _, textlines, _ = extract(data)
        assert textlines == []

    @pytest.mark.unit
    def test_dialogue_only_eol(self):
        """Dialogue text that is only an EOL marker should not be extracted."""
        data = _make_display_name('テスト') + _make_dialogue(0, '%K') + _make_file_end()
        _, textlines, _ = extract(data)
        # Only the name should be extracted, not the %K-only dialogue
        assert len(textlines) == 1
        assert textlines[0].text == 'テスト'
