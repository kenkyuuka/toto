"""Tests for mgos format handler."""

import struct
from io import BytesIO

from toto.filetypes.Mgos import Mgos
from toto.util import TextLine


def _make_string_entry(text_bytes):
    """Build a single string table entry: [2-byte LE length][data][0x00]."""
    str_len = len(text_bytes) + 1  # +1 for null terminator
    return struct.pack('<H', str_len) + text_bytes + b'\x00'


def make_mgos_script():
    """Build a minimal synthetic .o file with bytecode + string table.

    String table contains:
      0: "fontfat"          — ASCII system string (not extracted)
      1: "end"              — ASCII system string (not extracted)
      2: ""                 — empty string (not extracted)
      3: Japanese dialogue  — extracted
      4: Japanese narration — extracted
      5: string with \\x07 control code — extracted (contains Japanese)
    """
    # Build string table entries
    s0 = b'fontfat'
    s1 = b'end'
    s2 = b''  # empty string
    s3 = 'ちはや\u3000「おはよう」'.encode('cp932')
    s4 = '\u3000朝の光が差し込む。'.encode('cp932')
    s5 = '表示\x070032テスト'.encode('cp932')

    entries = [s0, s1, s2, s3, s4, s5]
    entry_blobs = [_make_string_entry(e) for e in entries]

    # Bytecode: some filler + opcode 0x02 references to each string
    # We need to know the bytecode length to compute string offsets.
    # Each reference is 5 bytes: 0x02 + 4-byte LE offset
    # We'll add some filler bytes too.
    filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)  # 20 bytes of init sequence

    num_refs = len(entries)
    # bytecode_length = filler + (5 bytes per ref)
    bytecode_length = len(filler) + num_refs * 5

    # Compute string table offsets
    string_offsets = []
    current = bytecode_length
    for blob in entry_blobs:
        string_offsets.append(current)
        current += len(blob)

    # Build bytecode
    bytecode = bytearray(filler)
    for offset in string_offsets:
        bytecode.append(0x02)
        bytecode.extend(struct.pack('<I', offset))

    assert len(bytecode) == bytecode_length

    # Build complete file
    string_table = b''.join(entry_blobs)
    return bytes(bytecode) + string_table


def make_mgos_dialogue_script():
    """Build a synthetic .o file with named dialogue lines.

    String table contains:
      0: "fontfat"          — ASCII system string (not extracted)
      1: "ちはや　「おはよう」"    — name + dialogue
      2: "あかね　「おはよう、ちーちゃん」" — different name + dialogue
      3: "ちはや　「いい天気だね」"   — same name (ちはや) again
      4: "　朝の光が差し込む。"       — narration with leading fullwidth space (no name)
    """
    s0 = b'fontfat'
    s1 = 'ちはや\u3000「おはよう」'.encode('cp932')
    s2 = 'あかね\u3000「おはよう、ちーちゃん」'.encode('cp932')
    s3 = 'ちはや\u3000「いい天気だね」'.encode('cp932')
    s4 = '\u3000朝の光が差し込む。'.encode('cp932')

    entries = [s0, s1, s2, s3, s4]
    entry_blobs = [_make_string_entry(e) for e in entries]

    filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
    num_refs = len(entries)
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

    assert len(bytecode) == bytecode_length
    string_table = b''.join(entry_blobs)
    return bytes(bytecode) + string_table


