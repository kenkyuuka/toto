"""Artemis Engine KAG-style text scenario scripts (``scenario/**/*.txt``), as unpacked from ``.pfs``.

A script is a sequence of lines. Tags are in square brackets, ``*name`` is a label, ``//`` and
``;`` start comments and ``/* ... */`` spans lines; every other line is text. Through the
``&autoinsert`` directives at the top of each script, each line of text ends in a line break and
a blank line ends the page, so a message is a run of consecutive text lines, and inserted text
must be wrapped into source lines of its own.

Speaker names in ``[name ...]`` select the name window's image, so they are recorded as the
speaker of the next message but left untouched. Choice (``[sel_text text=...]``) and save title
(``[title text=...]``) values are translated in place.
"""

import json
import logging
import re
import struct
import unicodedata
from io import BytesIO
from pathlib import Path

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

ASCII_SPACE = ' \t'
UTF8_BOM = b'\xef\xbb\xbf'
INTERMEDIATE_MAGIC = b'TOTOARTM'

_TAG = r'\[(?:"[^"]*"|[^\]"])*\]'
_TAG_RE = re.compile(_TAG)
_TAG_ONLY_RE = re.compile(rf'(?:[ \t]*{_TAG})+[ \t]*(?://.*)?')
_TAG_NAME_RE = re.compile(r'\[\s*([^\s\]="]+)')
_TAG_ARG_RE = re.compile(r'\s*(?:(?P<key>[^\s="\]]+)\s*=\s*)?(?:"(?P<quoted>[^"]*)"|(?P<bare>[^\s"\]]+))')
_INLINE_TOKEN_RE = re.compile(rf'(?P<tag>{_TAG})|(?P<space>[ \t]+)|(?P<text>[^ \t\[]+|\[)')

COMMAND_STARTS = ('//', '/*', ';', '*', '@')
# Tags whose ``text`` attribute is shown to the player: choice buttons and save titles.
TEXT_ATTR_TAGS = frozenset({'sel_text', 'title'})


def display_width(text):
    """Columns *text* takes on screen: 2 per full-width character, 1 otherwise; tags take none."""
    width = 0
    for m in _INLINE_TOKEN_RE.finditer(text):
        if m.lastgroup != 'tag':
            width += sum(2 if unicodedata.east_asian_width(c) in 'WF' else 1 for c in m.group())
    return width


def wrap_columns(text, width):
    """Break *text* at spaces into lines of at most *width* columns.

    Inline tags count as zero width and stay attached to the word they touch. A word wider than
    *width* is left whole.
    """
    words = []  # (word, width)
    word = ''
    for m in _INLINE_TOKEN_RE.finditer(text.strip(ASCII_SPACE)):
        if m.lastgroup == 'space':
            if word:
                words.append((word, display_width(word)))
                word = ''
        else:
            word += m.group()
    if word:
        words.append((word, display_width(word)))

    lines, line, line_width = [], [], 0
    for word, w in words:
        if line and line_width + 1 + w > width:
            lines.append(' '.join(line))
            line, line_width = [], 0
        line_width += w + (1 if line else 0)
        line.append(word)
    if line:
        lines.append(' '.join(line))
    return lines


def _is_word_char(ch):
    return ch.isascii() and not ch.isspace()


def join_lines(lines):
    """Join a message's lines into one string, with a space only between two ASCII words."""
    out = lines[0]
    for line in lines[1:]:
        if out and line and _is_word_char(out[-1]) and _is_word_char(line[0]):
            out += ' '
        out += line
    return out


def tag_args(tag):
    """``(name, [(key or None, value, (start, end)), ...])`` for a tag; spans index into *tag*."""
    m = _TAG_NAME_RE.match(tag)
    if not m:
        return None, []
    args = []
    pos = m.end()
    end = len(tag) - 1  # the closing bracket
    while pos < end:
        a = _TAG_ARG_RE.match(tag, pos, end)
        if not a or a.end() == pos:
            break
        group = 'quoted' if a.group('quoted') is not None else 'bare'
        args.append((a.group('key'), a.group(group), a.span(group)))
        pos = a.end()
    return m.group(1), args


def _linetag_prefix(tag, current):
    """The ``&linetag`` prefix in effect after *tag*."""
    name, args = tag_args(tag)
    if name != '&linetag':
        return current
    values = {k: v for k, v, _ in args if k}
    if values.get('allow', '1') == '0':
        return None
    return values.get('prefix') or current


