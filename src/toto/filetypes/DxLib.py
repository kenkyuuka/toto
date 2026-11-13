import json
import logging
import re
import struct
import textwrap
from dataclasses import dataclass
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)


def should_translate(text):
    return any(('\u0800' <= ch <= '\u9fa5') or ('\uff01' <= ch <= '\uff5e') for ch in text) and text[0] not in '#;'


replacements = {
    '': '^O',
    '': '^P',
    '': '^X',
}


def cleanup_text(text):
    for k, v in replacements.items():
        text = text.replace(k, v)
    return text


def uncleanup_text(text):
    for k, v in replacements.items():
        text = text.replace(v, k)
    return text


HEADER_SIZE = 0x10


def has_body_size(script):
    """Whether bytes 0-3 of *script* hold its body size (length minus the header)."""
    return len(script) >= HEADER_SIZE and int.from_bytes(script[:4], 'little') == len(script) - HEADER_SIZE


OP_EOL = 0x00
OP_TEXT = 0x01
OP_COMMENT = 0x02
OP_BLANK = 0x03
OP_CLICK = 0x04
OP_IF_OPEN = 0x08
OP_IF_CLOSE = 0x09
OP_NAME = 0x0D
OP_VOICE = 0x14
OP_MENU = 0x21
OP_PICTURE_MENU = 0x22
OP_SWITCH = 0x2A

NO_STRING = 0xFFFF
HANGING_INDENT = '　'
# Dialogue openers whose continuation lines the games indent under the bracket.
INDENTED_OPENERS = '「（'


@dataclass
class Record:
    line: int
    payload: bytes
    offset: int | None = None  # offset in the original code area; None for new records

    @property
    def op(self):
        return self.payload[0] if self.payload else None

    @property
    def string(self):
        """The u16 string operand of a text, name or voice record."""
        return struct.unpack_from('<H', self.payload, 1)[0]

    def size(self):
        return 3 + len(self.payload)


@dataclass
class Script:
    """A decrypted script: header, code records and string pool.

    Code offsets (label table, block jumps, menu and switch tables) are relative to
    the start of the code area, and are rewritten by ``to_bytes`` when records move.
    """

    body_size: int
    source_lines: int
    records: list[Record]
    tail: bytes
    label_offsets: list[int]
    strings: list[bytes]
    label_names: list[bytes]
    code_end: int = 0

    @classmethod
    def parse(cls, data):
        if len(data) < HEADER_SIZE:
            raise ValueError('too short for a script header')
        body_size, code_len, counts, source_lines = struct.unpack_from('<IIII', data)
        nstrings, nlabels = counts & 0xFFFF, counts >> 16
        if HEADER_SIZE + code_len > len(data):
            raise ValueError('code length runs past the end of the file')
        code = data[HEADER_SIZE : HEADER_SIZE + code_len]
        pool = data[HEADER_SIZE + code_len :]
        if len(pool) < 2 * nlabels:
            raise ValueError('label table runs past the end of the file')
        label_offsets = list(struct.unpack_from(f'<{nlabels}H', pool))
        parts = pool[2 * nlabels :].split(b'\0')
        if parts[-1] != b'' or len(parts) - 1 != nstrings + nlabels:
            raise ValueError('string pool does not match the header counts')
        records = []
        pos = 0
        while pos + 3 <= len(code):
            line, n = struct.unpack_from('<HB', code, pos)
            if n == 0 or pos + 3 + n > len(code):
                raise ValueError(f'malformed record at code offset {pos:#x}')
            records.append(Record(line, code[pos + 3 : pos + 3 + n], pos))
            pos += 3 + n
        script = cls(
            body_size, source_lines, records, code[pos:], label_offsets, parts[:nstrings], parts[nstrings:-1], pos
        )
        script._check_offsets()
        return script

    def _check_offsets(self):
        bounds = {r.offset for r in self.records} | {self.code_end}
        for off in self.label_offsets:
            if off not in bounds:
                raise ValueError(f'label offset {off:#x} is not a record boundary')
        for r in self.records:
            for off in _code_refs(r.payload):
                if off not in bounds:
                    raise ValueError(f'jump target {off:#x} at {r.offset:#x} is not a record boundary')

    def to_bytes(self, update_body_size):
        """Serialize, remapping every code offset from old record offsets to new ones."""
        remap = {}
        pos = 0
        for r in self.records:
            if r.offset is not None:
                remap.setdefault(r.offset, pos)
            pos += r.size()
        remap[self.code_end] = pos

        def new(off):
            try:
                return remap[off]
            except KeyError:
                raise ValueError(f'code offset {off:#x} points inside rewritten text') from None

        code = b''.join(struct.pack('<HB', r.line, len(r.payload)) + _relocate(r.payload, new) for r in self.records)
        code += self.tail
        if len(self.strings) >= NO_STRING:
            raise ValueError(f'too many strings ({len(self.strings)}); indices are u16 and {NO_STRING:#x} is reserved')
        pool = struct.pack(f'<{len(self.label_offsets)}H', *(new(o) for o in self.label_offsets))
        pool += b''.join(s + b'\0' for s in self.strings)
        pool += b''.join(s + b'\0' for s in self.label_names)
        body = code + pool
        body_size = len(body) if update_body_size else self.body_size
        counts = len(self.strings) | (len(self.label_offsets) << 16)
        return struct.pack('<IIII', body_size, len(code), counts, self.source_lines) + body


