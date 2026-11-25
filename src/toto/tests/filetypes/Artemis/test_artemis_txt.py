"""Tests for the Artemis Engine KAG-style ``.txt`` scenario handler."""

import logging
from io import BytesIO

import pytest

from toto.filetypes.ArtemisTxt import ArtemisTxt, display_width, wrap_columns

HEADER = '[&autoinsert target="lineend"   command="[行末]"]\n[&autoinsert target="blankline" command="[空行]"]\n'

SCRIPT = (
    HEADER
    + '[title text="不思議の国"]\n'
    + '\n'
    + '// 背景：川辺\n'
    + '[川辺 昼 y="-120"]\n'
    + '\n'
    + '　アリスは土手で姉の隣に座っていた。\n'
    + '\n'
    + '[name アリス alc_0001]\n'
    + '「白ウサギを見なかった？\n'
    + '時計を持ってたの」\n'
    + '\n'
    + '[name 白ウサギ]\n'
    + '「遅刻だ、遅刻だ！」\n'
    + '\n'
    + '[sel_init name="select1" flag="normal"]\n'
    + '[sel_text label="分岐A" text="追いかける" exp=""]\n'
    + '[sel_text label="分岐B" text="昼寝する" exp=""]\n'
    + '[select]\n'
    + '\n'
    + '*分岐A\n'
    + '　アリスは穴に落ちた。\n'
)


def sample(text=SCRIPT, newline='\r\n', codec='cp932'):
    return text.replace('\n', newline).encode(codec)


def extract(data, **kwargs):
    intermediate, lines, metadata = ArtemisTxt.extract_lines(BytesIO(data), **kwargs)
    return intermediate, lines, metadata


def insert(intermediate, lines, translations=None, width=ArtemisTxt.default_width, **metadata):
    trans = {t.key: t for t in lines}
    for key, text in (translations or {}).items():
        trans[key] = trans[key]._replace(text=text)
    intermediate.seek(0)
    return ArtemisTxt.insert_lines(intermediate, trans, width=width, **metadata).read()


def keyed(lines):
    return {t.text: t.key for t in lines}


# --------------------------------------------------------------------------- #
# Extraction
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_extracts_messages_choices_and_titles():
    _, lines, _ = extract(sample())
    assert [(t.text, t.speaker) for t in lines] == [
        ('不思議の国', None),
        ('　アリスは土手で姉の隣に座っていた。', None),
        ('「白ウサギを見なかった？時計を持ってたの」', 'アリス'),
        ('「遅刻だ、遅刻だ！」', '白ウサギ'),
        ('追いかける', None),
        ('昼寝する', None),
        ('　アリスは穴に落ちた。', None),
    ]


@pytest.mark.unit
def test_no_unwrap_keeps_line_breaks():
    _, lines, _ = extract(sample(), unwrap=False)
    assert '「白ウサギを見なかった？\n時計を持ってたの」' in [t.text for t in lines]


@pytest.mark.unit
def test_unwrap_puts_space_between_ascii_words_only():
    data = sample(HEADER + 'Alice was\nvery tired.\n「えっと\nAlice」\n')
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ['Alice was very tired.「えっとAlice」']


@pytest.mark.unit
def test_speaker_applies_to_next_message_only():
    data = sample(HEADER + '[name アリス]\n「はい」\n\n　ナレーション。\n')
    _, lines, _ = extract(data)
    assert [t.speaker for t in lines] == ['アリス', None]


@pytest.mark.unit
@pytest.mark.parametrize(
    'line',
    [
        '// コメント',
        '; コメント',
        '*ラベル',
        '@',
        '[bg file="川辺"]',
        '[se file="se004"]  // 鈴の音',
        '  [wait 30]',
    ],
)
def test_command_lines_are_not_text(line):
    _, lines, _ = extract(sample(HEADER + line + '\n'))
    assert lines == []


@pytest.mark.unit
def test_block_comments_are_skipped():
    data = sample(HEADER + '/*\n消えたチェシャ猫\n*/\n本文\n/* 一行 */\n')
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ['本文']