class TestMgosNameSplit:
    def test_names_split_from_dialogue(self):
        """Character names are extracted separately from dialogue text."""
        data = make_mgos_dialogue_script()
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))

        texts = [t.text for t in textlines]
        # Names should be separate entries
        assert 'ちはや' in texts
        assert 'あかね' in texts
        # Dialogue should not include the name
        assert 'おはよう' in texts
        assert 'おはよう、ちーちゃん' in texts
        assert 'いい天気だね' in texts
        # Narration (starts with fullwidth space, no name) stays as-is
        assert '朝の光が差し込む。' in texts

    def test_same_name_shares_key(self):
        """The same character name within a file gets the same TRANS key."""
        data = make_mgos_dialogue_script()
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))

        # Find all keys for 'ちはや'
        chihaya_keys = [t.key for t in textlines if t.text == 'ちはや']
        # Should appear exactly once in textlines (deduplicated)
        assert len(chihaya_keys) == 1

    def test_name_split_roundtrip(self):
        """Extract with identity translation produces identical output."""
        original = make_mgos_dialogue_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        assert output.read() == original

    def test_name_split_translated(self):
        """Translating names and dialogue separately works correctly."""
        original = make_mgos_dialogue_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))

        trans = {}
        for t in textlines:
            if t.text == 'ちはや':
                trans[t.key] = TextLine(t.key, 'Chihaya', t.eol)
            elif t.text == 'あかね':
                trans[t.key] = TextLine(t.key, 'Akane', t.eol)
            elif t.text == 'おはよう':
                trans[t.key] = TextLine(t.key, 'Good morning', t.eol)
            elif t.text == 'おはよう、ちーちゃん':
                trans[t.key] = TextLine(t.key, 'Morning, Chi-chan', t.eol)
            elif t.text == 'いい天気だね':
                trans[t.key] = TextLine(t.key, 'Nice weather', t.eol)
            else:
                trans[t.key] = t

        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        modified = output.read()

        # Re-extract and verify the combined strings
        _, textlines2, _ = Mgos.extract_lines(BytesIO(modified))
        texts2 = [t.text for t in textlines2]
        assert 'Chihaya' in texts2
        assert 'Akane' in texts2
        assert 'Good morning' in texts2
        assert 'Morning, Chi-chan' in texts2
        assert 'Nice weather' in texts2


def make_mgos_direction_script():
    """Build a synthetic .o file with post-quote stage directions.

    String table contains:
      0: "fontfat"          — ASCII system string (not extracted)
      1: "ちはや　「おはよう」（元気に）"  — dialogue + direction
      2: "あかね　「ちーちゃん？」（？？？で）" — dialogue + direction
      3: "ちはや　「（うーん……）」"       — inner parenthetical (NOT a direction)
      4: "ちはや　「いい天気だね」"        — plain dialogue (no direction)
      5: "　朝の光が差し込む。"            — narration (no name, no quotes)
    """
    s0 = b'fontfat'
    s1 = 'ちはや\u3000「おはよう」（元気に）'.encode('cp932')
    s2 = 'あかね\u3000「ちーちゃん？」（？？？で）'.encode('cp932')
    s3 = 'ちはや\u3000「（うーん……）」'.encode('cp932')
    s4 = 'ちはや\u3000「いい天気だね」'.encode('cp932')
    s5 = '\u3000朝の光が差し込む。'.encode('cp932')

    entries = [s0, s1, s2, s3, s4, s5]
    entry_blobs = [_make_string_entry(e) for e in entries]

    filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
    num_refs = len(entries)
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

    assert len(bytecode) == bytecode_length
    string_table = b''.join(entry_blobs)
    return bytes(bytecode) + string_table