def _table_layout(payload):
    """(start, count) of the u16 code offsets in *payload*, or None if it has none."""
    op = payload[0]
    if op in (OP_IF_OPEN, OP_IF_CLOSE):
        return 2, 1
    if op == OP_SWITCH:
        return 2, payload[1] + 1
    if op in (OP_MENU, OP_PICTURE_MENU):
        return 4, payload[3] + 1
    return None


def _code_refs(payload):
    layout = _table_layout(payload)
    if layout is None:
        return ()
    start, count = layout
    if len(payload) != start + 2 * count:
        raise ValueError(f'unexpected payload length for opcode {payload[0]:#04x}')
    return struct.unpack_from(f'<{count}H', payload, start)


def _relocate(payload, new):
    layout = _table_layout(payload)
    if layout is None:
        return payload
    start, count = layout
    offsets = [new(o) for o in _code_refs(payload)]
    return payload[:start] + struct.pack(f'<{count}H', *offsets)


@dataclass
class Message:
    """A run of text and end-of-line records, usually ended by a click."""

    start: int  # index of the first record of the run
    stop: int  # index just past the run
    clicked: bool  # the run is followed by a click record


def find_messages(records):
    out = []
    i = 0
    while i < len(records):
        if records[i].op != OP_TEXT:
            i += 1
            continue
        j = i
        while j < len(records) and records[j].op in (OP_TEXT, OP_EOL):
            j += 1
        clicked = j < len(records) and records[j].op == OP_CLICK
        out.append(Message(i, j, clicked))
        i = j
    return out


def _decode(raw):
    return raw.decode('cp932')


INTERMEDIATE_MAGIC = b'TOTODXU1'
_KEY_RE = re.compile(rb'<<<TRANS:\d+>>>')


def _is_word_char(ch):
    """Whether *ch* is ASCII text that needs a space when two lines are joined."""
    return ch.isascii() and not ch.isspace()