@pytest.mark.unit
def test_lua_blocks_are_skipped():
    data = sample(HEADER + '[lua]\nlocal hatter = "mad"\n[/lua]\n帽子屋\n')
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ['帽子屋']


@pytest.mark.unit
def test_linetag_prefix_lines_are_commands():
    data = sample(HEADER + '#bg file="庭"\n[&linetag allow="1" prefix="#"]\n#bg file="庭"\n薔薇\n')
    _, lines, _ = extract(data)
    # Before the directive, a '#' line is ordinary text.
    assert [t.text for t in lines] == ['#bg file="庭"', '薔薇']


@pytest.mark.unit
def test_inline_tags_stay_in_text():
    data = sample(HEADER + '「おい、[ruby text="や"]飲[/ruby]るか？」\n')
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ['「おい、[ruby text="や"]飲[/ruby]るか？」']


@pytest.mark.unit
def test_tag_with_bracket_in_quoted_value_is_a_tag_line():
    data = sample(HEADER + '[eval exp="a[0]=1"]\n')
    _, lines, _ = extract(data)
    assert lines == []


@pytest.mark.unit
def test_ignore_patterns():
    import re

    _, lines, _ = extract(sample(), ignore_patterns=[re.compile('遅刻')])
    assert '「遅刻だ、遅刻だ！」' not in [t.text for t in lines]


@pytest.mark.unit
def test_rejects_non_ascii_compatible_codec():
    with pytest.raises(ValueError, match='ASCII'):
        extract(sample(codec='utf-16-le'), codec='utf-16-le')


# --------------------------------------------------------------------------- #
# Insertion
# --------------------------------------------------------------------------- #


@pytest.mark.unit
@pytest.mark.parametrize('unwrap', [True, False])
@pytest.mark.parametrize('newline', ['\r\n', '\n'])
def test_identity_roundtrip_is_byte_exact(unwrap, newline):
    data = sample(newline=newline)
    intermediate, lines, metadata = extract(data, unwrap=unwrap)
    assert insert(intermediate, lines, **metadata) == data


@pytest.mark.unit
def test_identity_roundtrip_keeps_cp932_duplicate_encodings():
    # 0xFA 0x55 (IBM ￤) re-encodes to 0xEE 0xFA in Python's cp932.
    raw = sample(HEADER) + b'\x81\x75\xfa\x55\x81\x76\r\n'
    intermediate, lines, metadata = extract(raw)
    assert insert(intermediate, lines, **metadata) == raw


@pytest.mark.unit
def test_translated_message_is_wrapped_into_source_lines():
    intermediate, lines, metadata = extract(sample())
    key = keyed(lines)['「白ウサギを見なかった？時計を持ってたの」']
    english = '"Have you seen the White Rabbit? He had a pocket watch, and he was terribly late."'
    out = insert(intermediate, lines, {key: english}, width=50, **metadata).decode('cp932')
    assert (
        '[name アリス alc_0001]\r\n'
        '"Have you seen the White Rabbit? He had a pocket\r\n'
        'watch, and he was terribly late."\r\n'
        '\r\n'
        '[name 白ウサギ]\r\n'
    ) in out


@pytest.mark.unit
def test_explicit_breaks_are_kept_and_blank_lines_dropped():
    intermediate, lines, metadata = extract(sample())
    key = keyed(lines)['「遅刻だ、遅刻だ！」']
    out = insert(intermediate, lines, {key: 'Oh dear!\\n\\nOh dear!'}, **metadata).decode('cp932')
    assert '[name 白ウサギ]\r\nOh dear!\r\nOh dear!\r\n\r\n' in out


@pytest.mark.unit
def test_choice_and_title_values_are_replaced_in_place():
    intermediate, lines, metadata = extract(sample())
    k = keyed(lines)
    out = insert(
        intermediate, lines, {k['追いかける']: 'Follow the rabbit', k['不思議の国']: 'Wonderland'}, **metadata
    ).decode('cp932')
    assert '[sel_text label="分岐A" text="Follow the rabbit" exp=""]\r\n' in out
    assert '[title text="Wonderland"]\r\n' in out