def _intermediate(header, data):
    raw = json.dumps(header, ensure_ascii=False).encode('utf-8')
    return INTERMEDIATE_MAGIC + struct.pack('<I', len(raw)) + raw + data


def _read_intermediate(raw):
    if not raw.startswith(INTERMEDIATE_MAGIC):
        raise ValueError('not an Artemis intermediate file')
    pos = len(INTERMEDIATE_MAGIC)
    (n,) = struct.unpack_from('<I', raw, pos)
    pos += 4
    return json.loads(raw[pos : pos + n].decode('utf-8')), raw[pos + n :]


def _split_raw_lines(data):
    """Split *data* into lines, each keeping its own terminator."""
    return re.findall(rb'[^\n]*\n|[^\n]+', data)


def _terminator(raw_line):
    if raw_line.endswith(b'\r\n'):
        return '\r\n'
    return '\n' if raw_line.endswith(b'\n') else ''


class ArtemisTxt(TranslatableFile):
    # The engine wraps per character, splitting English words, so insertion breaks lines itself:
    # each source line of text is one line on screen. The window is 25 full-width characters wide
    # (50 columns) and holds 4 lines.
    default_wrap = '\n'
    default_width = 50
    max_lines = 4

    @staticmethod
    def detect_artemis_encoding(data):
        """UTF-8 if *data* is valid UTF-8, else CP932 (the engine's default without ``CHARSET``).

        Japanese CP932 text is almost never valid UTF-8, which makes this far more reliable on
        short scripts than statistical detection.
        """
        try:
            data.decode('utf-8')
        except UnicodeDecodeError:
            return 'cp932'
        return 'utf-8'

    @staticmethod
    def get_paths(workpath):
        return sorted(Path(workpath).glob('**/*.txt'))

    @classmethod
    def extract_lines(cls, input_file, codec=None, ignore_patterns=(), unwrap=True, **kwargs):
        """Extract messages (with their speakers), choice labels and save titles.

        With *unwrap* (the default) a message's lines are joined, so insertion can rewrap them;
        otherwise they're joined with ``\\n``.
        """
        data = input_file.read()
        if codec is None:
            codec = cls.detect_artemis_encoding(data)
        if '\n[a'.encode(codec) != b'\n[a':
            raise ValueError(f'Artemis scripts must use an ASCII-compatible encoding, not {codec}')

        body = data[len(UTF8_BOM) :] if data.startswith(UTF8_BOM) else data
        raw_lines = _split_raw_lines(body)
        lines = []
        for i, raw in enumerate(raw_lines):
            try:
                lines.append(raw.decode(codec).rstrip('\r\n'))
            except UnicodeDecodeError:
                logger.warning('Line %d is not valid %s; leaving it untouched', i + 1, codec)
                lines.append(None)

        textlines = []
        items = []

        def export(text, speaker=None):
            if not text or cls._should_ignore(text, ignore_patterns):
                return None
            key = f'<<<TRANS:{len(textlines)}>>>'
            textlines.append(TextLine(key, text, '', speaker))
            return key

        speaker = None
        prefix = None  # &linetag prefix
        block = None  # 'comment' or 'lua' while inside one
        message = []  # line indices of the open message

        def close_message():
            nonlocal speaker
            if not message:
                return
            parts = [lines[i].strip(ASCII_SPACE) for i in message]
            source = join_lines(parts) if unwrap else '\n'.join(parts)
            key = export(source, speaker)
            if key:
                first = lines[message[0]]
                items.append(
                    {
                        'kind': 'message',
                        'key': key,
                        'start': message[0],
                        'end': message[-1] + 1,
                        'source': source,
                        'indent': first[: len(first) - len(first.lstrip(ASCII_SPACE))],
                        'prefix': prefix,
                    }
                )
            message.clear()
            speaker = None

        for i, line in enumerate(lines):
            stripped = line.strip(ASCII_SPACE) if line is not None else None

            if block == 'comment':
                if '*/' in line:
                    block = None
                continue
            if block == 'lua':
                if stripped.lower().startswith('[/lua]'):
                    block = None
                continue

            if line is None or not stripped:
                close_message()
                continue
            if stripped.startswith('/*'):
                close_message()
                if '*/' not in stripped[2:]:
                    block = 'comment'
                continue
            if stripped.startswith(COMMAND_STARTS) or (prefix and stripped.startswith(prefix)):
                close_message()
                continue
            if _TAG_ONLY_RE.fullmatch(stripped):
                close_message()
                speaker = cls._tag_line(i, line, speaker, items, export)
                for m in _TAG_RE.finditer(line):
                    prefix = _linetag_prefix(m.group(), prefix)
                if stripped.lower().startswith('[lua]') and not stripped.lower().endswith('[/lua]'):
                    block = 'lua'
                continue
            if cls._should_ignore(stripped, ignore_patterns):
                close_message()
                continue
            message.append(i)
        close_message()

        metadata = {'codec': codec}
        if codec.replace('-', '_').lower() in ('cp932', 'shift_jis', 'ms932', 'windows_31j', 'sjis'):
            fixup = build_file_cp932_fixup([data])
            if fixup:
                metadata['cp932_fixup'] = fixup

        header = {'codec': codec, 'items': items}
        return BytesIO(_intermediate(header, data)), textlines, metadata

    @staticmethod
    def _tag_line(i, line, speaker, items, export):
        """Record a tag line's speaker and translatable attributes; returns the speaker in effect."""
        for m in _TAG_RE.finditer(line):
            name, args = tag_args(m.group())
            if name == 'name':
                positional = [v for k, v, _ in args if k is None]
                speaker = positional[0] if positional else speaker
            elif name in TEXT_ATTR_TAGS:
                for k, value, (s, e) in args:
                    if k == 'text':
                        key = export(value)
                        if key:
                            items.append(
                                {
                                    'kind': 'attr',
                                    'key': key,
                                    'line': i,
                                    'span': [m.start() + s, m.start() + e],
                                    'source': value,
                                }
                            )
        return speaker

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, width=None, cp932_fixup=None, codec=None, **kwargs):
        header, data = _read_intermediate(intermediate_file.read())
        codec = header['codec']
        bom = UTF8_BOM if data.startswith(UTF8_BOM) else b''
        raw_lines = _split_raw_lines(data[len(bom) :])

        def encode(text):
            raw = text.encode(codec)
            return apply_cp932_fixup(raw, cp932_fixup) if cp932_fixup else raw

        def translation(item):
            t = translation_dict.get(item['key'])
            if t is None:
                return None
            text = t.text.replace('\\n', '\n')
            return None if text == item['source'] else text

        replacements = {}  # first line index -> (end index, new bytes)
        attrs = {}  # line index -> [(span, new text)]
        for item in header['items']:
            new = translation(item)
            if new is None:
                continue
            if item['kind'] == 'message':
                start, end = item['start'], item['end']
                out = cls._message_lines(item, new, width)
                if out is None:
                    continue
                nl = _terminator(raw_lines[start]) or '\r\n'
                text = nl.join(item['indent'] + line for line in out) + _terminator(raw_lines[end - 1])
                replacements[start] = (end, encode(text))
            else:
                if '"' in new:
                    logger.warning('Replacing double quotes with single quotes in %s: %r', item['key'], new)
                    new = new.replace('"', "'")
                attrs.setdefault(item['line'], []).append((item['span'], new.replace('\n', ' ')))

        for i, edits in attrs.items():
            line = raw_lines[i].decode(codec)
            for (s, e), new in sorted(edits, reverse=True):
                line = line[:s] + new + line[e:]
            replacements[i] = (i + 1, encode(line))

        out = bytearray(bom)
        i = 0
        while i < len(raw_lines):
            if i in replacements:
                end, new = replacements[i]
                out += new
                i = end
            else:
                out += raw_lines[i]
                i += 1
        return BytesIO(bytes(out))

    @classmethod
    def _message_lines(cls, item, text, width):
        """The source lines for a translated message, warning about anything the engine would misread."""
        lines = []
        for raw_segment in text.split('\n'):
            segment = raw_segment.strip(ASCII_SPACE)
            if not segment:
                continue  # a blank line would end the page
            if width and display_width(segment) > width:
                lines.extend(wrap_columns(segment, width))
            else:
                lines.append(segment)
        if not lines:
            logger.warning('Translation of %s is empty; keeping the source text', item['key'])
            return None

        if len(lines) > cls.max_lines:
            logger.warning(
                'Message %s needs %d lines; the window holds %d: %r', item['key'], len(lines), cls.max_lines, text
            )
        prefix = item.get('prefix')
        for line in lines:
            if line.startswith(COMMAND_STARTS) or (prefix and line.startswith(prefix)) or _TAG_ONLY_RE.fullmatch(line):
                logger.warning('Line of %s would be read as a command, not text: %r', item['key'], line)
        return lines
