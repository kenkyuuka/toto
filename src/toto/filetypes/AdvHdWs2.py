"""AdvHD WS2 script format handler.

WS2 files are bytecode scripts used by the AdvHD visual novel engine (Willplus/Will).
They contain single-byte opcodes with variable-length operands and UTF-16LE strings.
See doc/advhd-ws2.md for format documentation.
"""

import logging
import re
import struct
from io import BytesIO

from ..util import TextLine
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

MAGIC = b'WS2I'

# Chunk types for intermediate format
_RAW = 0
_DIALOGUE = 1
_NAME = 2
_CHOICE = 3

# Matches %K, %P, or %K%P at the end of dialogue text
_EOL_RE = re.compile(r'(%K%P|%K|%P)$')

# ---------------------------------------------------------------------------
# Opcode signature table
#
# Maps opcode (int) -> list of operand type codes, terminated by -1.
# Types: 0=byte, 1/2=word(u16), 3/4=int/ptr(u32), 5=float, 6/9/10=string, 7=array, 8=marker(0 bytes)
# Source: Lite0812/AdvHD2.1_WS2_Toolkit disasm_ws2.py
# ---------------------------------------------------------------------------
_SIGS = {
    0x00: [-1],
    0x04: [10, 8, -1],
    0x05: [-1],
    0x07: [10, 8, -1],
    0x08: [0, -1],
    0x09: [0, 1, 5, -1],
    0x0A: [1, 5, -1],
    0x0B: [1, 0, -1],
    0x0C: [1, 0, 7, 1, -1],
    0x0D: [1, 1, 5, -1],
    0x0E: [1, 1, 0, -1],
    0x11: [6, 8, 0, 5, -1],
    0x12: [6, 8, 0, 10, 8, -1],
    0x13: [-1],
    0x14: [4, 6, 8, 6, 8, 0, -1],  # DisplayMessage
    0x15: [6, 8, 0, -1],  # SetDisplayName
    0x16: [0, 0, -1],
    0x17: [-1],
    0x18: [0, 6, 8, -1],  # AddToLog
    0x19: [-1],
    0x1A: [6, 8, -1],
    0x1B: [0, -1],
    0x1C: [6, 8, 6, 8, 1, 0, -1],
    0x1D: [1, -1],
    0x1E: [6, 8, 10, 8, 5, 5, 1, 1, 0, 5, -1],
    0x1F: [6, 8, 5, -1],
    0x20: [6, 8, 5, 1, -1],
    0x21: [6, 8, 1, 1, 1, -1],
    0x22: [6, 8, 0, -1],
    0x28: [6, 8, 10, 8, 5, 5, 1, 1, 0, 1, 1, 0, 5, -1],
    0x29: [6, 8, 5, -1],
    0x2A: [6, 8, 5, 1, -1],
    0x2B: [6, 8, -1],
    0x2C: [6, 8, -1],
    0x2D: [6, 8, 0, -1],
    0x2E: [-1],
    0x2F: [6, 8, 1, 5, -1],
    0x30: [6, 8, 5, -1],
    0x32: [10, 8, -1],
    0x33: [6, 8, 10, 8, 0, 0, -1],
    0x34: [6, 8, 10, 8, 0, 0, -1],
    0x35: [6, 8, 10, 8, 0, 0, 0, -1],
    0x36: [6, 8, 5, 5, 5, 5, 5, 5, 5, 0, 0, -1],
    0x37: [6, 8, -1],
    0x38: [6, 8, 0, -1],
    0x39: [6, 8, 0, 0, 7, 1, -1],
    0x3A: [6, 8, 0, 0, -1],
    0x3B: [6, 8, 6, 8, 1, 1, 1, 5, 5, 5, 5, 5, 5, 5, 5, -1],
    0x3C: [6, 8, -1],
    0x3D: [1, -1],
    0x3E: [-1],
    0x3F: [7, 6, -1],
    0x40: [6, 8, 10, 8, 0, -1],
    0x41: [6, 8, 0, -1],
    0x42: [6, 8, 1, -1],
    0x43: [6, 8, -1],
    0x44: [6, 8, 6, 8, 0, -1],
    0x45: [6, 8, 1, 5, 5, 5, 5, -1],
    0x46: [6, 8, 1, 0, 5, 5, 5, 5, -1],
    0x47: [6, 8, 6, 8, 1, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0x48: [6, 8, 6, 8, 1, 0, 0, 10, 8, -1],
    0x49: [6, 8, 6, 8, 10, 8, -1],
    0x4A: [6, 8, 6, 8, -1],
    0x4B: [6, 8, 1, 1, 5, 5, 5, 5, -1],
    0x4C: [6, 8, 1, 1, 0, 5, 5, 5, 5, -1],
    0x4D: [6, 8, 6, 8, 1, 1, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0x4E: [6, 8, 6, 8, 1, 1, 0, 0, 10, 8, -1],
    0x4F: [6, 8, 6, 8, 1, 10, 8, -1],
    0x50: [6, 8, 6, 8, 1, -1],
    0x51: [6, 8, 6, 8, 1, 5, 0, -1],
    0x52: [6, 8, 6, 8, 5, 1, 5, 0, 10, 8, -1],
    0x53: [6, 8, 6, 8, -1],
    0x54: [6, 8, 6, 8, 10, 8, -1],
    0x55: [6, 8, 6, 8, -1],
    0x56: [6, 8, 0, 1, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 0, 5, 5, 5, 5, 0, 1, 6, 8, 1, 6, 8, 10, 8, 5, -1],
    0x57: [6, 8, 1, -1],
    0x58: [6, 8, 6, 8, -1],
    0x59: [6, 8, 6, 8, 1, -1],
    0x5A: [6, 8, 7, 1, -1],
    0x5B: [6, 8, 1, 0, -1],
    0x5C: [6, 8, -1],
    0x5D: [6, 8, 6, 8, 0, -1],
    0x5E: [6, 8, 5, 5, -1],
    0x5F: [10, 8, -1],
    0x60: [1, 1, 1, 1, -1],
    0x61: [0, 5, 5, 5, 5, -1],
    0x62: [6, 8, -1],
    0x63: [6, 8, 0, -1],
    0x64: [0, -1],
    0x65: [1, 0, 5, 5, 0, 10, 8, -1],
    0x66: [10, 8, -1],
    0x67: [0, 0, 1, 5, 5, 5, 5, 5, 0, -1],
    0x68: [0, -1],
    0x69: [6, 8, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0x6A: [6, 8, 1, 0, 0, 10, 8, -1],
    0x6B: [6, 8, 6, 8, -1],
    0x6C: [6, 8, 5, 5, -1],
    0x6D: [6, 8, 5, 5, 0, 0, 0, -1],
    0x6E: [9, 8, 6, 8, -1],
    0x6F: [9, 8, -1],
    0x70: [9, 8, 1, -1],
    0x71: [-1],
    0x72: [9, 8, 1, 1, 9, 8, -1],
    0x73: [9, 8, 9, 8, 1, -1],
    0x74: [9, 8, 9, 8, -1],
    0x75: [9, 8, 6, 8, -1],
    0x78: [6, 8, 10, 8, 0, 0, 0, -1],
    0x79: [6, 8, 6, 8, 5, -1],
    0x7A: [6, 8, 10, 8, 5, 0, 0, 10, 8, -1],
    0x7B: [6, 8, 10, 8, -1],
    0x7C: [6, 8, 6, 8, 5, -1],
    0x7D: [6, 8, 5, -1],
    0x7E: [6, 8, -1],
    0x7F: [6, 8, 5, 5, 5, 5, 5, -1],
    0x80: [6, 8, -1],
    0x81: [6, 8, 0, 10, 8, 5, 5, 0, -1],
    0x82: [6, 8, 10, 8, 5, -1],
    0x83: [6, 8, 6, 8, 5, 5, -1],
    0x84: [6, 8, 6, 8, 6, 8, 5, 1, 5, -1],
    0x85: [6, 8, 6, 8, 0, 5, -1],
    0x86: [6, 8, 5, 5, 5, -1],
    0x87: [6, 8, 5, -1],
    0x88: [6, 8, 6, 8, 6, 8, 5, 1, 5, -1],
    0x89: [6, 8, 5, 5, -1],
    0x8A: [6, 8, 6, 8, 0, 0, 0, -1],
    0x8C: [6, 8, 10, 8, 6, 8, 0, 0, 6, 8, 10, 8, -1],
    0x8D: [4, 6, 8, 6, 8, 0, 0, 1, 10, 8, -1],
    0x8E: [4, 6, 8, 6, 8, 0, 0, 1, 10, 8, -1],
    0x8F: [6, 8, 10, 8, -1],
    0x90: [6, 8, -1],
    0x91: [-1],
    0x96: [1, 5, 5, 5, 5, -1],
    0x97: [1, 0, 5, 5, 5, 5, -1],
    0x98: [6, 8, 1, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0x99: [6, 8, 1, 0, 0, 10, 8, -1],
    0x9A: [-1],
    0x9B: [6, 8, -1],
    0x9C: [6, 8, 10, 8, -1],
    0x9D: [6, 8, -1],
    0x9E: [6, 8, 0, -1],
    0x9F: [6, 8, 0, -1],
    0xA0: [5, 5, 5, 5, -1],
    0xA1: [-1],
    0xA5: [6, 8, 5, 5, 10, 8, 10, 8, 5, 0, 0, -1],
    0xA6: [6, 8, 1, 1, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0xA7: [6, 8, 1, 1, 0, 0, 10, 8, -1],
    0xA8: [6, 8, 6, 8, 1, 1, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0xA9: [6, 8, 6, 8, 1, 1, 0, 0, 10, 8, -1],
    0xAA: [1, 0, 0, 5, 5, 5, 5, 5, 1, 5, -1],
    0xAB: [1, 0, 0, -1],
    0xAC: [-1],
    0xAD: [1, -1],
    0xAE: [6, 8, 1, -1],
    0xAF: [1, 1, 5, 5, 5, 5, -1],
    0xB0: [6, 8, 1, 1, 5, 5, 5, 5, -1],
    0xB4: [6, 8, 10, 8, 0, 0, -1],
    0xB5: [6, 8, 6, 8, 0, 0, 5, 5, 5, 0, 0, 10, 8, -1],
    0xB6: [6, 8, 5, -1],
    0xB7: [6, 8, 5, -1],
    0xB8: [6, 8, -1],
    0xB9: [6, 8, 6, 8, -1],
    0xBA: [6, 8, 6, 8, 6, 8, -1],
    0xBB: [6, 8, 0, -1],
    0xBE: [6, 8, 10, 8, 0, 0, -1],
    0xBF: [6, 8, 6, 8, -1],
    0xC0: [6, 8, 6, 8, 0, 0, 0, 0, 10, 8, -1],
    0xC1: [6, 8, -1],
    0xC2: [6, 8, 6, 8, 1, 1, 0, 0, 0, -1],
    0xC3: [6, 8, 1, 1, 6, 8, -1],
    0xC8: [-1],
    0xC9: [6, 8, 6, 8, 1, 1, 1, 1, -1],
    0xCA: [6, 8, 6, 8, -1],
    0xCB: [6, 8, 0, 0, -1],
    0xCC: [-1],
    0xCD: [6, 8, 6, 8, 6, 8, 6, 8, 6, 8, 5, 0, -1],
    0xCE: [0, -1],
    0xCF: [6, 8, 6, 8, 5, -1],
    0xD0: [6, 8, 1, -1],
    0xD1: [6, 8, 1, -1],
    0xD2: [6, 8, -1],
    0xD3: [6, 8, -1],
    0xD4: [10, 8, 1, 1, -1],
    0xD5: [6, 8, 5, -1],
    0xD6: [6, 8, 10, 8, -1],
    0xDC: [6, 8, 10, 8, 0, 0, 5, 5, 5, 0, -1],
    0xDD: [6, 8, 5, 5, 5, 0, 5, 0, 10, 8, -1],
    0xDE: [6, 8, 1, 5, 5, 5, 0, 5, 0, 10, 8, -1],
    0xDF: [6, 8, -1],
    0xE0: [6, 8, 1, -1],
    0xE5: [4, 4, -1],
    0xE7: [-1],
    0xE8: [-1],
    0xE9: [0, -1],
    0xF0: [0, -1],
    0xF8: [-1],
    0xF9: [0, 10, 8, -1],
    0xFA: [-1],
    0xFB: [0, -1],
    0xFC: [1, -1],
    0xFD: [-1],
    0xFE: [6, 8, -1],
}

_SPECIAL = frozenset({0x01, 0x02, 0x06, 0x0F, 0xE6, 0xFF})


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------


def _read_str(data, pos):
    """Read null-terminated UTF-16LE string starting at *pos*.

    Returns ``(text, pos_after_null)``.
    """
    start = pos
    while pos + 1 < len(data):
        if data[pos] == 0 and data[pos + 1] == 0:
            return data[start:pos].decode('utf-16-le'), pos + 2
        pos += 2
    msg = f'Unterminated UTF-16LE string at offset {start:#x}'
    raise ValueError(msg)


def _encode_str(text):
    """Encode *text* as UTF-16LE with null terminator."""
    return text.encode('utf-16-le') + b'\x00\x00'


def _skip(data, pos, type_code):
    """Skip one operand of *type_code* at *pos*.  Returns new position."""
    if type_code == 0:
        return pos + 1
    if type_code in (1, 2):
        return pos + 2
    if type_code in (3, 4, 5):
        return pos + 4
    if type_code in (6, 9, 10):
        _, pos = _read_str(data, pos)
        return pos
    if type_code == 8:
        return pos  # marker, 0 bytes
    msg = f'Unknown operand type {type_code}'
    raise ValueError(msg)


def _ror2(b):
    return ((b >> 2) | (b << 6)) & 0xFF


def _decrypt(data):
    return bytes(_ror2(b) for b in data)


def _rol2(b):
    return ((b << 2) | (b >> 6)) & 0xFF


def _encrypt(data):
    return bytes(_rol2(b) for b in data)


def _ends_with_ff(data):
    """Return True if *data* parses as a valid WS2 stream ending with 0xFF."""
    try:
        pos = 0
        last_opcode = None
        while pos < len(data):
            opcode = data[pos]
            last_opcode = opcode
            pos += 1

            if opcode == 0x01:
                val = data[pos]
                pos += 1
                if val in (2, 128, 129, 130, 192) or (val == 3 and pos < len(data) and data[pos] in (50, 51, 127, 128)):
                    for tc in (1, 5, 4, 4):
                        pos = _skip(data, pos, tc)
            elif opcode in (0x02, 0x06):
                pos = _skip(data, pos, 4)
            elif opcode == 0x0F:
                count = data[pos]
                pos += 1
                for _ in range(count):
                    pos = _skip(data, pos, 1)  # word
                    _, pos = _read_str(data, pos)  # choice text
                    pos += 3  # op1, op2, op3
                    op_jump = data[pos]
                    pos += 1
                    if op_jump == 6:
                        pos = _skip(data, pos, 4)
                    elif op_jump == 7:
                        _, pos = _read_str(data, pos)
                    else:
                        return False
            elif opcode == 0xE6:
                pos = _skip(data, pos, 4)
                pos = _skip(data, pos, 4)
            elif opcode == 0xFF:
                pos = _skip(data, pos, 4)
                pos += 4
            elif opcode in _SIGS:
                sig = _SIGS[opcode]
                i = 0
                while i < len(sig):
                    tc = sig[i]
                    if tc == -1:
                        break
                    if tc == 7:
                        cnt = data[pos]
                        pos += 1
                        next_tc = sig[i + 1]
                        for _ in range(cnt):
                            pos = _skip(data, pos, next_tc)
                        i += 2
                        continue
                    pos = _skip(data, pos, tc)
                    i += 1
            else:
                return False
        return last_opcode == 0xFF and pos == len(data)
    except (ValueError, IndexError, struct.error):
        return False


def _detect_encrypted(data):
    """Return True if *data* appears to be ROL2-encrypted."""
    if _ends_with_ff(data):
        return False
    if _ends_with_ff(_decrypt(data)):
        return True
    return False


# ---------------------------------------------------------------------------
# Intermediate file I/O
# ---------------------------------------------------------------------------


def _write_intermediate(chunks, fixups, encrypted):
    """Serialize chunks and fixups into intermediate format."""
    buf = BytesIO()
    buf.write(MAGIC)
    buf.write(struct.pack('<B', 1 if encrypted else 0))
    buf.write(struct.pack('<I', len(chunks)))
    buf.write(struct.pack('<I', len(fixups)))

    for orig_offset, chunk_type, payload in chunks:
        buf.write(struct.pack('<BI', chunk_type, orig_offset))
        if chunk_type == _RAW:
            buf.write(struct.pack('<I', len(payload)))
            buf.write(payload)
        else:
            key, eol, orig_byte_len = payload
            key_bytes = key.encode('ascii')
            eol_bytes = eol.encode('ascii')
            buf.write(struct.pack('<I', orig_byte_len))
            buf.write(struct.pack('<H', len(key_bytes)))
            buf.write(key_bytes)
            buf.write(struct.pack('<H', len(eol_bytes)))
            buf.write(eol_bytes)

    for chunk_idx, offset_in_chunk in fixups:
        buf.write(struct.pack('<II', chunk_idx, offset_in_chunk))

    buf.seek(0)
    return buf


def _read_intermediate(data):
    """Deserialize intermediate format.  Returns (chunks, fixups, encrypted)."""
    pos = 0
    if data[pos : pos + 4] != MAGIC:
        msg = f"Invalid intermediate magic: {data[:4]!r}"
        raise ValueError(msg)
    pos += 4

    encrypted = bool(data[pos])
    pos += 1

    num_chunks = struct.unpack_from('<I', data, pos)[0]
    pos += 4
    num_fixups = struct.unpack_from('<I', data, pos)[0]
    pos += 4

    chunks = []
    for _ in range(num_chunks):
        chunk_type = data[pos]
        pos += 1
        orig_offset = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        if chunk_type == _RAW:
            length = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            payload = data[pos : pos + length]
            pos += length
            chunks.append((orig_offset, _RAW, payload))
        else:
            orig_byte_len = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            key_len = struct.unpack_from('<H', data, pos)[0]
            pos += 2
            key = data[pos : pos + key_len].decode('ascii')
            pos += key_len
            eol_len = struct.unpack_from('<H', data, pos)[0]
            pos += 2
            eol = data[pos : pos + eol_len].decode('ascii')
            pos += eol_len
            chunks.append((orig_offset, chunk_type, (key, eol, orig_byte_len)))

    fixups = []
    for _ in range(num_fixups):
        chunk_idx = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        offset_in_chunk = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        fixups.append((chunk_idx, offset_in_chunk))

    return chunks, fixups, encrypted


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


def _parse_file(data, ignore_patterns=()):
    """Parse a decrypted WS2 file.

    Returns ``(chunks, fixups, textlines)`` where:
    - chunks: list of ``(orig_offset, type, payload)``
    - fixups: list of ``(chunk_index, offset_in_chunk)`` for pointer values
    - textlines: list of ``TextLine``
    """
    chunks = []
    fixups = []
    textlines = []
    ptr_positions = []  # file offsets of pointer operands (pre-chunking)
    line_index = 0
    raw_start = 0  # start of the current uncommitted raw region

    def _flush_raw(end):
        """Emit raw bytes from raw_start..end and assign pending pointers."""
        nonlocal raw_start
        if end <= raw_start:
            return
        chunk_idx = len(chunks)
        raw_data = data[raw_start:end]
        chunks.append((raw_start, _RAW, raw_data))
        # Assign pointer fixups that fall within this raw chunk
        for pp in ptr_positions:
            if raw_start <= pp < end:
                fixups.append((chunk_idx, pp - raw_start))
        raw_start = end

    def _emit_text(text_type, string_start, string_end, text, eol):
        nonlocal raw_start, line_index
        _flush_raw(string_start)
        key = f'<<<TRANS:{line_index}>>>'
        orig_byte_len = string_end - string_start
        chunks.append((string_start, text_type, (key, eol, orig_byte_len)))
        textlines.append(TextLine(key, text, eol))
        line_index += 1
        raw_start = string_end

    pos = 0
    while pos < len(data):
        opcode = data[pos]
        pos += 1

        if opcode == 0x14:  # DisplayMessage
            pos += 4  # skip sequence number (not a jump pointer)
            _, pos = _read_str(data, pos)  # skip variable name string
            # marker (0 bytes)
            dialogue_start = pos
            dialogue_text, pos = _read_str(data, pos)
            dialogue_end = pos
            # marker (0 bytes)
            pos += 1  # separator byte

            eol_match = _EOL_RE.search(dialogue_text)
            if eol_match:
                eol = eol_match.group(1)
                text = dialogue_text[: eol_match.start()]
            else:
                eol = ''
                text = dialogue_text

            if text and not TranslatableFile._should_ignore(text, ignore_patterns):
                _emit_text(_DIALOGUE, dialogue_start, dialogue_end, text, eol)

        elif opcode == 0x15:  # SetDisplayName
            name_start = pos
            name_text, pos = _read_str(data, pos)
            name_end = pos
            # marker (0 bytes)
            pos += 1  # separator byte

            if name_text.startswith('%LF'):
                name = name_text[3:]
                if name and not TranslatableFile._should_ignore(name, ignore_patterns):
                    _emit_text(_NAME, name_start, name_end, name, '')

        elif opcode == 0x0F:  # ShowChoice (special)
            count = data[pos]
            pos += 1
            for _ in range(count):
                pos += 2  # word (choice id)
                choice_start = pos
                choice_text, pos = _read_str(data, pos)
                choice_end = pos
                pos += 3  # op1, op2, op3
                op_jump = data[pos]
                pos += 1
                if op_jump == 6:
                    ptr_positions.append(pos)
                    pos += 4
                elif op_jump == 7:
                    _, pos = _read_str(data, pos)

                if choice_text and not TranslatableFile._should_ignore(choice_text, ignore_patterns):
                    _emit_text(_CHOICE, choice_start, choice_end, choice_text, '')

        elif opcode == 0x01:  # Condition (special)
            val = data[pos]
            pos += 1
            if val in (2, 128, 129, 130, 192) or (val == 3 and pos < len(data) and data[pos] in (50, 51, 127, 128)):
                pos = _skip(data, pos, 1)  # word
                pos = _skip(data, pos, 5)  # float
                ptr_positions.append(pos)
                pos = _skip(data, pos, 4)  # ptr
                ptr_positions.append(pos)
                pos = _skip(data, pos, 4)  # ptr

        elif opcode in (0x02, 0x06):  # Jump (special)
            ptr_positions.append(pos)
            pos = _skip(data, pos, 4)

        elif opcode == 0xE6:  # ConditionalJump (special)
            ptr_positions.append(pos)
            pos = _skip(data, pos, 4)
            ptr_positions.append(pos)
            pos = _skip(data, pos, 4)

        elif opcode == 0xFF:  # FileEnd (special)
            pos = _skip(data, pos, 4)  # int
            pos += 4  # 4 individual bytes

        elif opcode in _SIGS:
            sig = _SIGS[opcode]
            i = 0
            while i < len(sig):
                tc = sig[i]
                if tc == -1:
                    break
                if tc == 7:  # array
                    cnt = data[pos]
                    pos += 1
                    next_tc = sig[i + 1]
                    for _ in range(cnt):
                        pos = _skip(data, pos, next_tc)
                    i += 2
                    continue
                pos = _skip(data, pos, tc)
                i += 1
        else:
            msg = f'Unknown opcode {opcode:#x} at offset {pos - 1:#x}'
            raise ValueError(msg)

    # Flush remaining raw bytes
    _flush_raw(len(data))

    # Discard ptr_positions that weren't assigned (shouldn't happen, but be safe)
    return chunks, fixups, textlines


# ---------------------------------------------------------------------------
# Handler class
# ---------------------------------------------------------------------------


class AdvHdWs2(TranslatableFile):
    @staticmethod
    def get_paths(workpath):
        return list(workpath.glob('**/*.ws2'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), **kwargs):
        raw_data = input_file.read()

        encrypted = _detect_encrypted(raw_data)
        data = _decrypt(raw_data) if encrypted else raw_data

        chunks, fixups, textlines = _parse_file(data, ignore_patterns)
        intermediate = _write_intermediate(chunks, fixups, encrypted)
        return (intermediate, textlines, {})

    @staticmethod
    def insert_lines(intermediate_file, translation_dict):
        idata = intermediate_file.read()
        chunks, fixups, encrypted = _read_intermediate(idata)

        # First pass: build output and offset mapping
        output_parts = []
        # Track (original_offset, original_size, new_size) for offset mapping
        offset_records = []

        for orig_offset, chunk_type, payload in chunks:
            if chunk_type == _RAW:
                new_bytes = bytearray(payload)
                orig_size = len(payload)
            else:
                key, eol, orig_byte_len = payload
                orig_size = orig_byte_len
                trans = translation_dict.get(key)
                if trans is not None:
                    text = trans.text
                else:
                    text = key  # fallback to key if no translation

                if chunk_type == _DIALOGUE:
                    new_bytes = _encode_str(text + eol)
                elif chunk_type == _NAME:
                    new_bytes = _encode_str('%LF' + text)
                elif chunk_type == _CHOICE:
                    new_bytes = _encode_str(text)
                else:
                    msg = f'Unknown chunk type {chunk_type}'
                    raise ValueError(msg)

            offset_records.append((orig_offset, orig_size, len(new_bytes)))
            output_parts.append(new_bytes)

        # Build cumulative offset shift table for pointer fixup
        # For each chunk: new_start = orig_offset + cumulative_shift
        cumulative_shift = 0
        shift_table = []  # (original_offset, shift)
        for orig_offset, orig_size, new_size in offset_records:
            shift_table.append((orig_offset, cumulative_shift))
            cumulative_shift += new_size - orig_size

        def _map_offset(old_offset):
            """Map an original byte offset to its new position."""
            # Find the last shift entry with original_offset <= old_offset
            best_shift = 0
            for entry_offset, shift in shift_table:
                if entry_offset <= old_offset:
                    best_shift = shift
                else:
                    break
            return old_offset + best_shift

        # Second pass: fix up pointer values in raw chunks
        for chunk_idx, offset_in_chunk in fixups:
            raw_bytes = output_parts[chunk_idx]
            old_ptr = struct.unpack_from('<I', raw_bytes, offset_in_chunk)[0]
            if old_ptr == 0:
                continue  # null pointers don't need fixup
            new_ptr = _map_offset(old_ptr)
            struct.pack_into('<I', raw_bytes, offset_in_chunk, new_ptr)

        # Assemble final output
        output = BytesIO()
        for part in output_parts:
            output.write(bytes(part))

        if encrypted:
            output.seek(0)
            output = BytesIO(_encrypt(output.read()))

        output.seek(0)
        return output
