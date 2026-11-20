"""System-NNN ``.xtx`` lists (``spt/namecol.xtx``, ``nya/systemse.xtx`` …).

A list is plain CP932 text that the engine reads as a flat sequence of tokens, ignoring
line structure: tokens are separated by one optional comma followed by any spaces, tabs
and line breaks. Each consumer indexes the tokens with a fixed stride (``namecol.xtx`` is
``name R G B``), so a translated token must stay a single token.
"""

import json
import logging
import struct
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

SPACES = b' \t\r\n'
DELIMITERS = b' \t,\r\n\0'
# Tokens starting with these are structural keys (sections, file references, comments)
MARKERS = b'[@#!'
# The engine copies tokens into 128-byte slots and stops with an error on longer ones
MAX_TOKEN_BYTES = 127
ASCII_SPACE = ' \t'

INTERMEDIATE_MAGIC = b'TOTONNNX'


def tokens(data):
    """Yield ``(start, end)`` of each token, splitting exactly as the engine does."""
    n = 0
    end = len(data)
    nul = data.find(b'\0')
    if nul >= 0:
        end = nul
    while n < end:
        if data[n] == ord(','):
            n += 1
        while n < end and data[n] in SPACES:
            n += 1
        if n >= end:
            break
        start = n
        while n < end and data[n] not in DELIMITERS:
            n += 1
        yield start, n


def xtx_safe(text):
    """Replace characters that would split a token with their full-width forms."""
    safe = text.replace(',', '，').replace(' ', '　').replace('\t', '　')
    return safe.replace('\r', '').replace('\n', '')


class SystemNnnXtx(TranslatableFile):
    @staticmethod
    def get_paths(workpath):
        return sorted(workpath.glob('**/*.xtx'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), **kwargs):
        """Extract every token containing non-ASCII text, except structural ``[`` ``@`` ``#`` ``!`` keys."""
        data = input_file.read()
        textlines = []
        raws = []
        spans = []
        for start, end in tokens(data):
            raw = data[start:end]
            if raw.isascii() or raw[0] in MARKERS:
                continue
            try:
                text = raw.decode('cp932')
            except UnicodeDecodeError:
                continue
            if cls._should_ignore(text, ignore_patterns):
                continue
            key = f'<<<TRANS:{len(textlines)}>>>'
            textlines.append(TextLine(key, text, b''))
            raws.append(raw)
            spans.append([start, end, key])

        header = json.dumps({'spans': spans}).encode('utf-8')
        intermediate = BytesIO(INTERMEDIATE_MAGIC + struct.pack('<I', len(header)) + header + data)
        metadata = {}
        fixup = build_file_cp932_fixup(raws)
        if fixup:
            metadata['cp932_fixup'] = fixup
        return intermediate, textlines, metadata

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None, **kwargs):
        raw = intermediate_file.read()
        if not raw.startswith(INTERMEDIATE_MAGIC):
            raise ValueError('not a System-NNN list intermediate file')
        pos = len(INTERMEDIATE_MAGIC)
        (header_len,) = struct.unpack_from('<I', raw, pos)
        pos += 4
        spans = json.loads(raw[pos : pos + header_len].decode('utf-8'))['spans']
        data = bytearray(raw[pos + header_len :])

        for start, end, key in reversed(spans):
            if key not in translation_dict:
                continue
            original = data[start:end].decode('cp932')
            new = translation_dict[key].text.replace('\\n', '\n').strip(ASCII_SPACE)
            if not new or new == original:
                continue
            safe = xtx_safe(new)
            if safe != new:
                logger.info('List entry %r: spaces and commas made full-width: %r', new, safe)
            encoded = safe.encode('cp932')
            if cp932_fixup:
                encoded = apply_cp932_fixup(encoded, cp932_fixup)
            if len(encoded) > MAX_TOKEN_BYTES:
                raise ValueError(
                    f'list entry {safe!r} is {len(encoded)} bytes; the engine allows {MAX_TOKEN_BYTES} '
                    '(128 including the terminator)'
                )
            data[start:end] = encoded

        return BytesIO(bytes(data))
