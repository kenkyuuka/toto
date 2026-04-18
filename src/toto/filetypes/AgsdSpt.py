import re
import struct
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

# Matches any Japanese character (hiragana, katakana, kanji, fullwidth punctuation)
_JP_CHAR_RE = re.compile(r'[\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF01-\uFF5E]')

# Intermediate file magic
MAGIC = b'ASPT'

# Text entry type tags
TAG_CHAR = 0x07
TAG_BREAK = 0x0D

# Text node header: sentinel + speaker + count + 2 reserved
_TEXT_NODE_HEADER = struct.Struct('<IiIII')
_TEXT_NODE_SENTINEL = 0xFFFFFFFF

# Single character entry
_ENTRY = struct.Struct('<II2sH')  # type, value, char_bytes, pad

# Segment types in intermediate file
SEG_RAW = 0
SEG_TEXT = 1

# Flags for text segments
FLAG_VERBATIM = 0
FLAG_EXTRACTED = 1


def _find_text_blocks(data):
    """Find all text display nodes in an SPT file.

    Scans for the ``FFFFFFFF`` sentinel at every byte offset (entries are not
    necessarily 4-byte aligned), validates the 20-byte header, and collects
    the character entries that follow.

    Returns a list of dicts with keys:
        header_offset: byte offset of the 20-byte header
        end_offset: byte offset past the last entry
        speaker: speaker ID (int32; -1 for narration)
        entries: list of (tag, char_bytes) tuples
    """
    blocks = []
    sentinel_bytes = _TEXT_NODE_SENTINEL.to_bytes(4, 'little')
    i = 0
    data_len = len(data)

    while i < data_len - 20:
        # Scan for the FFFFFFFF sentinel
        i = data.find(sentinel_bytes, i)
        if i < 0:
            break

        hdr_off = i

        # Need at least 20 bytes for header + 12 for one entry
        if hdr_off + 32 > data_len:
            i += 1
            continue

        # Read and validate header
        _sentinel, speaker, count = struct.unpack_from('<IiI', data, hdr_off)
        z1, z2 = struct.unpack_from('<II', data, hdr_off + 12)

        if z1 != 0 or z2 != 0 or count < 1 or count > 10000:
            i += 1
            continue

        # Check that there's enough room for all entries
        entries_start = hdr_off + 20
        entries_end = entries_start + count * 12
        if entries_end > data_len:
            i += 1
            continue

        # Read and validate entries
        entries = []
        valid = True
        for j in range(count):
            off = entries_start + j * 12
            et = struct.unpack_from('<I', data, off)[0]
            ev = struct.unpack_from('<I', data, off + 4)[0]
            cb = data[off + 8 : off + 10]
            pd = data[off + 10 : off + 12]

            if et == TAG_CHAR and ev == 0 and pd == b'\x00\x00':
                entries.append((TAG_CHAR, cb))
            elif et == TAG_BREAK and ev == 0:
                entries.append((TAG_BREAK, b'\x00\x00'))
            else:
                valid = False
                break

        if valid and len(entries) == count:
            blocks.append(
                {
                    'header_offset': hdr_off,
                    'end_offset': entries_end,
                    'speaker': speaker,
                    'entries': entries,
                }
            )
            i = entries_end
        else:
            i += 1

    return blocks


def _is_cp932_lead(byte):
    """Return True if *byte* is a CP932 double-byte lead byte."""
    return 0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC


def _decode_text_block(entries):
    """Decode character entries into a text string.

    TAG_CHAR entries are decoded as CP932 characters (including ``\\n`` for
    byte ``0x0A``).  Single-byte characters are stored with a ``0x00`` padding
    byte in the 2-byte entry field — only the first byte is meaningful.
    TAG_BREAK entries are the end-of-text marker and are **not** included in
    the returned string.
    """
    parts = []
    for tag, cb in entries:
        if tag == TAG_CHAR:
            try:
                if _is_cp932_lead(cb[0]):
                    # Double-byte CP932 character: both bytes are meaningful
                    parts.append(cb.decode('cp932'))
                else:
                    # Single-byte character: second byte is null padding
                    parts.append(cb[0:1].decode('cp932'))
            except (UnicodeDecodeError, ValueError):
                parts.append('\ufffd')
        # TAG_BREAK is the end marker -- skip it
    return ''.join(parts)


def _encode_text_entries(text):
    """Encode a text string into a list of (tag, char_bytes) tuples.

    Every character becomes a TAG_CHAR entry with its CP932 encoding.
    A trailing TAG_BREAK entry is appended as the end-of-text marker.

    Returns (entries, raw_char_bytes_list) where raw_char_bytes_list contains
    the CP932 bytes for each character (for fixup purposes).
    """
    entries = []
    raw_bytes_list = []
    for ch in text:
        encoded = ch.encode('cp932')
        # Pad single-byte chars to 2 bytes
        if len(encoded) == 1:
            cb = encoded + b'\x00'
        else:
            cb = encoded[:2]
        entries.append((TAG_CHAR, cb))
        raw_bytes_list.append(encoded)
    # Always append end-of-text marker
    entries.append((TAG_BREAK, b'\x00\x00'))
    return entries, raw_bytes_list


def _apply_char_fixup(cb, cp932_fixup):
    """Apply CP932 fixup to a single character entry's bytes."""
    fixed = apply_cp932_fixup(cb.rstrip(b'\x00'), cp932_fixup)
    if len(fixed) == 1:
        return fixed + b'\x00'
    return fixed[:2]