class DxLib(TranslatableFile):
    default_wrap = '\n'
    default_width = 45
    lines_per_page = 3

    @staticmethod
    def get_paths(workpath):
        return workpath.glob('*')

    @staticmethod
    def get_strings_offset(script):
        position = script.tell()
        script.seek(4, 0)
        offset = int.from_bytes(script.read(4), byteorder='little') + 0x10
        script.seek(position, 0)
        return offset

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=True, **kwargs):
        """Extract translatable strings.

        Without *unwrap*, every translatable string in the pool is extracted on its own.
        With *unwrap* (the default), each message (the text lines up to a click) is extracted as one
        joined line so that insertion can rewrap it into as many lines as it needs.
        Files whose record layout can't be parsed fall back to the per-string mode.
        """
        data = input_file.read()
        if unwrap:
            try:
                script = Script.parse(data)
            except ValueError as e:
                logger.info('Cannot parse script records (%s); extracting strings without unwrapping', e)
            else:
                return cls._extract_unwrapped(script, has_body_size(data), ignore_patterns)
        return cls._extract_legacy(BytesIO(data), ignore_patterns)

    @classmethod
    def insert_lines(
        cls,
        intermediate_file,
        translation_dict,
        cp932_fixup=None,
        update_body_size=False,
        width=None,
        wrap=None,
        **kwargs,
    ):
        data = intermediate_file.read()
        if data.startswith(INTERMEDIATE_MAGIC):
            return cls._insert_unwrapped(data, translation_dict, cp932_fixup, width)
        return cls._insert_legacy(BytesIO(data), translation_dict, cp932_fixup, update_body_size)

    @staticmethod
    def _join_lines(lines):
        """Join a message's lines, dropping the hanging indent of continuation lines."""
        out = lines[0] if lines else ''
        for line in lines[1:]:
            rest = line.removeprefix(HANGING_INDENT)
            if out and rest and _is_word_char(out[-1]) and _is_word_char(rest[0]):
                out += ' '
            out += rest
        return out

    @staticmethod
    def _uses_indent(lines):
        if len(lines) > 1:
            return all(line.startswith(HANGING_INDENT) for line in lines[1:])
        return bool(lines) and lines[0][:1] in INDENTED_OPENERS

    @classmethod
    def _message_lines(cls, script, message):
        return [
            _decode(script.strings[r.string]) for r in script.records[message.start : message.stop] if r.op == OP_TEXT
        ]

    @classmethod
    def _extract_unwrapped(cls, script, update_body_size, ignore_patterns):
        messages = find_messages(script.records)
        # Messages in script order, each preceded by the standalone strings (speaker
        # names, menu options) that first appear before it. The compiler numbers
        # strings by first use, so a string index is a good proxy for its position.
        found = []  # (kind, ident, text, raw byte strings, sort position)
        message_strings = set()
        for n, m in enumerate(messages):
            text_records = [r for r in script.records[m.start : m.stop] if r.op == OP_TEXT]
            try:
                lines = cls._message_lines(script, m)
            except UnicodeDecodeError:
                logger.warning('Skipping undecodable message at record %d', m.start)
                continue
            if any('\t' in line for line in lines):
                # A definition table (``name<TAB>value`` per line, as in _DEF), not prose:
                # leave its lines to be extracted one by one.
                continue
            message_strings.update(r.string for r in text_records)
            joined = cls._join_lines(lines)
            if should_translate(joined) and not cls._should_ignore(joined, ignore_patterns):
                raws = [script.strings[r.string] for r in text_records]
                found.append(('message', n, joined, raws, text_records[0].string))
        standalone = []
        for i, raw in enumerate(script.strings):
            if i in message_strings or not raw:
                continue
            try:
                text = _decode(raw)
            except UnicodeDecodeError:
                continue
            if should_translate(text) and not cls._should_ignore(text, ignore_patterns):
                standalone.append(('string', i, text, [raw], i))

        ordered = []
        pending = iter(standalone)
        nxt = next(pending, None)
        for item in found:
            while nxt is not None and nxt[4] < item[4]:
                ordered.append(nxt)
                nxt = next(pending, None)
            ordered.append(item)
        while nxt is not None:
            ordered.append(nxt)
            nxt = next(pending, None)

        textlines = []
        message_keys = [None] * len(messages)
        raws = []
        for line_index, (kind, ident, text, item_raws, _) in enumerate(ordered):
            key = f'<<<TRANS:{line_index}>>>'
            textlines.append(TextLine(key, cleanup_text(text), b''))
            raws.extend(item_raws)
            if kind == 'message':
                message_keys[ident] = key
            else:
                script.strings[ident] = key.encode('ascii')

        header = json.dumps({'messages': message_keys, 'update_body_size': update_body_size}).encode('utf-8')
        intermediate = BytesIO()
        intermediate.write(INTERMEDIATE_MAGIC)
        intermediate.write(struct.pack('<I', len(header)))
        intermediate.write(header)
        intermediate.write(script.to_bytes(update_body_size=False))
        intermediate.seek(0)

        metadata = {}
        cp932_fixup = build_file_cp932_fixup(raws)
        if cp932_fixup:
            metadata['cp932_fixup'] = cp932_fixup
        return intermediate, textlines, metadata

    @classmethod
    def _wrap_message(cls, text, width, indent):
        """Split *text* on explicit breaks and wrap each piece; continuation lines get *indent*."""
        lines = []
        for piece in text.split('\n'):
            first = '' if not lines else indent
            if width:
                wrapped = textwrap.wrap(piece, width=width, initial_indent=first, subsequent_indent=indent)
            else:
                wrapped = [first + piece]
            lines.extend(wrapped or [first])
        return lines

    @classmethod
    def _insert_unwrapped(cls, data, translation_dict, cp932_fixup, width):
        pos = len(INTERMEDIATE_MAGIC)
        (header_len,) = struct.unpack_from('<I', data, pos)
        pos += 4
        header = json.loads(data[pos : pos + header_len].decode('utf-8'))
        script = Script.parse(data[pos + header_len :])

        def encode(text):
            encoded = uncleanup_text(text).encode('cp932')
            return apply_cp932_fixup(encoded, cp932_fixup) if cp932_fixup else encoded

        for i, raw in enumerate(script.strings):
            if _KEY_RE.fullmatch(raw):
                script.strings[i] = encode(translation_dict[raw.decode('ascii')].text)

        index = {}
        for i, raw in enumerate(script.strings):
            index.setdefault(raw, i)

        def intern(raw):
            if raw not in index:
                index[raw] = len(script.strings)
                script.strings.append(raw)
            return index[raw]

        records = script.records
        messages = find_messages(records)
        keys = header['messages']
        if len(keys) != len(messages):
            raise ValueError('intermediate file does not match its message table')

        out = []
        done = 0
        for message, key in zip(messages, keys, strict=True):
            if key is None:
                continue
            lines = cls._message_lines(script, message)
            text = translation_dict[key].text.replace('\\n', '\n')
            if text == cleanup_text(cls._join_lines(lines)):
                continue
            out.extend(records[done : message.start])
            out.extend(cls._rewrap(records, message, text, width, cls._uses_indent(lines), encode, intern))
            done = message.stop
        out.extend(records[done:])
        script.records = out

        return BytesIO(script.to_bytes(update_body_size=header['update_body_size']))

    @classmethod
    def _rewrap(cls, records, message, text, width, indented, encode, intern):
        """Records replacing *message* with *text*, wrapped and split into pages."""
        old = records[message.start : message.stop]
        text_lines = [r.line for r in old if r.op == OP_TEXT]
        eol_lines = [r.line for r in old if r.op == OP_EOL]
        trailing_eol = old[-1].op == OP_EOL
        last_line = old[-1].line

        lines = cls._wrap_message(text, width, HANGING_INDENT if indented else '')
        if message.clicked:
            pages = [lines[i : i + cls.lines_per_page] for i in range(0, len(lines), cls.lines_per_page)]
            click = records[message.stop]
        else:
            pages = [lines]
            if len(lines) > cls.lines_per_page:
                logger.warning('Text before a non-click record needs %d lines: %r', len(lines), text)
        name = None
        i = message.start - 1
        while i >= 0 and records[i].op in (OP_VOICE, OP_COMMENT, OP_BLANK):
            i -= 1
        if i >= 0 and records[i].op == OP_NAME:
            name = records[i]

        new = []
        k = 0
        for p, page in enumerate(pages):
            if p:
                new.append(Record(click.line, click.payload))
                if name is not None:
                    new.append(Record(name.line, name.payload))
            for j, line in enumerate(page):
                idx = intern(encode(line))
                new.append(
                    Record(
                        text_lines[k] if k < len(text_lines) else last_line, bytes([OP_TEXT]) + struct.pack('<H', idx)
                    )
                )
                if j < len(page) - 1 or trailing_eol:
                    new.append(Record(eol_lines[k] if k < len(eol_lines) else last_line, bytes([OP_EOL])))
                k += 1
        # Jumps and labels that pointed at the start of the run now point at its replacement.
        new[0].offset = old[0].offset
        return new

    @classmethod
    def _extract_legacy(cls, input_file, ignore_patterns=()):
        # assume we're starting with the med unpacked, so this is getting text from the individual
        # files
        textlines = []
        intermediate_file = BytesIO()

        data = input_file.read()
        input_file.seek(0)
        update_body_size = has_body_size(data)

        offset = cls.get_strings_offset(input_file)

        # The pool opens with the label table, u16 code offsets (count in the high half
        # of header bytes 8-11), before the strings. Copy it through with the code.
        label_count = int.from_bytes(data[10:12], 'little') if len(data) >= HEADER_SIZE else 0
        if offset + 2 * label_count <= len(data):
            offset += 2 * label_count

        # copy the file up to the strings
        intermediate_file.write(input_file.read(offset))

        # from this point on, everything is null-terminated strings
        chars = input_file.read()
        chunk = b''
        line_index = 0
        raw_translated_chunks = []
        for char in chars:
            if char:
                # this is non-zero, so save the letter and move on
                chunk += int.to_bytes(char)
            else:
                # we have hit a null, so this string is complete
                if not chunk:
                    # we got a lonely b'\0'
                    intermediate_file.write(b'\0')
                    continue
                try:
                    text = chunk.decode('cp932')
                    if should_translate(text) and not cls._should_ignore(text, ignore_patterns):
                        key = f'<<<TRANS:{line_index}>>>'
                        textlines.append(TextLine(key, cleanup_text(text), b'\0'))
                        intermediate_file.write(key.encode('cp932'))
                        raw_translated_chunks.append(chunk)
                        line_index += 1
                    else:
                        intermediate_file.write(chunk + b'\0')
                    chunk = b''
                except Exception:
                    logger.exception('Failed to decode %r', chunk)
                    intermediate_file.write(chunk + b'\0')
                    chunk = b''
        # now if anything is left over in chunk, it goes at the end of the file
        intermediate_file.write(chunk)

        cp932_fixup = build_file_cp932_fixup(raw_translated_chunks)

        intermediate_file.seek(0)
        metadata = {}
        if cp932_fixup:
            metadata['cp932_fixup'] = cp932_fixup
        if update_body_size:
            # Archive tools (and likely the engine) recognize scripts by this field, so
            # insert_lines must keep it in step with the translated length.
            metadata['update_body_size'] = True
        return intermediate_file, textlines, metadata

    @classmethod
    def _insert_legacy(cls, intermediate_file, translation_dict, cp932_fixup=None, update_body_size=False):
        def get_trans(m):
            t = translation_dict[m.group(0).decode('cp932')]
            encoded = uncleanup_text(t.text).encode('cp932')
            if cp932_fixup:
                encoded = apply_cp932_fixup(encoded, cp932_fixup)
            return encoded + t.eol

        output_file = BytesIO()
        offset = cls.get_strings_offset(intermediate_file)
        output_file.write(intermediate_file.read(offset))

        data = intermediate_file.read()
        new_data = re.sub(rb'<<<TRANS:\d+>>>', get_trans, data)
        output_file.write(new_data)
        if update_body_size:
            output_file.seek(0)
            output_file.write((output_file.getbuffer().nbytes - HEADER_SIZE).to_bytes(4, 'little'))
        output_file.seek(0)

        return output_file
