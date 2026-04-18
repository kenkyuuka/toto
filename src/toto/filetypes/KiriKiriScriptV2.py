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
        """Skip wrapping for lines containing KiriKiri inline commands (``[``)."""
        return '[' not in text and super().should_wrap_line(text, width)

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

        command_starts = ('[', '*', ';', '@', '//', '{')
        macro_re = re.compile(r'(?P<text>.*?)(?P<eol>' + line_end_macros + ')') if line_end_macros else None

        raw_data = input_file.read()
        text, codec, bom = cls._decode_input(raw_data, codec)

        cp932_fixup = {}
        if codecs.lookup(codec).name in ('shift_jis', 'cp932'):
            cp932_fixup = build_file_cp932_fixup([raw_data])

        for i, line in enumerate(text.splitlines(keepends=True)):
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

            # Blank lines and command lines: flush any open group, then pass through.
            if stripped == '' or any(lstripped.startswith(s) for s in command_starts):
                if group:
                    group.flush(intermediate_file, textlines, codec)
                    group = _Group()

                if (
                    lstripped.startswith('[select link="')
                    or lstripped.startswith('[「]')
                    or lstripped.startswith('[（]')
                ):
                    # Certain commands contain translatable text.
                    key = f'<<<TRANS:{i}>>>'
                    leading = line[: len(line) - len(lstripped)]
                    intermediate_file.write((leading + key).encode(codec, errors='backslashreplace'))
                    textlines.append(TextLine(key, lstripped, ''))
                else:
                    intermediate_file.write(line.encode(codec, errors='backslashreplace'))
                continue

            key = f'<<<TRANS:{i}>>>'
            leading = line[: len(line) - len(lstripped)]
            ending = line[len(rstripped) :]

            if macro_re and (m := macro_re.match(stripped)):
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
