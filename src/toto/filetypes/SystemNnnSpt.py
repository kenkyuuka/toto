"""System-NNN ``.spt`` scripts (Cyc, CYCLET and the engine's many other licensees).

A script is a 32-word header followed by a stream of length-prefixed records that runs to
end of file; the whole file is bitwise-NOT encrypted. Text lives in two table records:
messages (dialogue and narration, with the speaker on the first line) and strings
(choices, but also labels and file names). Code refers to text by table index only, so
the tables can be rebuilt with text of any length as long as the header pointers that
follow them are moved.
"""

import json
import logging
import struct
import textwrap
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .SystemNnnXtx import xtx_safe
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

HEADER_WORDS = 32
IDENTIFY_DATA = 0x66660001
CODE_DATA_HEADER0 = 0x55550001
CODE_DATA_TABLE = 0x55550002
CODE_SELECT = 0x22220006
SIGNATURE = struct.pack('<II', IDENTIFY_DATA, CODE_DATA_HEADER0)  # header words 1-2

# Header words holding (count, pointer) of each text table
TABLES = {'messages': (4, 5), 'strings': (6, 7)}
# Header words holding word indices of tables; any that lie past a rebuilt table must move
POINTER_WORDS = (5, 7, 9, 16, 17, 18, 19, 20, 21, 22, 23)

CRLF = '\r\n'
ASCII_SPACE = ' \t'
HANGING_INDENT = '　'
SPEECH_OPENERS = '「『（'
NOT_IN_NAMES = '「『（。、'

INTERMEDIATE_MAGIC = b'TOTONNN1'

_NOT = bytes(range(255, -1, -1))


def crypt(data):
    """Encrypt or decrypt a script: the engine's default key is a plain bitwise NOT."""
    return bytes(data).translate(_NOT)