class TestMgosQuotesAndDirections:
    """Quotation marks are excluded from translatable text;
    post-quote parenthetical stage directions are extracted separately."""

    def test_quotes_excluded_from_dialogue(self):
        """「」 are not part of the extracted dialogue text."""
        data = make_mgos_direction_script()
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))
        texts = [t.text for t in textlines]
        # Dialogue text should not include quotation marks
        assert 'おはよう' in texts
        assert 'いい天気だね' in texts
        assert 'ちーちゃん？' in texts
        # No text should still have the 「」 wrapper
        for t in texts:
            if t in ('ちはや', 'あかね') or t.startswith('\u3000'):
                continue
            assert not (t.startswith('「') and t.endswith('」')), f"Dialogue still wrapped in quotes: {t!r}"

    def test_direction_extracted_separately(self):
        """Post-quote （direction） becomes a separate TextLine."""
        data = make_mgos_direction_script()
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))
        texts = [t.text for t in textlines]
        assert '元気に' in texts
        assert '？？？で' in texts

    def test_inner_parens_not_split(self):
        """Parentheticals inside 「」 are part of the dialogue, not directions."""
        data = make_mgos_direction_script()
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))
        texts = [t.text for t in textlines]
        # 「（うーん……）」 has parens inside quotes — the paren content
        # is part of the dialogue, not a separate direction
        assert '（うーん……）' in texts
        # And it should NOT appear as a stripped direction
        assert 'うーん……' not in texts

    def test_direction_roundtrip(self):
        """Extract with identity translation produces identical output."""
        original = make_mgos_direction_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        assert output.read() == original

    def test_direction_translated(self):
        """Translating dialogue and directions reinserts them correctly."""
        original = make_mgos_direction_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))

        trans = {}
        for t in textlines:
            if t.text == 'ちはや':
                trans[t.key] = TextLine(t.key, 'Chihaya', t.eol)
            elif t.text == 'あかね':
                trans[t.key] = TextLine(t.key, 'Akane', t.eol)
            elif t.text == 'おはよう':
                trans[t.key] = TextLine(t.key, 'Good morning', t.eol)
            elif t.text == '元気に':
                trans[t.key] = TextLine(t.key, 'cheerfully', t.eol)
            elif t.text == 'ちーちゃん？':
                trans[t.key] = TextLine(t.key, 'Chi-chan?', t.eol)
            elif t.text == '？？？で':
                trans[t.key] = TextLine(t.key, 'puzzled', t.eol)
            elif t.text == 'いい天気だね':
                trans[t.key] = TextLine(t.key, 'Nice weather', t.eol)
            elif t.text == '（うーん……）':
                trans[t.key] = TextLine(t.key, '(Hmm...)', t.eol)
            else:
                trans[t.key] = t

        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        modified = output.read()

        # Re-extract and verify
        _, textlines2, _ = Mgos.extract_lines(BytesIO(modified))
        texts2 = [t.text for t in textlines2]
        assert 'Chihaya' in texts2
        assert 'Akane' in texts2
        assert 'Good morning' in texts2
        assert 'cheerfully' in texts2
        assert 'Chi-chan?' in texts2
        assert 'puzzled' in texts2
        assert 'Nice weather' in texts2
        assert '(Hmm...)' in texts2


class TestMgosCommandSkip:
    def test_commands_not_extracted(self):
        """Strings starting with ● are engine commands and must not be extracted."""
        # Build a script with a command string mixed in with dialogue
        s0 = b'fontfat'
        s1 = 'ちはや\u3000「おはよう」'.encode('cp932')
        s2 = '●ＢＧ、居間（夜）'.encode('cp932')
        s3 = '●ＳＥ、鳥の声'.encode('cp932')
        s4 = '\u3000朝の光が差し込む。'.encode('cp932')

        entries = [s0, s1, s2, s3, s4]
        entry_blobs = [_make_string_entry(e) for e in entries]

        filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        bytecode_length = len(filler) + len(entries) * 5

        string_offsets = []
        current = bytecode_length
        for blob in entry_blobs:
            string_offsets.append(current)
            current += len(blob)

        bytecode = bytearray(filler)
        for offset in string_offsets:
            bytecode.append(0x02)
            bytecode.extend(struct.pack('<I', offset))

        data = bytes(bytecode) + b''.join(entry_blobs)
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))

        texts = [t.text for t in textlines]
        # Commands should NOT be extracted
        assert '●ＢＧ、居間（夜）' not in texts
        assert '●ＳＥ、鳥の声' not in texts
        # Dialogue and narration should still be extracted
        assert 'ちはや' in texts
        assert 'おはよう' in texts
        assert '朝の光が差し込む。' in texts

    def test_circle_commands_not_extracted(self):
        """Strings starting with ○ are engine commands and must not be extracted."""
        s0 = b'fontfat'
        s1 = '○ＳＤ、e020cX、後ろ向きe120も判定。'.encode('cp932')
        s2 = '♪ＢＧＭ、ヒロイン１のテーマ'.encode('cp932')
        s3 = '!003_op_選択肢残り_0007'.encode('cp932')
        s4 = '⑤予行練習をする'.encode('cp932')

        entries = [s0, s1, s2, s3, s4]
        entry_blobs = [_make_string_entry(e) for e in entries]

        filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        bytecode_length = len(filler) + len(entries) * 5

        string_offsets = []
        current = bytecode_length
        for blob in entry_blobs:
            string_offsets.append(current)
            current += len(blob)

        bytecode = bytearray(filler)
        for offset in string_offsets:
            bytecode.append(0x02)
            bytecode.extend(struct.pack('<I', offset))

        data = bytes(bytecode) + b''.join(entry_blobs)
        _, textlines, _ = Mgos.extract_lines(BytesIO(data))

        texts = [t.text for t in textlines]
        assert '○ＳＤ、e020cX、後ろ向きe120も判定。' not in texts
        assert '♪ＢＧＭ、ヒロイン１のテーマ' not in texts
        assert '!003_op_選択肢残り_0007' not in texts
        assert '⑤予行練習をする' in texts

    def test_command_roundtrip(self):
        """Commands survive roundtrip unchanged."""
        s0 = b'fontfat'
        s1 = 'ちはや\u3000「おはよう」'.encode('cp932')
        s2 = '●ＢＧ、居間（夜）'.encode('cp932')

        entries = [s0, s1, s2]
        entry_blobs = [_make_string_entry(e) for e in entries]

        filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        bytecode_length = len(filler) + len(entries) * 5

        string_offsets = []
        current = bytecode_length
        for blob in entry_blobs:
            string_offsets.append(current)
            current += len(blob)

        bytecode = bytearray(filler)
        for offset in string_offsets:
            bytecode.append(0x02)
            bytecode.extend(struct.pack('<I', offset))

        original = bytes(bytecode) + b''.join(entry_blobs)
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        assert output.read() == original


