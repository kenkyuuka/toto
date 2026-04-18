import logging
import re
import struct
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

# mgos VM instruction size map (from reverse engineering of vm_execute_loop).
# Maps opcode byte -> total instruction size in bytes.
_INSN_SIZE = {}
_INSN_SIZE[0x08] = 1  # push null
for _b in range(0x80, 0xC0):  # operators
    _INSN_SIZE[_b] = 1
for _b in range(0xC0, 0xC4):  # shortcuts: declare, if-false-goto, goto, return
    _INSN_SIZE[_b] = 1
for _b in range(0xD0, 0xD5):  # push small int 0-4
    _INSN_SIZE[_b] = 1
for _b in [0x20, 0x22, 0x23, 0x25, 0x26, 0x27]:  # 1-byte operand
    _INSN_SIZE[_b] = 2
for _b in [0x10, 0x12, 0x13, 0x15, 0x16, 0x17]:  # 2-byte operand
    _INSN_SIZE[_b] = 3
for _b in [0x00, 0x01, 0x02, 0x03, 0x05, 0x06, 0x07]:  # 4-byte operand
    _INSN_SIZE[_b] = 5

# String reference opcodes: opcode -> (struct format, operand size in bytes)
_STRING_REF_OPCODES = {0x02: ('<I', 4), 0x12: ('<H', 2), 0x22: ('B', 1)}

# Matches any Japanese character (hiragana, katakana, kanji, fullwidth punctuation)
_JP_CHAR_RE = re.compile(r'[\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF\uFF01-\uFF5E]')

MAGIC = b'GALT'
FLAG_EXTRACTED = 0x01
FLAG_VERBATIM = 0x00
FLAG_SPLIT = 0x02
FLAG_NARRATION = 0x03  # narration: leading fullwidth space stripped on extract, re-added on insert
FLAG_SPLIT_UNQUOTED = 0x04  # name + dialogue without 「」 brackets

# Matches a character name followed by fullwidth space at the start of a string.
# Name is non-whitespace characters; what follows is the dialogue.
_NAME_DIALOGUE_RE = re.compile(r'^(\S+)\u3000(.+)$', re.DOTALL)

# Matches quoted dialogue with an optional post-quote stage direction.
# Group 1: dialogue content (between 「 and 」)
# Group 2: direction content (between （ and ）), or None
_QUOTED_DIALOGUE_RE = re.compile(r'^\u300c(.*)\u300d(?:\uff08(.*)\uff09)?$', re.DOTALL)


