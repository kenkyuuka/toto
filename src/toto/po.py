"""Minimal gettext PO reading and writing for translation exchange.

Only what toto needs is supported: one entry per text line, keyed by ``msgctxt``, with
extracted comments for metadata. The reader is more lenient than the writer, because target
files come back from translation tools: it accepts multi-line strings, flags, comments, CRLF
line endings and plural or obsolete entries (which it skips).
"""

from collections import namedtuple

PoEntry = namedtuple('PoEntry', ['msgctxt', 'msgid', 'msgstr', 'line'])

HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Content-Transfer-Encoding: 8bit\\n"\n'

_ESCAPES = {'\\': '\\\\', '"': '\\"', '\n': '\\n', '\t': '\\t', '\r': '\\r'}
_UNESCAPES = {
    '\\': '\\',
    '"': '"',
    'n': '\n',
    't': '\t',
    'r': '\r',
    'a': '\a',
    'b': '\b',
    'f': '\f',
    'v': '\v',
}


class PoError(ValueError):
    pass


def escape(text):
    """Escape *text* for use inside a double-quoted PO string."""
    return ''.join(_ESCAPES.get(c, c) for c in text)


def unescape(text):
    """Reverse :func:`escape`. An unknown escape such as ``\\q`` yields the bare character."""
    out = []
    chars = iter(text)
    for c in chars:
        if c == '\\':
            nxt = next(chars, '')
            out.append(_UNESCAPES.get(nxt, nxt))
        else:
            out.append(c)
    return ''.join(out)


def source_text(textline):
    """The string to translate for *textline*, exactly as the txt output writes it."""
    return textline.text.replace('\n', '\\n')


def _comments(textline):
    """Extracted-comment lines (without the ``#. `` prefix) describing *textline*."""
    comments = []
    speaker = getattr(textline, 'speaker', None)
    if speaker:
        comments.append(f'Speaker: {speaker}')
    return comments


def write_po(f, textlines):
    """Write *textlines* to the text stream *f* as a PO file.

    A line with empty text is left out: an entry with an empty ``msgid`` would be read as the
    header. Insertion then keeps its (empty) source text, as the txt format does for an
    untranslated empty line.
    """
    f.write(HEADER)
    for t in textlines:
        if t.text == '':
            continue
        f.write('\n')
        for comment in _comments(t):
            # A comment ends at the line break, so keep it on one line.
            f.write('#. ' + comment.replace('\r', ' ').replace('\n', ' ') + '\n')
        f.write(f'msgctxt "{escape(t.key)}"\n')
        f.write(f'msgid "{escape(source_text(t))}"\n')
        f.write('msgstr ""\n')


def _quoted(s, lineno):
    s = s.strip()
    if len(s) < 2 or s[0] != '"' or s[-1] != '"':
        raise PoError(f'line {lineno}: expected a quoted string, got {s!r}')
    return unescape(s[1:-1])


def read_po(f):
    """Read the PO text stream *f* and return its entries, header excluded, as :class:`PoEntry`.

    Plural entries and obsolete (``#~``) entries are skipped.
    """
    entries = []
    fields = {}
    current = None  # field the next continuation string belongs to
    start = None

    def finish():
        nonlocal fields, current, start
        if 'msgid' in fields and 'msgid_plural' not in fields:
            msgctxt = fields.get('msgctxt')
            msgid = fields['msgid']
            if not (msgctxt is None and msgid == ''):  # the header
                entries.append(PoEntry(msgctxt, msgid, fields.get('msgstr', ''), start))
        fields, current, start = {}, None, None

    # Split on LF only: str.splitlines() would also split on characters such as U+2028
    # that may appear raw in game text.
    for lineno, raw_line in enumerate(f.read().replace('\r\n', '\n').split('\n'), 1):
        line = raw_line.strip()
        if not line:
            finish()
            continue
        if line.startswith('#'):
            # A comment after a complete entry belongs to the next one.
            if 'msgstr' in fields or current == 'msgstr[]':
                finish()
            continue
        if line.startswith('"'):
            if current is None:
                raise PoError(f'line {lineno}: string continuation outside an entry')
            if current != 'msgstr[]':
                fields[current] += _quoted(line, lineno)
            continue
        keyword, _, rest = line.partition(' ')
        if keyword.startswith('msgstr['):
            fields['msgid_plural'] = fields.get('msgid_plural', '')
            current = 'msgstr[]'
            continue
        if keyword not in ('msgctxt', 'msgid', 'msgid_plural', 'msgstr'):
            raise PoError(f'line {lineno}: unknown keyword {keyword!r}')
        # A new msgctxt or msgid after a complete entry starts the next entry.
        if keyword in ('msgctxt', 'msgid') and ('msgstr' in fields or current == 'msgstr[]'):
            finish()
        if keyword in fields:
            raise PoError(f'line {lineno}: duplicate {keyword}')
        if start is None:
            start = lineno
        fields[keyword] = _quoted(rest, lineno)
        current = keyword
    finish()
    return entries