class TestMgosNoStrings:
    """Files with no string references should be handled gracefully."""

    def test_no_string_refs_returns_empty(self):
        """A bytecode-only file with no string refs returns empty textlines."""
        # Pure bytecode: just filler instructions, no string ref opcodes
        data = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        intermediate, textlines, metadata = Mgos.extract_lines(BytesIO(data))
        assert textlines == []

    def test_no_string_refs_roundtrip(self):
        """A no-strings file survives extract+insert roundtrip."""
        original = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))
        assert textlines == []
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, {})
        assert output.read() == original

    def test_non_bytecode_file_returns_empty(self):
        """A file whose first byte is not a valid opcode returns empty textlines."""
        # Simulate a BMP or other non-mgos file passed by shell glob
        data = b'BM\x00\x00\x00\x00' + b'\xff' * 100
        intermediate, textlines, metadata = Mgos.extract_lines(BytesIO(data))
        assert textlines == []


class TestMgosExtract:
    def test_extracts_japanese(self):
        """Japanese strings are extracted; ASCII and empty strings are not."""
        data = make_mgos_script()
        f = BytesIO(data)

        _, textlines, _ = Mgos.extract_lines(f)

        texts = [t.text for t in textlines]
        # Name 'ちはや' is split out from dialogue 'おはよう'
        assert len(textlines) == 4
        assert 'ちはや' in texts
        assert 'おはよう' in texts
        assert '朝の光が差し込む。' in texts
        assert '表示\x070032テスト' in texts

    def test_control_codes_preserved(self):
        """\\x07 variable references survive extraction and roundtrip."""
        data = make_mgos_script()
        f = BytesIO(data)

        _, textlines, _ = Mgos.extract_lines(f)

        control_lines = [t for t in textlines if '\x07' in t.text]
        assert len(control_lines) == 1
        assert '\x070032' in control_lines[0].text

        # Roundtrip: insert original text back
        f.seek(0)
        intermediate, textlines, _ = Mgos.extract_lines(f)
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        result = output.read()

        # Re-extract and check control code is still there
        f2 = BytesIO(result)
        _, textlines2, _ = Mgos.extract_lines(f2)
        control_lines2 = [t for t in textlines2 if '\x07' in t.text]
        assert len(control_lines2) == 1
        assert '\x070032' in control_lines2[0].text


