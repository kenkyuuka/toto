"""Tests for the Liar-soft GSC format handler."""

import struct
from io import BytesIO

import pytest

from toto.filetypes.LiarSoftGsc import GscScript, LiarSoftGsc, build_gsc, parse_gsc
from toto.util import TextLine

# ------------------------------------------------------------------ helpers --


def _message(name_idx, text_idx, voice=0):
    """Build a 0x51 show-message instruction."""
    return struct.pack('<H7I', 0x51, 0, voice, 0, 0, name_idx, text_idx, 1)


def _table(entries):
    """Build an (index, data) pair for a list of byte strings."""
    index = bytearray()
    data = bytearray()
    for e in entries:
        index += struct.pack('<I', len(data))
        data += e + b'\0'
    return bytes(index), bytes(data)


def _make_gsc(strings, code=None, t2_records=(b'',), labels=((b'', 0),)):
    """Build a complete GSC file by hand, independently of build_gsc."""
    encoded = [s.encode('cp932') for s in strings]
    if code is None:
        code = b''.join(_message(i, i + 1) for i in range(1, len(encoded) - 1, 2))
    t1_index, t1_data = _table(encoded)
    t2_index, t2_data = _table(list(t2_records))
    t3_name_index, t3_names = _table([name for name, _ in labels])
    t3_offsets = b''.join(struct.pack('<I', off) for _, off in labels)
    body = code + t1_index + t1_data + t2_index + t2_data + t2_data + t3_name_index + t3_offsets + t3_names
    header = struct.pack(
        '<9I',
        36 + len(body),
        36,
        len(code),
        len(t1_index),
        len(t1_data),
        len(t2_index),
        len(t2_data),
        len(t3_name_index),
        len(t3_names),
    )
    return header + body


ALICE_STRINGS = [
    '',
    'アリス',
    'あら、白ウサギさん！　そんなに急いでどこへ行くの？',
    '白ウサギ',
    'たいへんだ、たいへんだ！　遅刻しちゃう！',
    'grpo_bg',
    'grpe',
    '穴はどこまでも深く続いていた。^nアリスはゆっくりと落ちていく。',
]


def _strings_of(data):
    return [s.decode('cp932') for s in parse_gsc(data).strings]


def _extract(data, **kwargs):
    intermediate, textlines, metadata = LiarSoftGsc.extract_lines(BytesIO(data), **kwargs)
    return intermediate.read(), textlines, metadata


def _insert(intermediate, translations, **kwargs):
    trans = {k: TextLine(k, v, b'') for k, v in translations.items()}
    return LiarSoftGsc.insert_lines(BytesIO(intermediate), trans, **kwargs).read()


# ------------------------------------------------------------ parse/build --


@pytest.mark.unit
def test_parse_reads_string_table():
    data = _make_gsc(ALICE_STRINGS)
    assert _strings_of(data) == ALICE_STRINGS


@pytest.mark.unit
def test_parse_keeps_code_and_tables_opaque():
    code = _message(1, 2, voice=7) + _message(3, 4)
    data = _make_gsc(
        ALICE_STRINGS,
        code=code,
        t2_records=(b'', b'\x41\x1f\xff\xff'),
        labels=((b'', 0), (b'REP001', 30)),
    )
    script = parse_gsc(data)
    assert script.code == code
    assert script.t2_index == 8
    assert script.t2_data == 6
    assert len(script.t2) == 8 + 2 * 6
    assert script.t3.endswith(b'\0REP001\0')


@pytest.mark.unit
@pytest.mark.parametrize(
    'labels',
    [((b'', 0),), ((b'', 0), (b'TOP1', 0x10), (b'click', 0x20))],
    ids=['no-labels', 'labels'],
)
def test_build_is_identity(labels):
    data = _make_gsc(ALICE_STRINGS, t2_records=(b'', b'\x01\x02\x03'), labels=labels)
    assert build_gsc(parse_gsc(data)) == data


@pytest.mark.unit
def test_build_updates_sizes():
    script = parse_gsc(_make_gsc(ALICE_STRINGS))
    script.strings[2] = b'Oh, Mr. White Rabbit! Where are you going in such a hurry?'
    out = build_gsc(script)
    file_size, _, _, t1_index, t1_data = struct.unpack_from('<5I', out)
    assert file_size == len(out)
    assert t1_index == 4 * len(ALICE_STRINGS)
    assert t1_data == sum(len(s) + 1 for s in script.strings)
    assert parse_gsc(out).strings == script.strings


@pytest.mark.unit
@pytest.mark.parametrize(
    'mutate',
    [
        lambda d: d[:-1],
        lambda d: struct.pack('<I', len(d) + 1) + d[4:],
        lambda d: d[:4] + struct.pack('<I', 32) + d[8:],
        lambda _d: b'\0' * 10,
    ],
    ids=['truncated', 'bad-file-size', 'bad-header-size', 'too-short'],
)
def test_parse_rejects_invalid(mutate):
    with pytest.raises(ValueError):
        parse_gsc(mutate(_make_gsc(ALICE_STRINGS)))


# ---------------------------------------------------------------- extract --


@pytest.mark.unit
def test_extract_selects_text_strings_only():
    data = _make_gsc(ALICE_STRINGS)
    intermediate, textlines, _ = _extract(data)
    assert [t.text for t in textlines] == [ALICE_STRINGS[i] for i in (1, 2, 3, 4, 7)]
    assert [t.key for t in textlines] == [f'<<<TRANS:{i}>>>' for i in range(5)]
    assert _strings_of(intermediate) == [
        '',
        '<<<TRANS:0>>>',
        '<<<TRANS:1>>>',
        '<<<TRANS:2>>>',
        '<<<TRANS:3>>>',
        'grpo_bg',
        'grpe',
        '<<<TRANS:4>>>',
    ]