def _words(data):
    return struct.unpack(f'<{len(data) // 4}I', data[: len(data) // 4 * 4])


def iter_records(data):
    """Yield ``(pc, words)`` for each record of a decrypted script, from the header to end of file."""
    w = _words(data)
    pc = 0
    while pc < len(w):
        size = w[pc]
        if size < 3 or pc + size > len(w):
            raise ValueError(f'bad record size {size} at word {pc}')
        yield pc, w[pc : pc + size]
        pc += size


def read_table(data, which):
    """The slots of a text table, in index order: each string, its NUL and the padding after it."""
    w = _words(data)
    count_word, ptr_word = TABLES[which]
    count, ptr = w[count_word], w[ptr_word]
    rec = ptr - 4
    end = rec + w[rec]
    ptrs = w[ptr : ptr + count]
    following = [*sorted(set(ptrs)), end]
    slots = []
    for p in ptrs:
        nxt = following[following.index(p) + 1]
        slot = data[p * 4 : nxt * 4]
        if b'\0' not in slot:
            raise ValueError(f'{which} table: string at word {p} is not terminated')
        slots.append(slot)
    return slots


def rebuild_tables(data, new_slots):
    """Rebuild text tables from ``{table: slots}`` (read with :func:`read_table` beforehand).

    A table's pointer array holds absolute word indices, so once an earlier table changes
    size every later text table's pointers are stale. Tables are therefore rebuilt in file
    order, each from slots read before anything moved, with freshly computed pointers.
    """
    w = _words(data)
    for which in sorted(new_slots, key=lambda t: w[TABLES[t][1]]):
        data = _replace_table(data, which, new_slots[which])
    return data


def _replace_table(data, which, slots):
    """Rebuild one text table from *slots* and move the header pointers that follow it."""
    w = list(_words(data))
    count_word, ptr_word = TABLES[which]
    if len(slots) != w[count_word]:
        raise ValueError(f'{which} table has {w[count_word]} entries, got {len(slots)}')
    rec = w[ptr_word] - 4
    old_end = rec + w[rec]
    ptrs = []
    pos = rec + 4 + len(slots)
    for slot in slots:
        ptrs.append(pos)
        pos += len(slot) // 4
    size = pos - rec
    record = struct.pack(f'<4I{len(ptrs)}I', size, *w[rec + 1 : rec + 4], *ptrs) + b''.join(slots)
    delta = size - (old_end - rec)
    header = w[:HEADER_WORDS]
    for i in POINTER_WORDS:
        if header[i] and header[i] >= old_end:
            header[i] += delta
    return struct.pack(f'<{HEADER_WORDS}I', *header) + data[HEADER_WORDS * 4 : rec * 4] + record + data[old_end * 4 :]


def make_slot(raw):
    """A NUL-terminated, zero-padded slot for a new string."""
    raw += b'\0'
    return raw + b'\0' * (-len(raw) % 4)


def select_strings(data):
    """String-table indices shown by SELECT: ``{index: is_prompt}``.

    A prompt's first byte is a marker (``%``) that the engine skips.
    """
    found = {}
    for _, r in iter_records(data):
        if r[1:3] != (0x66660006, CODE_SELECT):
            continue
        options = r[3]
        prompts = r[-1] if len(r) != options + 5 else 0
        for n, idx in enumerate(r[4 : 4 + prompts + options]):
            found.setdefault(idx, n < prompts)
    return found


def decode(raw):
    try:
        return raw.decode('cp932')
    except UnicodeDecodeError:
        return None


def should_translate(text):
    return any(not ch.isascii() for ch in text)


def looks_like_speech(lines):
    """Whether *lines* start with a speaker name line followed by quoted speech."""
    return (
        len(lines) > 1
        and lines[0]
        and lines[1][:1] in SPEECH_OPENERS
        and not any(ch in lines[0] for ch in NOT_IN_NAMES)
    )


def join_lines(lines):
    """Join a message's lines, dropping the hanging indent of continuation lines."""
    out = lines[0]
    for line in lines[1:]:
        rest = line.removeprefix(HANGING_INDENT)
        if out and rest and out[-1].isascii() and not out[-1].isspace() and rest[0].isascii() and not rest[0].isspace():
            out += ' '
        out += rest
    return out


def uses_indent(lines):
    """Whether continuation lines of this body hang under an opening quote."""
    if len(lines) > 1:
        return all(line.startswith(HANGING_INDENT) for line in lines[1:])
    return lines[0][:1] in SPEECH_OPENERS


def wrap(text, width, indent):
    """Break *text* into lines of at most *width* columns; a newline in *text* is a hard break."""
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
    return lines


def _load(data):
    """Return ``(plaintext, encrypted)`` for a script, or raise if it isn't one."""
    if data[4:12] == SIGNATURE:
        return bytes(data), False
    if crypt(data[4:12]) == SIGNATURE:
        return crypt(data), True
    raise ValueError('not a System-NNN script (no SPTHEADER0 header)')


class SystemNnnSpt(TranslatableFile):
    # The engine doesn't wrap: every line break is in the script. Original lines are at most
    # 24-27 full-width characters (48-54 bytes) and messages at most 4 lines.
    default_wrap = CRLF
    default_width = 48
    max_lines = 4

    @staticmethod
    def get_paths(workpath):
        return sorted(workpath.glob('**/*.spt'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=True, **kwargs):
        """Extract dialogue, speaker names (once per script) and choices.

        With *unwrap* (the default) a message's lines are joined so insertion can rewrap them.
        """
        data, encrypted = _load(input_file.read())

        textlines = []
        raws = []

        def export(text, raw):
            if not text or not should_translate(text) or cls._should_ignore(text, ignore_patterns):
                return None
            key = f'<<<TRANS:{len(textlines)}>>>'
            textlines.append(TextLine(key, text, b''))
            raws.append(raw)
            return key

        raw_messages = [s[: s.index(b'\0')] for s in read_table(data, 'messages')]
        messages = [decode(raw) for raw in raw_messages]
        split = [text is not None and looks_like_speech(text.split(CRLF)) for text in messages]
        # A first line that is a speaker elsewhere in the script is a name even when no quote
        # follows it (e.g. two names over one line of speech)
        speakers = {text.split(CRLF)[0] for text, s in zip(messages, split, strict=True) if s}
        for i, text in enumerate(messages):
            if text is not None and not split[i]:
                lines = text.split(CRLF)
                split[i] = len(lines) > 1 and lines[0] in speakers

        names = {}  # speaker name -> key; each name is exported once per file
        items = []
        for i, text in enumerate(messages):
            if text is None:
                continue
            lines = text.split(CRLF)
            item = {'i': i, 'unwrap': unwrap, 'split': split[i]}
            if split[i]:
                name = lines.pop(0)
                if name not in names:
                    names[name] = export(name, raw_messages[i].split(CRLF.encode())[0])
                item['name'] = names[name]
            item['indent'] = uses_indent(lines)
            item['body'] = export(join_lines(lines) if unwrap else '\n'.join(lines), raw_messages[i])
            if item.get('name') or item['body']:
                items.append(item)

        strings = read_table(data, 'strings')
        choices = []
        for idx, prompt in select_strings(data).items():
            raw = strings[idx][: strings[idx].index(b'\0')]
            text = decode(raw[1:] if prompt else raw)
            if text is not None:
                key = export(text, raw)
                if key:
                    choices.append({'i': idx, 'prompt': prompt, 'key': key})

        header = json.dumps({'encrypted': encrypted, 'messages': items, 'choices': choices}).encode('utf-8')
        intermediate = BytesIO()
        intermediate.write(INTERMEDIATE_MAGIC + struct.pack('<I', len(header)) + header + data)
        intermediate.seek(0)

        metadata = {}
        fixup = build_file_cp932_fixup(raws)
        if fixup:
            metadata['cp932_fixup'] = fixup
        return intermediate, textlines, metadata

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None, width=None, **kwargs):
        raw = intermediate_file.read()
        if not raw.startswith(INTERMEDIATE_MAGIC):
            raise ValueError('not a System-NNN intermediate file')
        pos = len(INTERMEDIATE_MAGIC)
        (header_len,) = struct.unpack_from('<I', raw, pos)
        pos += 4
        header = json.loads(raw[pos : pos + header_len].decode('utf-8'))
        data = raw[pos + header_len :]

        def encode(text):
            out = text.encode('cp932')
            return apply_cp932_fixup(out, cp932_fixup) if cp932_fixup else out

        def translation(key):
            """The translation of *key* without surrounding ASCII spaces, or None when there is none."""
            if key is None or key not in translation_dict:
                return None
            return translation_dict[key].text.replace('\\n', '\n').strip(ASCII_SPACE)

        slots = {which: read_table(data, which) for which in TABLES}
        messages, strings = slots['messages'], slots['strings']
        for item in header['messages']:
            text = messages[item['i']][: messages[item['i']].index(b'\0')].decode('cp932')
            new = cls._message(text, item, translation, width)
            if new is not None:
                messages[item['i']] = make_slot(encode(new))

        for item in header['choices']:
            raw_text = strings[item['i']][: strings[item['i']].index(b'\0')]
            marker, original = (raw_text[:1], raw_text[1:]) if item['prompt'] else (b'', raw_text)
            new = translation(item['key'])
            if new is not None and new != original.decode('cp932').strip(ASCII_SPACE):
                strings[item['i']] = make_slot(marker + encode(new))

        data = rebuild_tables(data, slots)

        return BytesIO(crypt(data) if header['encrypted'] else data)

    @classmethod
    def _message(cls, text, item, translation, width):
        """The new text of a message, or None when nothing in it changed."""
        lines = text.split(CRLF)
        name = lines.pop(0) if item['split'] else None
        changed = False

        new_name = translation(item.get('name'))
        if new_name is not None and new_name != name.strip(ASCII_SPACE):
            # A space would split the name in namecol.xtx, which must match it exactly
            name = xtx_safe(new_name.replace('\n', ' '))
            changed = True

        new_body = translation(item.get('body'))
        original = join_lines(lines) if item['unwrap'] else '\n'.join(lines)
        if new_body is not None and new_body != original.strip(ASCII_SPACE):
            lines = wrap(new_body, width, HANGING_INDENT if item['indent'] else '')
            if len(lines) > cls.max_lines:
                logger.warning(
                    'Message %d has %d lines (the window shows %d): %r', item['i'], len(lines), cls.max_lines, new_body
                )
            changed = True

        if not changed:
            return None
        return CRLF.join(([name] if name is not None else []) + lines)
