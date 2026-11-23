"""Unit tests for the minimal PO reader and writer."""

import io

import pytest

from toto.po import HEADER, PoEntry, PoError, escape, read_po, unescape, write_po
from toto.util import TextLine


def _write(textlines):
    f = io.StringIO()
    write_po(f, textlines)
    return f.getvalue()


def _read(text):
    return [(e.msgctxt, e.msgid, e.msgstr) for e in read_po(io.StringIO(text))]


@pytest.mark.unit
@pytest.mark.parametrize(
    'text',
    [
        'Alice said "Curiouser and curiouser!"',
        'C:\\rabbit\\hole',
        'Hatter\tMad',
        'a literal \\n from the source',
        'carriage\rreturn',
        '\\"tricky\\\\',
        '',
    ],
)
def test_escape_roundtrip(text):
    assert unescape(escape(text)) == text
    assert '\n' not in escape(text) and '\r' not in escape(text) and '\t' not in escape(text)


@pytest.mark.unit
def test_unescape_known_and_unknown_escapes():
    assert unescape('\\a\\b\\f\\v') == '\a\b\f\v'
    assert unescape('\\q\\') == 'q'


@pytest.mark.unit
def test_write_entry_layout():
    lines = [
        TextLine('<<<TRANS:0>>>', 'Alice fell down the rabbit hole.', b''),
        TextLine('<<<TRANS:1>>>', '「Off with\nher head!」', b'', speaker='ハートの女王'),
    ]
    assert _write(lines) == (
        HEADER + '\n'
        'msgctxt "<<<TRANS:0>>>"\n'
        'msgid "Alice fell down the rabbit hole."\n'
        'msgstr ""\n'
        '\n'
        '#. Speaker: ハートの女王\n'
        'msgctxt "<<<TRANS:1>>>"\n'
        'msgid "「Off with\\\\nher head!」"\n'
        'msgstr ""\n'
    )


@pytest.mark.unit
def test_write_skips_empty_text():
    text = _write([TextLine('k0', '', b''), TextLine('k1', 'Drink me', b'')])
    assert 'k0' not in text
    assert _read(text) == [('k1', 'Drink me', '')]


@pytest.mark.unit
def test_write_keeps_comments_on_one_line():
    text = _write([TextLine('k0', 'Eat me', b'', speaker='White\nRabbit')])
    assert '#. Speaker: White Rabbit\n' in text


@pytest.mark.unit
@pytest.mark.parametrize(
    'text',
    [
        'Alice said "Curiouser and curiouser!"',
        'Hatter\tMad',
        'a real\nnewline',
        'a literal \\n from the source',
        'trailing backslash \\',
    ],
)
def test_write_read_roundtrip(text):
    """A msgid reads back as the txt output's line for the same text."""
    (entry,) = read_po(io.StringIO(_write([TextLine('<<<TRANS:3>>>', text, b'')])))
    assert entry.msgctxt == '<<<TRANS:3>>>'
    assert entry.msgid == text.replace('\n', '\\n')


@pytest.mark.unit
def test_read_multiline_strings_flags_and_comments():
    text = (
        '# translator comment\n'
        'msgid ""\n'
        'msgstr ""\n'
        '"Content-Type: text/plain; charset=UTF-8\\n"\n'
        '\n'
        '#. Speaker: アリス\n'
        '#: somewhere:12\n'
        '#, fuzzy\n'
        'msgctxt "<<<TRANS:0>>>"\n'
        'msgid ""\n'
        '"Who are "\n'
        '"you?"\n'
        'msgstr ""\n'
        '"Who are "\n'
        '"YOU?"\n'
        '\n'
        'msgctxt "<<<TRANS:1>>>"\n'
        'msgid "Tea?"\n'
        'msgstr ""\n'
    )
    assert _read(text) == [('<<<TRANS:0>>>', 'Who are you?', 'Who are YOU?'), ('<<<TRANS:1>>>', 'Tea?', '')]


@pytest.mark.unit
@pytest.mark.parametrize('blank_after_header', [True, False])
@pytest.mark.parametrize('newline', ['\n', '\r\n'], ids=['lf', 'crlf'])
def test_read_header_spacing_and_crlf(blank_after_header, newline):
    text = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'
    if blank_after_header:
        text += '\n'
    text += 'msgctxt "k"\nmsgid "Tea?"\nmsgstr "Thé ?"\n'
    assert _read(text.replace('\n', newline)) == [('k', 'Tea?', 'Thé ?')]


@pytest.mark.unit
def test_read_entries_without_blank_lines_between():
    text = 'msgctxt "a"\nmsgid "Eat me"\nmsgstr "x"\n#. note\nmsgctxt "b"\nmsgid "Drink me"\nmsgstr "y"\n'
    assert _read(text) == [('a', 'Eat me', 'x'), ('b', 'Drink me', 'y')]


@pytest.mark.unit
def test_read_does_not_split_on_unicode_line_separators():
    text = 'msgctxt "k"\nmsgid "Alice\u2028Rabbit\x85Hatter\x0c"\nmsgstr ""\n'
    assert _read(text) == [('k', 'Alice\u2028Rabbit\x85Hatter\x0c', '')]


@pytest.mark.unit
def test_read_skips_plural_and_obsolete_entries():
    text = (
        'msgctxt "p"\nmsgid "one tart"\nmsgid_plural "%d tarts"\nmsgstr[0] "a"\nmsgstr[1] "b"\n\n'
        '#~ msgctxt "old"\n#~ msgid "Gone"\n#~ msgstr "Parti"\n\n'
        'msgctxt "k"\nmsgid "Tea?"\nmsgstr ""\n'
    )
    assert _read(text) == [('k', 'Tea?', '')]


@pytest.mark.unit
def test_read_records_entry_line():
    (entry,) = read_po(io.StringIO('msgid ""\nmsgstr ""\n\n#. x\nmsgctxt "k"\nmsgid "a"\nmsgstr ""\n'))
    assert entry == PoEntry('k', 'a', '', 5)


@pytest.mark.unit
@pytest.mark.parametrize(
    'text',
    [
        'msgid "unterminated\nmsgstr ""\n',
        '"orphan continuation"\n',
        'msgid "a"\nmsgid "b"\nmsgstr ""\n',
        'msgfoo "a"\n',
    ],
)
def test_read_rejects_malformed(text):
    with pytest.raises(PoError):
        read_po(io.StringIO(text))