@pytest.mark.unit
def test_extract_ignore_patterns():
    import re

    _, textlines, _ = _extract(_make_gsc(ALICE_STRINGS), ignore_patterns=[re.compile('^白ウサギ$')])
    assert '白ウサギ' not in [t.text for t in textlines]
    assert len(textlines) == 4


@pytest.mark.unit
def test_extract_unwrap_removes_line_breaks():
    _, textlines, _ = _extract(_make_gsc(ALICE_STRINGS), unwrap=True)
    assert textlines[-1].text == '穴はどこまでも深く続いていた。アリスはゆっくりと落ちていく。'


@pytest.mark.unit
def test_extract_records_cp932_fixup():
    # U+2252 (≒) is 0x81E0 in NEC form and 0x8790 in this file's variant.
    strings = ['', 'アリス', 'ケーキ≒クッキー']
    data = bytearray(_make_gsc(strings))
    data = bytes(data).replace('≒'.encode('cp932'), b'\x87\x90')
    _, _, metadata = _extract(data)
    assert metadata['cp932_fixup'] == {b'\x81\xe0': b'\x87\x90'}


# ----------------------------------------------------------------- insert --


@pytest.mark.unit
def test_identity_roundtrip():
    data = _make_gsc(ALICE_STRINGS, labels=((b'', 0), (b'REP001', 0x1E)))
    intermediate, textlines, metadata = _extract(data)
    out = _insert(intermediate, {t.key: t.text for t in textlines}, **metadata)
    assert out == data


@pytest.mark.unit
def test_insert_translation_changes_lengths():
    code = _message(1, 2) + _message(3, 4)
    data = _make_gsc(ALICE_STRINGS, code=code, t2_records=(b'', b'\x41\x1f'), labels=((b'', 0), (b'REP001', 30)))
    intermediate, textlines, _ = _extract(data)
    translations = {
        '<<<TRANS:0>>>': 'Alice',
        '<<<TRANS:1>>>': 'Oh, Mr. White Rabbit! Where are you going in such a hurry?',
        '<<<TRANS:2>>>': 'White Rabbit',
        '<<<TRANS:3>>>': "Oh dear! Oh dear! I shall be too late!",
        '<<<TRANS:4>>>': 'Down, down, down.',
    }
    out = _insert(intermediate, translations)

    original, result = parse_gsc(data), parse_gsc(out)
    assert result.code == original.code
    assert result.t2 == original.t2
    assert result.t3 == original.t3
    assert [s.decode('cp932') for s in result.strings] == [
        '',
        'Alice',
        'Oh, Mr. White Rabbit! Where are you going in such a hurry?',
        'White Rabbit',
        'Oh dear! Oh dear! I shall be too late!',
        'grpo_bg',
        'grpe',
        'Down, down, down.',
    ]


@pytest.mark.unit
def test_insert_wraps_with_line_break_markup():
    intermediate, _, _ = _extract(_make_gsc(['', 'アリス', 'ケーキを食べた。']))
    out = _insert(
        intermediate,
        {'<<<TRANS:0>>>': 'Alice', '<<<TRANS:1>>>': 'Alice ate the little cake marked EAT ME and grew'},
        width=20,
    )
    assert _strings_of(out)[2] == 'Alice ate the little^ncake marked EAT ME^nand grew'


@pytest.mark.unit
def test_insert_keeps_manual_line_breaks():
    intermediate, _, _ = _extract(_make_gsc(['', 'アリス', 'ケーキを食べた。']))
    text = 'Alice ate the cake^nmarked EAT ME, and she grew and grew'
    out = _insert(intermediate, {'<<<TRANS:0>>>': 'Alice', '<<<TRANS:1>>>': text}, width=20)
    assert _strings_of(out)[2] == text


@pytest.mark.unit
def test_insert_does_not_wrap_untranslated_japanese():
    long_line = 'アリスは土手の上で姉さんのそばに座っていたが、何もすることがなくて退屈しはじめていた。'
    data = _make_gsc(['', 'アリス', long_line])
    intermediate, textlines, _ = _extract(data)
    out = _insert(intermediate, {t.key: t.text for t in textlines}, width=20)
    assert out == data


@pytest.mark.unit
def test_insert_applies_cp932_fixup():
    intermediate, _, _ = _extract(_make_gsc(['', 'アリス', 'ケーキ']))
    out = _insert(
        intermediate,
        {'<<<TRANS:0>>>': 'Alice', '<<<TRANS:1>>>': 'cake≒cookie'},
        cp932_fixup={b'\x81\xe0': b'\x87\x90'},
    )
    assert parse_gsc(out).strings[2] == b'cake\x87\x90cookie'


@pytest.mark.unit
def test_insert_missing_translation_raises():
    intermediate, _, _ = _extract(_make_gsc(['', 'アリス', 'ケーキ']))
    with pytest.raises(KeyError):
        _insert(intermediate, {'<<<TRANS:0>>>': 'Alice'})


@pytest.mark.integration
def test_get_paths(tmp_path):
    (tmp_path / 'scr').mkdir()
    (tmp_path / 'scr' / '1010.gsc').write_bytes(b'')
    (tmp_path / 'notes.txt').write_bytes(b'')
    assert LiarSoftGsc.get_paths(tmp_path) == [tmp_path / 'scr' / '1010.gsc']


@pytest.mark.unit
def test_gsc_script_dataclass_roundtrip():
    script = GscScript(b'', [b''], b'\0' * 6, b'\0' * 9, 4, 1, 4, 1)
    assert parse_gsc(build_gsc(script)) == script
