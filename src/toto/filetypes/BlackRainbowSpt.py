"""BLACKRAINBOW ADVDX ``.spt`` scripts (催眠術２, 裏催眠術２DL, 黒虹本2, 催眠術FullVoice).

A script is a 0x40-byte name, a 12-byte header and a flat list of length-prefixed
records. Strings are stored inline; dialogue (message records) is XOR-masked with a
4-byte per-game key, everything else is plain CP932. No record refers to another by
byte offset, so text can change length freely as long as the length fields follow.
"""

import json
import logging
import struct
import textwrap
from dataclasses import dataclass
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

NAME_SIZE = 0x40
HEADER_SIZE = NAME_SIZE + 12

OP_MESSAGE = 0x08
OP_CHOICE = 0x0E
OP_SCENE_NAME = 0x1D
OP_TITLE = 0x1E
TEXT_OPS = (OP_SCENE_NAME, OP_TITLE)

KNOWN_KEYS = (
    bytes.fromhex('01000000'),  # 催眠術２, 裏催眠術２DL, 黒虹本2
    bytes.fromhex('2bc52a3d'),  # 催眠術FullVoice
)

CRLF = '\r\n'
HANGING_INDENT = '　'
INDENTED_OPENERS = '「『（'
NAME_OPEN, NAME_CLOSE = '【', '】'
NO_VOICE = 0xFFFFFFFF
CHOICE_SEP = b'\\0'  # a literal backslash-zero, not NUL
TITLE_BREAK = b'\\n'  # likewise a literal backslash-n

INTERMEDIATE_MAGIC = b'TOTOBRS1'


def mask(data, key):
    """XOR *data* with the repeating *key*, starting from the key's first byte (its own inverse)."""
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def parse_records(data):
    """Split a script into ``[(opcode, payload), ...]``, validating the header against the records."""
    if len(data) < HEADER_SIZE:
        raise ValueError('too short for an ADVDX script header')
    _, count, body_size = struct.unpack_from('<III', data, NAME_SIZE)
    if body_size != len(data) - HEADER_SIZE:
        raise ValueError(f'body size {body_size:#x} does not match the file length')
    records = []
    pos = HEADER_SIZE
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError(f'truncated record header at {pos:#x}')
        op, n = struct.unpack_from('<II', data, pos)
        if pos + 8 + n > len(data):
            raise ValueError(f'record at {pos:#x} runs past the end of the file')
        records.append((op, data[pos + 8 : pos + 8 + n]))
        pos += 8 + n
    if len(records) != count:
        raise ValueError(f'header says {count} records, found {len(records)}')
    return records


def build_script(prefix, records):
    """Serialize *records* after the name and reserved field in *prefix* (the first 0x44 bytes)."""
    body = b''.join(struct.pack('<II', op, len(payload)) + payload for op, payload in records)
    return prefix[: NAME_SIZE + 4] + struct.pack('<II', len(records), len(body)) + body


@dataclass
class Message:
    msg_id: int
    voice: int
    speaker: int
    name: bytes
    text: bytes

    @classmethod
    def parse(cls, payload):
        if len(payload) < 20:
            raise ValueError('message record too short')
        msg_id, voice, speaker, name_len, text_len = struct.unpack_from('<5I', payload)
        if 20 + name_len + text_len != len(payload):
            raise ValueError(f'message {msg_id}: string lengths do not match the record length')
        return cls(msg_id, voice, speaker, payload[20 : 20 + name_len], payload[20 + name_len :])

    def to_bytes(self):
        header = struct.pack('<5I', self.msg_id, self.voice, self.speaker, len(self.name), len(self.text))
        return header + self.name + self.text

    def masked(self, key):
        return Message(self.msg_id, self.voice, self.speaker, mask(self.name, key), mask(self.text, key))


def _decodes(raw):
    try:
        raw.decode('cp932')
    except UnicodeDecodeError:
        return False
    return True


def detect_key(records):
    """The known key under which every message string decodes, or None if there are no messages."""
    raws = []
    for op, payload in records:
        if op == OP_MESSAGE:
            m = Message.parse(payload)
            raws += [m.name, m.text]
    if not any(raws):
        return None
    for key in KNOWN_KEYS:
        if all(_decodes(mask(raw, key)) for raw in raws):
            return key
    raise ValueError('messages do not decode with any known key; pass the 4-byte key with --key')


