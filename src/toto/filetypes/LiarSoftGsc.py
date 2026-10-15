import re
import struct
from dataclasses import dataclass
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

HEADER = struct.Struct('<9I')
HEADER_SIZE = HEADER.size

# Explicit line break markup inside message text
LINE_BREAK = '^n'

_KEY_RE = re.compile(r'<<<TRANS:\d+>>>')


@dataclass
class GscScript:
    """A parsed GSC script.

    Only the string table (T1) is decoded. The code section and the T2/T3
    tables are kept as opaque bytes, since nothing in them refers to T1 by
    byte offset.
    """

    code: bytes
    strings: list[bytes]
    t2: bytes
    t3: bytes
    t2_index: int
    t2_data: int
    t3_index: int
    t3_data: int


def parse_gsc(data):
    """Parse a GSC script from *data*.

    Raises ValueError if the header sizes do not account for the file exactly.
    """
    if len(data) < HEADER_SIZE:
        raise ValueError('File too short for GSC header')
    file_size, header_size, code_size, t1_index, t1_data, t2_index, t2_data, t3_index, t3_data = HEADER.unpack_from(
        data
    )
    # T2 stores its data block twice; T3 stores two u32 arrays of t3_index bytes.
    expected = header_size + code_size + t1_index + t1_data + t2_index + 2 * t2_data + 2 * t3_index + t3_data
    if header_size != HEADER_SIZE or file_size != len(data) or expected != len(data) or t1_index % 4:
        raise ValueError('Not a GSC script (header sizes do not match file size)')

    pos = HEADER_SIZE
    code = data[pos : pos + code_size]
    pos += code_size

    count = t1_index // 4
    offsets = struct.unpack_from(f'<{count}I', data, pos)
    pos += t1_index
    pool = data[pos : pos + t1_data]
    pos += t1_data

    strings = []
    for offset in offsets:
        end = pool.find(b'\0', offset)
        if offset >= len(pool) or end < 0:
            raise ValueError(f'String offset {offset:#x} outside string data')
        strings.append(pool[offset:end])

    t2_size = t2_index + 2 * t2_data
    t2 = data[pos : pos + t2_size]
    pos += t2_size
    t3 = data[pos:]

    return GscScript(code, strings, t2, t3, t2_index, t2_data, t3_index, t3_data)


def build_gsc(script):
    """Serialize *script* back to GSC bytes, regenerating the string table."""
    index = bytearray()
    pool = bytearray()
    for s in script.strings:
        index += struct.pack('<I', len(pool))
        pool += s + b'\0'

    body_size = len(script.code) + len(index) + len(pool) + len(script.t2) + len(script.t3)
    header = HEADER.pack(
        HEADER_SIZE + body_size,
        HEADER_SIZE,
        len(script.code),
        len(index),
        len(pool),
        script.t2_index,
        script.t2_data,
        script.t3_index,
        script.t3_data,
    )
    return header + script.code + bytes(index) + bytes(pool) + script.t2 + script.t3


def _is_text(raw):
    # Resource names and labels are plain ASCII; anything else is display text.
    return not raw.isascii()


class LiarSoftGsc(TranslatableFile):
    default_wrap = LINE_BREAK

    @classmethod
    def should_wrap_line(cls, text, width):
        # Respect line breaks the translator placed by hand. Text without spaces
        # (e.g. untranslated Japanese) has no word boundaries to wrap at; the
        # engine already wraps it per character, so leave it alone.
        return LINE_BREAK not in text and ' ' in text and super().should_wrap_line(text, width)

    @staticmethod
    def get_paths(workpath):
        return list(workpath.glob('**/*.gsc'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=False):
        script = parse_gsc(input_file.read())

        textlines = []
        raw_extracted = []
        for i, raw in enumerate(script.strings):
            if not _is_text(raw):
                continue
            text = raw.decode('cp932')
            if cls._should_ignore(text, ignore_patterns):
                continue
            key = f'<<<TRANS:{len(textlines)}>>>'
            if unwrap:
                text = text.replace(LINE_BREAK, '')
            textlines.append(TextLine(key, text, b''))
            raw_extracted.append(raw)
            script.strings[i] = key.encode('ascii')

        metadata = {}
        cp932_fixup = build_file_cp932_fixup(raw_extracted)
        if cp932_fixup:
            metadata['cp932_fixup'] = cp932_fixup

        return BytesIO(build_gsc(script)), textlines, metadata

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None, width=None, wrap=None):
        if wrap is None:
            wrap = cls.default_wrap

        script = parse_gsc(intermediate_file.read())

        for i, raw in enumerate(script.strings):
            if not _KEY_RE.fullmatch(raw.decode('cp932', errors='replace')):
                continue
            key = raw.decode('ascii')
            if key not in translation_dict:
                raise KeyError(f'No translation for {key}')
            text = translation_dict[key].text.rstrip('\r\n')
            if cls.should_wrap_line(text, width):
                text = cls.wrap_text(text, width, wrap, '')
            encoded = text.encode('cp932')
            if cp932_fixup:
                encoded = apply_cp932_fixup(encoded, cp932_fixup)
            if b'\0' in encoded:
                raise ValueError(f'Translation for {key} contains a NUL character')
            script.strings[i] = encoded

        return BytesIO(build_gsc(script))
