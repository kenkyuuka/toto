import codecs
import logging
import re
from io import BytesIO
from pathlib import Path

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

# Match the stripping behavior of bytes.strip() — only ASCII whitespace,
# not fullwidth space or other Unicode whitespace.
_ASCII_WS = ' \t\n\r\x0b\x0c'

# A line consisting solely of tags, e.g. ``[bg file=x]``, ``[\]`` or
# ``[eval exp="a[0]"]``.  As in KAG's parser, a quote only delimits a value
# when it directly follows ``=`` (plus optional whitespace and ``&``/``%``);
# elsewhere it is literal.  A backtick escapes the next character.
_TAG = r"""\[(?:=[ \t]*[&%]?(?:"(?:`.|[^"`])*"|'(?:`.|[^'`])*')|`.|[^\]])*\]"""
_TAG_ONLY_RE = re.compile(rf'(?:[ \t]*{_TAG})+[ \t]*\\?')

# Inline TJS blocks.  KAG recognizes a delimiter only when the whole line,
# after leading tabs are stripped, is exactly one of these strings.
_SCRIPT_START = frozenset(('[iscript]', '[iscript]\\', '@iscript'))
_SCRIPT_END = frozenset(('[endscript]', '[endscript]\\', '@endscript'))


# Inline text tokens, for wrapping.  ``[[`` is KAG's escape for a literal ``[``.
_INLINE_TOKEN_RE = re.compile(rf'(?P<esc>\[\[)|(?P<tag>{_TAG})|(?P<space>[ \t]+)|(?P<text>[^ \t\[]+)')


def _tag_width(tag):
    """Return the displayed width of an inline tag.

    A bare tag with a non-ASCII name, such as ``[☆]``, is a game macro that
    draws a single symbol, so counts as one character.  Other tags only
    affect formatting and take up no space.
    """
    inner = tag[1:-1].strip()
    return 1 if inner and not inner.isascii() and not any(c.isspace() for c in inner) else 0


def _is_line_break(tag):
    return tag[1:-1].strip().lower() == 'r'


def _wrap_tagged(text, width):
    """Wrap text containing inline tags into lines of at most *width* displayed characters.

    Tags are never split, and stay attached to any adjoining word.  An
    explicit ``[r]`` is left in place but resets the line width.  A word
    wider than *width* is not broken.

    Returns a list of lines, or None if the text contains a malformed tag.
    """
    # Each chunk is (is_space, text, width, ends_with_line_break).
    chunks = []
    word, word_width = '', 0
    pos = 0
    while pos < len(text):
        m = _INLINE_TOKEN_RE.match(text, pos)
        if m is None:
            return None
        pos = m.end()
        token = m.group()
        if m.lastgroup == 'space':
            if word:
                chunks.append((False, word, word_width, False))
                word, word_width = '', 0
            chunks.append((True, token, len(token), False))
        elif m.lastgroup == 'tag':
            word += token
            word_width += _tag_width(token)
            if _is_line_break(token):
                chunks.append((False, word, word_width, True))
                word, word_width = '', 0
        else:
            word += token
            word_width += 1 if m.lastgroup == 'esc' else len(token)
    if word:
        chunks.append((False, word, word_width, False))

    lines = []
    line, line_width = [], 0
    # True until a word is placed after the start of a line or an explicit [r].
    fresh = True
    for is_space, chunk, chunk_width, line_break in chunks:
        if is_space:
            if line:
                line.append(chunk)
                line_width += chunk_width
            continue
        if not fresh and line_width + chunk_width > width:
            while line and line[-1].isspace():
                line.pop()
            lines.append(''.join(line))
            line, line_width = [], 0
        line.append(chunk)
        line_width += chunk_width
        fresh = False
        if line_break:
            line_width = 0
            fresh = True
    lines.append(''.join(line).rstrip(' \t'))
    return lines


def _kag_line(line):
    """Return a line as KAG compares it: terminator removed, leading tabs stripped."""
    return line.splitlines()[0].lstrip('\t')