class Mgos(TranslatableFile):
    @staticmethod
    def get_paths(workpath):
        return list(workpath.glob('**/*.o'))

    @staticmethod
    def _walk_bytecode(data):
        """Walk bytecode from offset 0, collecting string references.

        Returns (bytecode_end, refs) where refs is a list of
        (operand_position, offset_value, operand_size) tuples.
        """
        file_len = len(data)
        pos = 0
        refs = []
        min_string_offset = file_len  # track lowest valid string ref target

        while pos < file_len:
            byte = data[pos]
            size = _INSN_SIZE.get(byte)
            if size is None:
                break
            if pos + size > file_len:
                break
            if byte in _STRING_REF_OPCODES:
                fmt, op_size = _STRING_REF_OPCODES[byte]
                if op_size == 1:
                    offset = data[pos + 1]
                else:
                    offset = struct.unpack_from(fmt, data, pos + 1)[0]
                if 0 < offset < file_len:
                    refs.append((pos + 1, offset, op_size))
                    min_string_offset = min(min_string_offset, offset)
            pos += size

        # The string table starts at the minimum string ref offset, which may
        # be before where the walker stopped (the first string table bytes can
        # look like valid opcodes).
        bytecode_end = min(pos, min_string_offset)

        if not refs:
            return pos, []

        # Only keep refs whose opcodes are within the bytecode region.
        refs = [(op_pos, off, sz) for op_pos, off, sz in refs if op_pos < bytecode_end]

        return bytecode_end, refs

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), **kwargs):
        data = input_file.read()

        # Walk bytecode to find string references and the string table boundary
        string_table_start, refs = cls._walk_bytecode(data)
        bytecode = data[:string_table_start]

        if not refs:
            # No string references — file has no translatable content.
            # Store the raw file as bytecode so insert_lines can reproduce it.
            intermediate = BytesIO()
            intermediate.write(MAGIC)
            intermediate.write(struct.pack('<III', len(data), 0, 0))
            intermediate.write(data)
            intermediate.seek(0)
            return (intermediate, [], {})

        # Parse string table
        strings = cls._parse_string_table(data, string_table_start)

        # Build per-file CP932 fixup table from original bytes
        cp932_fixup = build_file_cp932_fixup([raw for _, raw in strings])

        # Decide which strings to extract
        textlines = []
        line_index = 0
        string_records = []
        name_keys = {}  # name text -> TRANS key (for deduplication)

        for original_offset, raw_bytes in strings:
            try:
                text = raw_bytes.decode('cp932')
            except (UnicodeDecodeError, ValueError):
                text = None

            should_extract = (
                text is not None
                and len(raw_bytes) > 0
                and not text.startswith(('●', '○', '♪', '!'))
                and _JP_CHAR_RE.search(text)
                and not cls._should_ignore(text, ignore_patterns)
            )

            if should_extract:
                name_match = _NAME_DIALOGUE_RE.match(text)
                if name_match:
                    name, dialogue = name_match.group(1), name_match.group(2)

                    if name not in name_keys:
                        name_key = f'<<<TRANS:{line_index}>>>'
                        name_keys[name] = name_key
                        textlines.append(TextLine(name_key, name, b''))
                        line_index += 1
                    else:
                        name_key = name_keys[name]

                    # Try to parse 「content」（direction） structure
                    quote_match = _QUOTED_DIALOGUE_RE.match(dialogue)
                    if quote_match:
                        dialogue_content = quote_match.group(1)
                        direction = quote_match.group(2)  # None if no direction

                        dialogue_key = f'<<<TRANS:{line_index}>>>'
                        textlines.append(TextLine(dialogue_key, dialogue_content, b''))
                        line_index += 1

                        if direction is not None:
                            direction_key = f'<<<TRANS:{line_index}>>>'
                            textlines.append(TextLine(direction_key, direction, b''))
                            line_index += 1
                            split_data = f'{name_key}\x00{dialogue_key}\x00{direction_key}'.encode('ascii')
                        else:
                            split_data = f'{name_key}\x00{dialogue_key}'.encode('ascii')
                    else:
                        # Dialogue without 「」 — extract as-is
                        dialogue_key = f'<<<TRANS:{line_index}>>>'
                        textlines.append(TextLine(dialogue_key, dialogue, b''))
                        line_index += 1
                        split_data = f'{name_key}\x00{dialogue_key}'.encode('ascii')
                        string_records.append((original_offset, FLAG_SPLIT_UNQUOTED, split_data))
                        continue

                    string_records.append((original_offset, FLAG_SPLIT, split_data))
                elif text.startswith('\u3000'):
                    # Narration: strip leading fullwidth space for translation
                    key = f'<<<TRANS:{line_index}>>>'
                    textlines.append(TextLine(key, text[1:], b''))
                    key_bytes = key.encode('ascii')
                    string_records.append((original_offset, FLAG_NARRATION, key_bytes))
                    line_index += 1
                else:
                    key = f'<<<TRANS:{line_index}>>>'
                    textlines.append(TextLine(key, text, b''))
                    key_bytes = key.encode('ascii')
                    string_records.append((original_offset, FLAG_EXTRACTED, key_bytes))
                    line_index += 1
            else:
                string_records.append((original_offset, FLAG_VERBATIM, raw_bytes))

        # Build intermediate file
        intermediate = BytesIO()
        intermediate.write(MAGIC)
        intermediate.write(struct.pack('<I', len(bytecode)))
        intermediate.write(struct.pack('<I', len(refs)))
        intermediate.write(struct.pack('<I', len(string_records)))
        intermediate.write(bytecode)

        for bc_pos, orig_offset, op_size in refs:
            intermediate.write(struct.pack('<IIB', bc_pos, orig_offset, op_size))

        for orig_offset, flag, data_bytes in string_records:
            intermediate.write(struct.pack('<I', orig_offset))
            intermediate.write(bytes([flag]))
            intermediate.write(struct.pack('<H', len(data_bytes)))
            intermediate.write(data_bytes)

        intermediate.seek(0)
        return (intermediate, textlines, {'cp932_fixup': cp932_fixup})

    @staticmethod
    def insert_lines(intermediate_file, translation_dict, cp932_fixup=None):
        data = intermediate_file.read()
        pos = 0

        # Parse header
        magic = data[pos : pos + 4]
        pos += 4
        if magic != MAGIC:
            raise ValueError(f"Invalid intermediate file magic: {magic!r}")

        bytecode_length = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        num_refs = struct.unpack_from('<I', data, pos)[0]
        pos += 4
        num_strings = struct.unpack_from('<I', data, pos)[0]
        pos += 4

        # Read bytecode
        bytecode = bytearray(data[pos : pos + bytecode_length])
        pos += bytecode_length

        # Read reference table
        refs = []
        for _ in range(num_refs):
            bc_pos = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            orig_offset = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            op_size = data[pos]
            pos += 1
            refs.append((bc_pos, orig_offset, op_size))

        # Read string records
        string_records = []
        for _ in range(num_strings):
            orig_offset = struct.unpack_from('<I', data, pos)[0]
            pos += 4
            flag = data[pos]
            pos += 1
            data_len = struct.unpack_from('<H', data, pos)[0]
            pos += 2
            record_data = data[pos : pos + data_len]
            pos += data_len
            string_records.append((orig_offset, flag, record_data))

        cp932_fixup = cp932_fixup or {}

        # Build new string table and offset mapping
        offset_map = {}  # original_offset -> new_offset
        new_string_table = BytesIO()
        current_offset = bytecode_length

        for orig_offset, flag, record_data in string_records:
            offset_map[orig_offset] = current_offset

            if flag == FLAG_EXTRACTED:
                key = record_data.decode('ascii')
                if key in translation_dict:
                    trans = translation_dict[key]
                    encoded = apply_cp932_fixup(trans.text.rstrip('\n\r').encode('cp932'), cp932_fixup)
                else:
                    encoded = record_data
            elif flag == FLAG_NARRATION:
                key = record_data.decode('ascii')
                if key in translation_dict:
                    trans = translation_dict[key]
                    encoded = apply_cp932_fixup(('\u3000' + trans.text.rstrip('\n\r')).encode('cp932'), cp932_fixup)
                else:
                    encoded = record_data
            elif flag in (FLAG_SPLIT, FLAG_SPLIT_UNQUOTED):
                parts = record_data.decode('ascii').split('\x00')
                name_key = parts[0]
                dialogue_key = parts[1]
                direction_key = parts[2] if len(parts) > 2 else None

                name_trans = translation_dict.get(name_key)
                dialogue_trans = translation_dict.get(dialogue_key)
                name_text = name_trans.text.rstrip('\n\r') if name_trans else name_key
                dialogue_text = dialogue_trans.text.rstrip('\n\r') if dialogue_trans else dialogue_key

                if flag == FLAG_SPLIT:
                    # Wrap dialogue back in 「」
                    combined = f'\u300c{dialogue_text}\u300d'
                else:
                    # Unquoted: no brackets
                    combined = dialogue_text

                if direction_key is not None:
                    direction_trans = translation_dict.get(direction_key)
                    direction_text = direction_trans.text.rstrip('\n\r') if direction_trans else direction_key
                    combined += f'\uff08{direction_text}\uff09'

                encoded = apply_cp932_fixup(f'{name_text}\u3000{combined}'.encode('cp932'), cp932_fixup)
            else:
                encoded = bytes(record_data)

            str_len = len(encoded) + 1  # +1 for null terminator
            new_string_table.write(struct.pack('<H', str_len))
            new_string_table.write(encoded)
            new_string_table.write(b'\x00')
            current_offset += 2 + str_len  # 2 for length prefix

        # Patch bytecode references
        for bc_pos, orig_offset, op_size in refs:
            new_offset = offset_map.get(orig_offset)
            if new_offset is None:
                continue
            if op_size == 4:
                struct.pack_into('<I', bytecode, bc_pos, new_offset)
            elif op_size == 2:
                if new_offset > 0xFFFF:
                    raise ValueError(
                        f"String table offset {new_offset:#x} exceeds uint16 range "
                        f"for 2-byte ref at bytecode pos {bc_pos}"
                    )
                struct.pack_into('<H', bytecode, bc_pos, new_offset)
            elif op_size == 1:
                if new_offset > 0xFF:
                    raise ValueError(
                        f"String table offset {new_offset:#x} exceeds uint8 range "
                        f"for 1-byte ref at bytecode pos {bc_pos}"
                    )
                bytecode[bc_pos] = new_offset

        output = BytesIO()
        output.write(bytes(bytecode))
        output.write(new_string_table.getvalue())
        output.seek(0)
        return output

    @staticmethod
    def _try_parse_string_table(data, start):
        """Try parsing data[start:] as consecutive string entries.

        Returns the number of entries if it parses cleanly to EOF, or -1 on failure.
        """
        pos = start
        count = 0
        while pos < len(data):
            if pos + 2 > len(data):
                return -1
            str_len = struct.unpack_from('<H', data, pos)[0]
            if str_len == 0 or pos + 2 + str_len > len(data):
                return -1
            if data[pos + 2 + str_len - 1] != 0:
                return -1
            count += 1
            pos += 2 + str_len
        return count if count > 0 else -1

    @staticmethod
    def _parse_string_table(data, start):
        """Parse string table entries, returning list of (offset, raw_bytes)."""
        entries = []
        pos = start
        while pos < len(data):
            str_len = struct.unpack_from('<H', data, pos)[0]
            raw_bytes = data[pos + 2 : pos + 2 + str_len - 1]  # exclude null terminator
            entries.append((pos, raw_bytes))
            pos += 2 + str_len
        return entries