class TestMgosCp932Fixup:
    def test_ibm_extended_roundtrip(self):
        """CP932 IBM extended chars (0xFA range) survive roundtrip unchanged."""
        # ⅰⅱⅲ encoded as IBM extended (FA40-FA42), not NEC (EEEF-EEF1)
        s0 = b'fontfat'
        s1 = b'\xfa\x40\xfa\x41\xfa\x42'  # ⅰⅱⅲ in IBM extended range

        entries = [_make_string_entry(s0), _make_string_entry(s1)]

        filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        bytecode_length = len(filler) + len(entries) * 5

        string_offsets = []
        current = bytecode_length
        for blob in entries:
            string_offsets.append(current)
            current += len(blob)

        bytecode = bytearray(filler)
        for offset in string_offsets:
            bytecode.append(0x02)
            bytecode.extend(struct.pack('<I', offset))

        original = bytes(bytecode) + b''.join(entries)
        intermediate, textlines, metadata = Mgos.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
        assert output.read() == original

    def test_ibm_extended_in_dialogue(self):
        """IBM extended chars inside split dialogue strings survive roundtrip."""
        s0 = b'fontfat'
        # ちはや　「ⅰ番を選んで」 with ⅰ as FA40
        s1 = 'ちはや\u3000「'.encode('cp932') + b'\xfa\x40' + '番を選んで」'.encode('cp932')

        entries = [_make_string_entry(s0), _make_string_entry(s1)]

        filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        bytecode_length = len(filler) + len(entries) * 5

        string_offsets = []
        current = bytecode_length
        for blob in entries:
            string_offsets.append(current)
            current += len(blob)

        bytecode = bytearray(filler)
        for offset in string_offsets:
            bytecode.append(0x02)
            bytecode.extend(struct.pack('<I', offset))

        original = bytes(bytecode) + b''.join(entries)
        intermediate, textlines, metadata = Mgos.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
        assert output.read() == original


class TestMgosRoundtrip:
    def test_roundtrip(self):
        """Extract with identity translation produces byte-for-byte identical output."""
        original = make_mgos_script()
        f = BytesIO(original)

        intermediate, textlines, _ = Mgos.extract_lines(f)
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)

        assert output.read() == original

    def test_roundtrip_trailing_newlines(self):
        """Trailing whitespace on translations (as from reading a text file) is stripped."""
        original = make_mgos_script()
        f = BytesIO(original)

        intermediate, textlines, _ = Mgos.extract_lines(f)
        # Simulate the CLI pipeline: translations have trailing newlines
        trans = {t.key: TextLine(t.key, t.text + '\n', t.eol) for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)

        assert output.read() == original


class TestMgosChangedLengths:
    def test_changed_lengths(self):
        """Replacing strings with different-length translations patches offsets correctly."""
        original = make_mgos_script()
        f = BytesIO(original)

        intermediate, textlines, _ = Mgos.extract_lines(f)

        # Build translations with different lengths
        trans = {}
        for t in textlines:
            if t.text == 'おはよう':
                # Longer replacement for dialogue
                trans[t.key] = TextLine(t.key, 'おはよう！元気？', t.eol)
            elif '朝' in t.text:
                # Longer replacement
                trans[t.key] = TextLine(t.key, '朝の光が窓から差し込んで、部屋全体を照らしている。', t.eol)
            else:
                trans[t.key] = t

        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        modified = output.read()

        # Re-extract from modified file and verify strings
        f2 = BytesIO(modified)
        _, textlines2, _ = Mgos.extract_lines(f2)

        texts2 = [t.text for t in textlines2]
        assert 'ちはや' in texts2
        assert 'おはよう！元気？' in texts2
        assert '朝の光が窓から差し込んで、部屋全体を照らしている。' in texts2
        assert len(textlines2) == 4

    def test_offsets_patched_correctly(self):
        """Bytecode references point to correct string table offsets after length changes."""
        original = make_mgos_script()
        f = BytesIO(original)

        intermediate, textlines, _ = Mgos.extract_lines(f)

        # Replace with a much shorter string
        trans = {}
        for t in textlines:
            if 'ちはや' in t.text:
                trans[t.key] = TextLine(t.key, 'Hi', t.eol)
            else:
                trans[t.key] = t

        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        modified = output.read()

        # Verify: every 0x02 reference in bytecode points to a valid string entry
        # Find string table start by looking for the minimum 0x02 reference
        file_len = len(modified)
        min_ref = file_len
        for i in range(file_len - 4):
            if modified[i] == 0x02:
                offset = struct.unpack_from('<I', modified, i + 1)[0]
                if offset < file_len and offset > i:
                    min_ref = min(min_ref, offset)

        # Parse string table from min_ref
        pos = min_ref
        valid_starts = set()
        while pos < file_len:
            valid_starts.add(pos)
            str_len = struct.unpack_from('<H', modified, pos)[0]
            assert str_len > 0, f"Zero-length string at {pos}"
            assert pos + 2 + str_len <= file_len, f"String overruns file at {pos}"
            assert modified[pos + 2 + str_len - 1] == 0, f"Missing null at {pos}"
            pos += 2 + str_len

        # Every 0x02 reference must point to a valid string start
        for i in range(min_ref - 4):
            if modified[i] == 0x02:
                offset = struct.unpack_from('<I', modified, i + 1)[0]
                if min_ref <= offset < file_len:
                    assert offset in valid_starts, (
                        f"Reference at bytecode pos {i+1} points to {offset}, " f"not a valid string entry start"
                    )