@pytest.mark.unit
def test_double_quote_in_attribute_is_replaced(caplog):
    intermediate, lines, metadata = extract(sample())
    key = keyed(lines)['追いかける']
    with caplog.at_level(logging.WARNING):
        out = insert(intermediate, lines, {key: 'Say "hello"'}, **metadata).decode('cp932')
    assert "text=\"Say 'hello'\"" in out
    assert 'double quote' in caplog.text


@pytest.mark.unit
def test_full_width_counts_two_columns():
    assert display_width('アリス') == 6
    assert display_width('Alice') == 5
    assert display_width('[ruby text="や"]飲[/ruby]') == 2
    assert wrap_columns('ああああ ああ', 8) == ['ああああ', 'ああ']


@pytest.mark.unit
def test_wrapping_keeps_inline_tags_whole():
    text = 'The [ruby text="March Hare"]Hare[/ruby] poured tea.'
    assert wrap_columns(text, 10) == ['The [ruby text="March Hare"]Hare[/ruby]', 'poured', 'tea.']


@pytest.mark.unit
def test_overflow_warning(caplog):
    intermediate, lines, metadata = extract(sample())
    key = keyed(lines)['「遅刻だ、遅刻だ！」']
    long = ' '.join(['Oh my ears and whiskers, how late it is getting!'] * 5)
    with caplog.at_level(logging.WARNING):
        insert(intermediate, lines, {key: long}, width=50, **metadata)
    assert 'lines' in caplog.text and 'Oh my ears' in caplog.text


@pytest.mark.unit
@pytest.mark.parametrize('start', ['*sigh*', '; well', '// so', '@'])
def test_warns_about_lines_that_would_parse_as_commands(caplog, start):
    intermediate, lines, metadata = extract(sample())
    key = keyed(lines)['「遅刻だ、遅刻だ！」']
    with caplog.at_level(logging.WARNING):
        insert(intermediate, lines, {key: f'{start} I am late.'}, **metadata)
    assert 'command' in caplog.text


@pytest.mark.unit
def test_unencodable_translation_is_an_error():
    intermediate, lines, metadata = extract(sample())
    key = keyed(lines)['「遅刻だ、遅刻だ！」']
    with pytest.raises(UnicodeEncodeError):
        insert(intermediate, lines, {key: 'Late 🐇'}, **metadata)


@pytest.mark.unit
def test_utf8_scripts():
    data = sample(codec='utf-8')
    intermediate, lines, metadata = extract(data, codec='utf-8')
    key = keyed(lines)['「遅刻だ、遅刻だ！」']
    out = insert(intermediate, lines, {key: 'Late 🐇'}, **metadata)
    assert 'Late 🐇\r\n'.encode() in out


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #


@pytest.mark.integration
def test_get_paths_finds_nested_txt(tmp_path):
    (tmp_path / '00_com').mkdir()
    (tmp_path / '00_com' / 's001a.txt').write_bytes(sample())
    (tmp_path / 'start.txt').write_bytes(sample())
    (tmp_path / 'readme.md').write_text('x')
    assert sorted(p.relative_to(tmp_path).as_posix() for p in ArtemisTxt.get_paths(tmp_path)) == [
        '00_com/s001a.txt',
        'start.txt',
    ]


@pytest.mark.unit
@pytest.mark.parametrize('codec', ['cp932', 'utf-8'])
def test_detects_encoding_of_short_scripts(codec):
    _, lines, metadata = extract(sample(HEADER + '帽子屋\n', codec=codec))
    assert metadata['codec'] == codec
    assert [t.text for t in lines] == ['帽子屋']


@pytest.mark.unit
def test_utf8_bom_is_kept():
    data = b'\xef\xbb\xbf' + sample('帽子屋\n', codec='utf-8')
    intermediate, lines, metadata = extract(data)
    assert [t.text for t in lines] == ['帽子屋']
    out = insert(intermediate, lines, {lines[0].key: 'The Hatter'}, **metadata)
    assert out == b'\xef\xbb\xbfThe Hatter\r\n'