def _write_text_node(out, speaker, entries):
    """Write a complete text display node (header + entries) to a stream."""
    count = len(entries)
    out.write(_TEXT_NODE_HEADER.pack(_TEXT_NODE_SENTINEL, speaker, count, 0, 0))
    for tag, cb in entries:
        out.write(_ENTRY.pack(tag, 0, cb, 0))


class AgsdSpt(TranslatableFile):
    default_wrap = '\n'

    @staticmethod
    def get_paths(workpath):
        return list(workpath.glob('**/*.spt'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=False):
        data = input_file.read()
        blocks = _find_text_blocks(data)

        if not blocks:
            # No text blocks -- return raw data as intermediate
            intermediate = BytesIO()
            intermediate.write(MAGIC)
            struct.pack_into('<I', (buf := bytearray(4)), 0, 1)
            intermediate.write(buf)
            # Single raw segment = entire file
            intermediate.write(bytes([SEG_RAW]))
            intermediate.write(struct.pack('<I', len(data)))
            intermediate.write(data)
            intermediate.seek(0)
            return (intermediate, [], {})

        # Collect raw CP932 bytes for fixup table
        all_raw_bytes = []
        for block in blocks:
            for tag, cb in block['entries']:
                if tag == TAG_CHAR:
                    all_raw_bytes.append(cb.rstrip(b'\x00'))
        cp932_fixup = build_file_cp932_fixup(all_raw_bytes)

        # Build segments: alternating raw data and text blocks
        segments = []
        pos = 0
        textlines = []
        line_index = 0

        for block in blocks:
            hdr_off = block['header_offset']
            end_off = block['end_offset']

            # Raw segment before this text block
            if hdr_off > pos:
                segments.append((SEG_RAW, data[pos:hdr_off]))

            # Decode the text (TAG_BREAK end markers are excluded)
            text = _decode_text_block(block['entries'])

            # Decide whether to extract
            should_extract = bool(text) and _JP_CHAR_RE.search(text) and not cls._should_ignore(text, ignore_patterns)

            if should_extract:
                key = f'<<<TRANS:{line_index}>>>'
                display_text = text.replace('\n', '') if unwrap else text
                textlines.append(TextLine(key, display_text, b''))
                segments.append(
                    (
                        SEG_TEXT,
                        {
                            'speaker': block['speaker'],
                            'flag': FLAG_EXTRACTED,
                            'key': key,
                            'original_text': text,
                        },
                    )
                )
                line_index += 1
            else:
                segments.append(
                    (
                        SEG_TEXT,
                        {
                            'speaker': block['speaker'],
                            'flag': FLAG_VERBATIM,
                            'key': '',
                            'original_text': text,
                        },
                    )
                )

            pos = end_off

        # Trailing raw data
        if pos < len(data):
            segments.append((SEG_RAW, data[pos:]))

        # Build intermediate file
        intermediate = BytesIO()
        intermediate.write(MAGIC)
        intermediate.write(struct.pack('<I', len(segments)))

        for seg_type, seg_data in segments:
            intermediate.write(bytes([seg_type]))
            if seg_type == SEG_RAW:
                intermediate.write(struct.pack('<I', len(seg_data)))
                intermediate.write(seg_data)
            else:  # SEG_TEXT
                intermediate.write(struct.pack('<i', seg_data['speaker']))
                intermediate.write(bytes([seg_data['flag']]))
                key_bytes = seg_data['key'].encode('ascii')
                intermediate.write(struct.pack('<H', len(key_bytes)))
                intermediate.write(key_bytes)
                text_bytes = seg_data['original_text'].encode('utf-8')
                intermediate.write(struct.pack('<H', len(text_bytes)))
                intermediate.write(text_bytes)

        intermediate.seek(0)
        return (intermediate, textlines, {'cp932_fixup': cp932_fixup})

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None, width=None, wrap=None):
        if wrap is None:
            wrap = cls.default_wrap

        data = intermediate_file.read()
        pos = 0

        magic = data[pos : pos + 4]
        pos += 4
        if magic != MAGIC:
            raise ValueError(f"Invalid intermediate file magic: {magic!r}")

        num_segments = struct.unpack_from('<I', data, pos)[0]
        pos += 4

        cp932_fixup = cp932_fixup or {}
        output = BytesIO()

        for _ in range(num_segments):
            seg_type = data[pos]
            pos += 1

            if seg_type == SEG_RAW:
                length = struct.unpack_from('<I', data, pos)[0]
                pos += 4
                output.write(data[pos : pos + length])
                pos += length

            elif seg_type == SEG_TEXT:
                speaker = struct.unpack_from('<i', data, pos)[0]
                pos += 4
                flag = data[pos]
                pos += 1
                key_len = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                key = data[pos : pos + key_len].decode('ascii')
                pos += key_len
                text_len = struct.unpack_from('<H', data, pos)[0]
                pos += 2
                original_text = data[pos : pos + text_len].decode('utf-8')
                pos += text_len

                if flag == FLAG_EXTRACTED and key in translation_dict:
                    trans = translation_dict[key]
                    text_to_encode = trans.text.rstrip('\n\r').replace('\\n', '\n')
                    if cls.should_wrap_line(text_to_encode, width):
                        text_to_encode = cls.wrap_text(text_to_encode, width, wrap, '')
                else:
                    text_to_encode = original_text

                # Encode text into character entries
                entries, _raw_list = _encode_text_entries(text_to_encode)

                # Apply CP932 fixup to character entries
                if cp932_fixup:
                    entries = [
                        (tag, _apply_char_fixup(cb, cp932_fixup) if tag == TAG_CHAR else cb) for tag, cb in entries
                    ]

                _write_text_node(output, speaker, entries)

        output.seek(0)
        return output