def split_speaker(text):
    """Split ``【display】\\r\\nbody`` into (display, body); display is None for narration."""
    if text.startswith(NAME_OPEN):
        end = text.find(NAME_CLOSE + CRLF)
        if end > 0:
            return text[1:end], text[end + 3 :]
        if text.endswith(NAME_CLOSE):
            return text[1:-1], None
    return None, text


def join_speaker(display, body):
    if display is None:
        return body
    if body is None:
        return NAME_OPEN + display + NAME_CLOSE
    return NAME_OPEN + display + NAME_CLOSE + CRLF + body


def _is_word_char(ch):
    return ch.isascii() and not ch.isspace()


def join_lines(lines):
    """Join a message's lines, dropping the hanging indent of continuation lines."""
    out = lines[0]
    for line in lines[1:]:
        rest = line.removeprefix(HANGING_INDENT)
        if out and rest and _is_word_char(out[-1]) and _is_word_char(rest[0]):
            out += ' '
        out += rest
    return out


def uses_indent(lines):
    """Whether continuation lines of this body hang under an opening quote."""
    if len(lines) > 1:
        return all(line.startswith(HANGING_INDENT) for line in lines[1:])
    return lines[0][:1] in INDENTED_OPENERS


def should_translate(text):
    return any(not ch.isascii() for ch in text)


def parse_choice(payload):
    (n,) = struct.unpack_from('<I', payload, 8)
    if 12 + n != len(payload):
        raise ValueError('choice string length does not match the record length')
    parts = payload[12:].split(CHOICE_SEP)
    return payload[:8], parts[:-1], parts[-1]


def build_choice(head, options, tail):
    raw = b''.join(o + CHOICE_SEP for o in options) + tail
    return head + struct.pack('<I', len(raw)) + raw


def parse_text(payload):
    (n,) = struct.unpack_from('<I', payload)
    if 4 + n != len(payload):
        raise ValueError('string length does not match the record length')
    return payload[4:]