class _Group:
    """Accumulates consecutive [r]-terminated lines into a single translatable unit."""

    def __init__(self):
        self.text = ''
        self.key = None
        self.leading = ''
        self.ending = ''
        self.eol = ''

    def __bool__(self):
        return bool(self.text)

    def add(self, key, text, leading, ending, eol=None):
        """Append a line to the group.

        key and leading are captured only from the first line.
        eol is updated only when explicitly passed (e.g. for [r] lines).
        """
        if not self.text:
            self.key = key
            self.leading = leading
        self.text += text
        self.ending = ending
        if eol is not None:
            self.eol = eol

    def flush(self, intermediate_file, textlines, codec, ending=None, eol=None):
        """Emit the accumulated group.

        Optional ending/eol overrides are used when the closing line provides
        its own values (e.g. a non-[r] macro that terminates the group).
        """
        assert self.key is not None
        if ending is None:
            ending = self.ending
        if eol is None:
            eol = self.eol
        intermediate_file.write((self.leading + self.key + ending).encode(codec, errors='backslashreplace'))
        textlines.append(TextLine(self.key, self.text, eol))


class KiriKiriScript(TranslatableFile):
    """Handler for KiriKiri .ks/.soc script files.

    Consecutive text lines ending with [r] are merged into a single
    translatable unit, so that multi-line dialogue can be translated as
    one piece of text.
    """

    default_wrap = '[r]'

    @classmethod
    def should_wrap_line(cls, text: str, width: int | None) -> bool:
        """Measure lines containing inline tags by their displayed width.

        Lines with malformed tags are never wrapped.
        """
        if '[' not in text or width is None:
            return super().should_wrap_line(text, width)
        lines = _wrap_tagged(text.strip(), width)
        return lines is not None and len(lines) > 1

    @staticmethod
    def wrap_text(text: str, width: int, wrap: str, newline: str) -> str:
        """Wrap text, keeping inline tags intact and measuring them by displayed width."""
        if '[' not in text:
            return TranslatableFile.wrap_text(text, width, wrap, newline)
        lines = _wrap_tagged(text.strip(), width)
        if lines is None:
            return text
        return (wrap + newline).join(lines)

    @staticmethod
    def get_paths(workpath) -> list[Path]:
        return list(workpath.glob('*.ks')) + list(workpath.glob('*.soc')) + list(workpath.glob('*.SOC'))

    @classmethod
    def _decode_input(cls, raw_data, codec):
        """Decode raw bytes and resolve encoding. Returns (text, codec, bom)."""
        if codec is None:
            codec = cls.detect_encoding(raw_data)
        text = raw_data.decode(codec, errors='backslashreplace')

        # Normalize UTF-16 to endian-specific codec so that encode() doesn't
        # prepend a BOM on every call.  Track the BOM separately.
        bom = b''
        if codecs.lookup(codec).name == 'utf-16':
            if raw_data[:2] == b'\xff\xfe':
                bom = b'\xff\xfe'
                codec = 'utf-16-le'
            elif raw_data[:2] == b'\xfe\xff':
                bom = b'\xfe\xff'
                codec = 'utf-16-be'

        # A BOM can survive decoding when the codec was given explicitly
        # (e.g. utf-16-le or utf-8).  Strip it and track it separately.
        if text.startswith('﻿'):
            text = text[1:]
            bom = '﻿'.encode(codec)

        return text, codec, bom

    @classmethod
    def extract_lines(
        cls,
        input_file,
        codec=None,
        group_starts=(
            '「',
            '　',
        ),
        line_end_macros=r'(?:\[[^\]]+\])+\\?',
        ignore_patterns=(),
        **kwargs,
    ) -> tuple[BytesIO, list[TextLine], dict]:
        intermediate_file = BytesIO()
        textlines: list[TextLine] = []
        group = _Group()

        command_starts = ('*', ';', '@', '//', '{')
        macro_re = re.compile(r'(?P<text>.*?)(?P<eol>' + line_end_macros + ')') if line_end_macros else None

        raw_data = input_file.read()
        text, codec, bom = cls._decode_input(raw_data, codec)

        cp932_fixup = {}
        if codecs.lookup(codec).name in ('shift_jis', 'cp932'):
            cp932_fixup = build_file_cp932_fixup([raw_data])

        script_start = None
        for i, line in enumerate(text.splitlines(keepends=True)):
            # Inline TJS code is passed through unchanged, delimiters included.
            if script_start is not None:
                intermediate_file.write(line.encode(codec, errors='backslashreplace'))
                if _kag_line(line) in _SCRIPT_END:
                    script_start = None
                continue
            if _kag_line(line) in _SCRIPT_START:
                if group:
                    group.flush(intermediate_file, textlines, codec)
                    group = _Group()
                intermediate_file.write(line.encode(codec, errors='backslashreplace'))
                script_start = i
                continue

            stripped = line.strip(_ASCII_WS)
            lstripped = line.lstrip(_ASCII_WS)
            rstripped = line.rstrip(_ASCII_WS)

            # Lines matching ignore patterns: flush any open group, then pass through.
            if stripped != '' and cls._should_ignore(stripped, ignore_patterns):
                if group:
                    group.flush(intermediate_file, textlines, codec)
                    group = _Group()
                intermediate_file.write(line.encode(codec, errors='backslashreplace'))
                continue

            key = f'<<<TRANS:{i}>>>'
            leading = line[: len(line) - len(lstripped)]
            ending = line[len(rstripped) :]

            # Blank lines, command lines, and tag-only lines: flush any open
            # group, then pass through.
            if (
                stripped == ''
                or any(lstripped.startswith(s) for s in command_starts)
                or _TAG_ONLY_RE.fullmatch(stripped)
            ):
                if group:
                    group.flush(intermediate_file, textlines, codec)
                    group = _Group()

                if lstripped.startswith('[select link="'):
                    # Choice text lives in a tag attribute.
                    intermediate_file.write((leading + key + ending).encode(codec, errors='backslashreplace'))
                    textlines.append(TextLine(key, stripped, ''))
                else:
                    intermediate_file.write(line.encode(codec, errors='backslashreplace'))
                continue

            if macro_re and (m := macro_re.fullmatch(stripped)):
                eol = m.group('eol')
                text_content = m.group('text')

                if eol == '[r]':
                    # In KiriKiri, [r] causes a line break.
                    group.add(key, text_content, leading, ending, eol='[r]')
                elif group:
                    # Other macros ([p], [l][r], etc.) close any open group.
                    group.text += text_content
                    group.flush(intermediate_file, textlines, codec, ending=ending, eol=eol)
                    group = _Group()
                else:
                    # Standalone macro-terminated line.
                    intermediate_file.write((leading + key + ending).encode(codec, errors='backslashreplace'))
                    textlines.append(TextLine(key, text_content, eol))
            elif macro_re and group_starts and any(line.startswith(c) for c in group_starts):
                # Lines starting with group_starts (e.g. 「) begin or continue a group.
                group.add(key, stripped, leading, ending)
            elif group:
                # Plain continuation of an already-open group.
                group.text += stripped
                group.ending = ending
            else:
                # Standalone text line with no macro.
                intermediate_file.write((key + ending).encode(codec, errors='backslashreplace'))
                textlines.append(TextLine(key, rstripped, ''))

        # KAG rejects an unclosed block; keep its contents out of the
        # translation rather than guessing where the code ends.
        if script_start is not None:
            logger.warning('Unclosed [iscript] block starting at line %d', script_start + 1)

        # The file may end while a group is still open.
        if group:
            group.flush(intermediate_file, textlines, codec)

        intermediate_file.seek(0)
        metadata = {'codec': codec, 'bom': bom}
        if cp932_fixup:
            metadata['cp932_fixup'] = cp932_fixup
        return (intermediate_file, textlines, metadata)

    @classmethod
    def insert_lines(
        cls, intermediate_file, translation_dict, width=None, wrap=None, codec='shift_jis', bom=b'', cp932_fixup=None
    ) -> BytesIO:
        if wrap is None:
            wrap = cls.default_wrap

        output_file = BytesIO()

        data = intermediate_file.read().decode(codec, errors='backslashreplace')
        newline = '\r\n' if '\r\n' in data else '\n'

        def get_trans(m):
            t = translation_dict[m.group(0)]
            if cls.should_wrap_line(t.text, width):
                text = cls.wrap_text(t.text, width, wrap, newline)
            else:
                text = t.text
            return text + t.eol

        new_data = re.sub(r'<<<TRANS:\d+>>>', get_trans, data)
        output_file.write(bom)
        encoded = new_data.encode(codec, errors='backslashreplace')
        if cp932_fixup:
            encoded = apply_cp932_fixup(encoded, cp932_fixup)
        output_file.write(encoded)

        output_file.seek(0)
        return output_file