def _make_mixed_ref_script():
    """Build a synthetic .o file using 0x02, 0x12, and 0x22 string refs."""
    s0 = b'fontfat'
    s1 = 'ちはや\u3000「おはよう」'.encode('cp932')
    s2 = '\u3000朝の光が差し込む。'.encode('cp932')

    entries = [s0, s1, s2]
    entry_blobs = [_make_string_entry(e) for e in entries]

    # Filler: 5 bytes of init sequence
    filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95])

    # We'll use: 0x02 (5 bytes) for s0, 0x12 (3 bytes) for s1, 0x12 (3 bytes) for s2
    # bytecode = filler(5) + ref0(5) + ref1(3) + ref2(3) = 16 bytes
    bytecode_length = len(filler) + 5 + 3 + 3

    string_offsets = []
    current = bytecode_length
    for blob in entry_blobs:
        string_offsets.append(current)
        current += len(blob)

    # All offsets must fit in uint16 for 0x12 refs
    assert all(off <= 0xFFFF for off in string_offsets)

    bytecode = bytearray(filler)
    # s0: 0x02 (4-byte offset)
    bytecode.append(0x02)
    bytecode.extend(struct.pack('<I', string_offsets[0]))
    # s1: 0x12 (2-byte offset)
    bytecode.append(0x12)
    bytecode.extend(struct.pack('<H', string_offsets[1]))
    # s2: 0x12 (2-byte offset)
    bytecode.append(0x12)
    bytecode.extend(struct.pack('<H', string_offsets[2]))

    assert len(bytecode) == bytecode_length
    string_table = b''.join(entry_blobs)
    return bytes(bytecode) + string_table


def _make_tiny_ref_script():
    """Build a very small .o file using 0x22 (1-byte offset) string refs."""
    s0 = b'abc'
    s1 = 'テスト'.encode('cp932')

    entries = [s0, s1]
    entry_blobs = [_make_string_entry(e) for e in entries]

    # Filler: minimal init
    filler = bytes([0x93, 0x95])

    # 0x22 refs are 2 bytes each
    bytecode_length = len(filler) + 2 + 2

    string_offsets = []
    current = bytecode_length
    for blob in entry_blobs:
        string_offsets.append(current)
        current += len(blob)

    assert all(off <= 0xFF for off in string_offsets)

    bytecode = bytearray(filler)
    bytecode.append(0x22)
    bytecode.append(string_offsets[0])
    bytecode.append(0x22)
    bytecode.append(string_offsets[1])

    assert len(bytecode) == bytecode_length
    string_table = b''.join(entry_blobs)
    return bytes(bytecode) + string_table