class BlackRainbowSpt(TranslatableFile):
    # The engine doesn't wrap, so insertion breaks translated text at the wrap width and
    # continues messages longer than two lines on extra pages. About 55 columns of
    # English fit the message box (measured in 催眠術２).
    default_wrap = CRLF
    default_width = 55
    lines_per_page = 2

    @staticmethod
    def get_paths(workpath):
        return sorted(workpath.glob('**/*.spt'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=True, key=None, **kwargs):
        """Extract dialogue, speaker display names, choices and scene titles.

        *key* is the hex message key; by default each known key is tried in turn.
        With *unwrap* (the default) a message's lines are joined so insertion can rewrap them.
        """
        data = input_file.read()
        records = parse_records(data)
        if key is not None:
            key = bytes.fromhex(key) if isinstance(key, str) else bytes(key)
            if not key:
                raise ValueError('the message key must not be empty')
        else:
            key = detect_key(records)

        textlines = []
        items = []
        raws = []

        def export(text, raw):
            if not text or not should_translate(text) or cls._should_ignore(text, ignore_patterns):
                return None
            k = f'<<<TRANS:{len(textlines)}>>>'
            textlines.append(TextLine(k, text, b''))
            raws.append(raw)
            return k

        out = []
        for idx, (op, payload) in enumerate(records):
            if op == OP_MESSAGE:
                m = Message.parse(payload)
                if key is not None:
                    m = m.masked(key)
                try:
                    text = m.text.decode('cp932')
                    m.name.decode('cp932')
                except UnicodeDecodeError:
                    raise ValueError(
                        f'message {m.msg_id} does not decode as CP932 with key {key.hex() if key else None}; '
                        'check --key'
                    ) from None
                display, body = split_speaker(text) if m.name else (None, text)
                item = {'r': idx, 'unwrap': unwrap}
                if display is not None:
                    item['name'] = export(display, display.encode('cp932'))
                if body is not None:
                    lines = body.split(CRLF)
                    item['indent'] = uses_indent(lines)
                    item['body'] = export(join_lines(lines) if unwrap else '\n'.join(lines), m.text)
                items.append(item)
                out.append((op, m.to_bytes()))
                continue
            if op == OP_CHOICE:
                _, options, _ = parse_choice(payload)
                keys = [export(o.decode('cp932', 'replace'), o) if _decodes(o) else None for o in options]
                items.append({'r': idx, 'options': keys})
            elif op in TEXT_OPS:
                raw = parse_text(payload)
                if _decodes(raw):
                    items.append({'r': idx, 'text': export(raw.decode('cp932').replace('\\n', '\n'), raw)})
            out.append((op, payload))

        header = json.dumps({'items': items}).encode('utf-8')
        intermediate = BytesIO()
        intermediate.write(INTERMEDIATE_MAGIC + struct.pack('<I', len(header)) + header)
        intermediate.write(build_script(data, out))
        intermediate.seek(0)

        metadata = {}
        if key is not None:
            metadata['encryption_key'] = key
        fixup = build_file_cp932_fixup(raws)
        if fixup:
            metadata['cp932_fixup'] = fixup
        return intermediate, textlines, metadata

    @classmethod
    def insert_lines(
        cls, intermediate_file, translation_dict, encryption_key=None, cp932_fixup=None, width=None, **kwargs
    ):
        data = intermediate_file.read()
        if not data.startswith(INTERMEDIATE_MAGIC):
            raise ValueError('not a BLACKRAINBOW intermediate file')
        pos = len(INTERMEDIATE_MAGIC)
        (header_len,) = struct.unpack_from('<I', data, pos)
        pos += 4
        items = json.loads(data[pos : pos + header_len].decode('utf-8'))['items']
        script = data[pos + header_len :]
        records = parse_records(script)

        def encode(text):
            raw = text.encode('cp932')
            return apply_cp932_fixup(raw, cp932_fixup) if cp932_fixup else raw

        def translation(k):
            return translation_dict[k].text.replace('\\n', '\n')

        by_record = {item['r']: item for item in items}
        out = []
        for idx, (op, payload) in enumerate(records):
            item = by_record.get(idx)
            if item is None:
                out.append((op, payload))
            elif op == OP_MESSAGE:
                m = Message.parse(payload)
                for n, text in enumerate(cls._message_pages(m, item, translation, encode, width)):
                    # Extra pages share the message ID: the engine's read flags (.rdd) are a
                    # byte per ID, so a new ID could index past the saved array.
                    page = Message(m.msg_id, m.voice if n == 0 else NO_VOICE, m.speaker, m.name, text)
                    if encryption_key is not None:
                        page = page.masked(encryption_key)
                    out.append((op, page.to_bytes()))
            elif op == OP_CHOICE:
                head, options, tail = parse_choice(payload)
                options = [
                    o if k is None else encode(translation(k)) for o, k in zip(options, item['options'], strict=True)
                ]
                out.append((op, build_choice(head, options, tail)))
            elif item.get('text') is not None:
                raw = encode(translation(item['text'])).replace(b'\n', TITLE_BREAK)
                out.append((op, struct.pack('<I', len(raw)) + raw))
            else:
                out.append((op, payload))

        return BytesIO(build_script(script, out))

    @classmethod
    def _message_pages(cls, m, item, translation, encode, width):
        """The new (unmasked) texts of message *m*, one per page; the original bytes when nothing changed."""
        display, body = split_speaker(m.text.decode('cp932')) if m.name else (None, m.text.decode('cp932'))
        changed = False
        if item.get('name') is not None:
            new = translation(item['name'])
            changed |= new != display
            display = new
        pages = [body]
        if item.get('body') is not None:
            lines = body.split(CRLF)
            original = join_lines(lines) if item['unwrap'] else '\n'.join(lines)
            new = translation(item['body'])
            if new != original:
                changed = True
                pages = cls._paginate(new, width, HANGING_INDENT if item.get('indent') else '')
                if len(pages) > 1:
                    logger.info('Message %d split into %d pages: %r', m.msg_id, len(pages), new)
        if not changed:
            return [m.text]
        return [encode(join_speaker(display, page)) for page in pages]

    @classmethod
    def _paginate(cls, text, width, indent):
        """Wrap *text* into lines of at most *width* columns and group them into pages.

        The engine does no wrapping of its own, so every line break is inserted here; a
        newline in *text* is a hard break. Continuation lines get *indent* (the full-width
        hanging indent under an opening quote), except at the top of a new page.
        """
        lines = []
        for piece in text.split('\n'):
            first = indent if lines else ''
            if width:
                wrapped = textwrap.wrap(
                    piece, width=width, initial_indent=first, subsequent_indent=indent, break_on_hyphens=False
                )
            else:
                wrapped = [first + piece]
            lines.extend(wrapped or [first])
        if not width:
            return [CRLF.join(lines)]
        pages = []
        for i in range(0, len(lines), cls.lines_per_page):
            chunk = lines[i : i + cls.lines_per_page]
            chunk[0] = chunk[0].removeprefix(indent) if i and indent else chunk[0]
            pages.append(CRLF.join(chunk))
        return pages