class TestMgosBytecodeWalker:
    """Tests for the bytecode walker and mixed ref types."""

    def test_walk_finds_all_ref_types(self):
        """Walker finds 0x02, 0x12, and 0x22 string refs."""
        data = _make_mixed_ref_script()
        bytecode_end, refs = Mgos._walk_bytecode(data)

        # Should find 3 refs
        assert len(refs) == 3
        # Check operand sizes
        sizes = [r[2] for r in refs]
        assert sizes == [4, 2, 2]

    def test_walk_finds_tiny_refs(self):
        """Walker finds 0x22 (1-byte) string refs."""
        data = _make_tiny_ref_script()
        bytecode_end, refs = Mgos._walk_bytecode(data)

        assert len(refs) == 2
        assert all(r[2] == 1 for r in refs)

    def test_mixed_ref_extraction(self):
        """Extraction works with mixed ref types."""
        data = _make_mixed_ref_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(data))

        # s0 is ASCII (not extracted), s1 is dialogue (name + dialogue), s2 is narration
        texts = [tl.text for tl in textlines]
        assert 'ちはや' in texts
        assert 'おはよう' in texts
        assert '朝の光が差し込む。' in texts

    def test_mixed_ref_roundtrip(self):
        """Extract + identity insert roundtrips with mixed ref types."""
        original = _make_mixed_ref_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))

        translation_dict = {tl.key: tl for tl in textlines}
        result = Mgos.insert_lines(intermediate, translation_dict)
        output = result.read()

        assert original == output

    def test_tiny_ref_roundtrip(self):
        """Extract + identity insert roundtrips with 1-byte refs."""
        original = _make_tiny_ref_script()
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))

        translation_dict = {tl.key: tl for tl in textlines}
        result = Mgos.insert_lines(intermediate, translation_dict)
        output = result.read()

        assert original == output

    def test_unquoted_name_dialogue_roundtrip(self):
        """Strings with name　dialogue but no 「」 survive roundtrip unchanged."""
        s0 = b'fontfat'
        # Unquoted: name + fullwidth space + text, but NO 「」
        s1 = '↑\u3000　メッセージ履歴画面の呼び出し'.encode('cp932')
        s2 = 'ちはや\u3000「おはよう」'.encode('cp932')  # quoted for contrast

        entries = [s0, s1, s2]
        entry_blobs = [_make_string_entry(e) for e in entries]

        filler = bytes([0x93, 0x27, 0x00, 0xC0, 0x95] * 4)
        bytecode_length = len(filler) + len(entries) * 5

        string_offsets = []
        current = bytecode_length
        for blob in entry_blobs:
            string_offsets.append(current)
            current += len(blob)

        bytecode = bytearray(filler)
        for offset in string_offsets:
            bytecode.append(0x02)
            bytecode.extend(struct.pack('<I', offset))

        original = bytes(bytecode) + b''.join(entry_blobs)
        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(original))
        trans = {t.key: t for t in textlines}
        intermediate.seek(0)
        output = Mgos.insert_lines(intermediate, trans)
        assert output.read() == original

    def test_offset_overflow_uint8(self):
        """Growing an earlier string pushes a 1-byte ref past 0xFF."""
        # Build a file where: s0 (extractable, via 0x02) then s1 (via 0x22).
        # If s0's translation grows large, s1's offset exceeds uint8 range.
        s0 = 'テスト'.encode('cp932')  # extractable (has Japanese)
        s1 = 'データ'.encode('cp932')  # extractable too

        entry_blobs = [_make_string_entry(s0), _make_string_entry(s1)]
        filler = bytes([0x93, 0x95])

        # ref0: 0x02 (5 bytes) for s0, ref1: 0x22 (2 bytes) for s1
        bytecode_length = len(filler) + 5 + 2

        string_offsets = []
        current = bytecode_length
        for blob in entry_blobs:
            string_offsets.append(current)
            current += len(blob)

        assert string_offsets[1] <= 0xFF  # s1 fits in uint8 initially

        bytecode = bytearray(filler)
        bytecode.append(0x02)
        bytecode.extend(struct.pack('<I', string_offsets[0]))
        bytecode.append(0x22)
        bytecode.append(string_offsets[1])

        assert len(bytecode) == bytecode_length
        data = bytes(bytecode) + b''.join(entry_blobs)

        intermediate, textlines, _ = Mgos.extract_lines(BytesIO(data))

        # Find the first textline (for s0) and make it huge
        translation_dict = {tl.key: tl for tl in textlines}
        first_key = textlines[0].key
        translation_dict[first_key] = TextLine(first_key, 'あ' * 200, b'')

        import pytest

        with pytest.raises(ValueError, match="exceeds uint8 range"):
            Mgos.insert_lines(intermediate, translation_dict)
