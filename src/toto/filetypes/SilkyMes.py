"""Silky Engine ``.mes`` scripts (Silky's / Silky's Sakura; Inumani), as unpacked from ``Script.arc``.

A close relative of AI6WIN's format. The header has two little-endian offset tables (messages and
choice menus), then big-endian stack-machine bytecode. Display text is inline STR_CRYPT strings in
which each hiragana is compressed to one byte, so ASCII can only be written as STR_UNCRYPT. Lines
are hard-wrapped in the script with TO_NEW_STRING, and speaker names are separate PUSH_STR
arguments to a syscall rather than part of the text.
"""

import logging
import struct
from io import BytesIO
from typing import NamedTuple

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .mes_bytecode import (
    ScriptError,
    check_messages,
    check_targets,
    join_lines,
    jump_targets,
    pack_intermediate,
    relink,
    sweep,
    unpack_intermediate,
    wrap,
)
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

__all__ = ['ScriptError', 'SilkyMes', 'decrypt', 'encrypt', 'find_names', 'find_runs', 'parse_script', 'rebuild']

OP_STR_CRYPT = 0x0A  # display text, hiragana compressed
OP_STR_PLAIN = 0x0B  # display text, plain CP932 (unused by the games; how translations get in)
OP_SYSCALL = 0x18
OP_MESSAGE = 0x19  # starts a message; operand is its number
OP_MENU = 0x1A  # opens a choice menu; operand is not an offset
OP_LINK = 0x1B  # starts a menu option; operand is the next option's offset
OP_NEWLINE = 0x1C  # TO_NEW_STRING 0 is a line break between strings
OP_PUSH = 0x32
OP_PUSH_STR = 0x33
OP_ADD = 0x34
TEXT_OPS = frozenset({OP_STR_CRYPT, OP_STR_PLAIN})
OFFSET_OPS = frozenset({0x14, 0x15, 0x16, OP_LINK})
LINE_BREAK = 0

# Opcode -> operand kind. Operands follow TesterTesterov's table, except that 36 is operand-less:
# in the game it is always followed by 3D, the MUL/BINARY_OR pair of a flag update, as in AI6WIN.
OPCODES = {
    **dict.fromkeys((0x00, *range(0x02, 0x07), *range(0x0C, 0x10), 0x11, 0x17, OP_SYSCALL), ''),
    **dict.fromkeys((*range(0x34, 0x44), *range(0xFA, 0x100)), ''),
    0x01: 'I',
    OP_STR_CRYPT: 'S',
    OP_STR_PLAIN: 'S',
    0x10: 'B',
    0x14: 'I',
    0x15: 'I',
    0x16: 'I',
    OP_MESSAGE: 'I',
    OP_MENU: 'I',
    OP_LINK: 'I',
    OP_NEWLINE: 'B',
    OP_PUSH: 'i',
    OP_PUSH_STR: 'S',
}

# PUSH_STR name; PUSH 10; PUSH 5; ADD; PUSH 29; SYSCALL -- syscall 29 with argument 15 sets the
# speaker. Syscall 29 takes other arguments too, so the whole sequence is matched.
NAME_CALL = ((OP_PUSH, 10), (OP_PUSH, 5), (OP_ADD, None), (OP_PUSH, 29), (OP_SYSCALL, None))

HIRAGANA_FIRST = 0x829F  # ぁ, compressed to 0x01
HIRAGANA_LAST = 0x82F1  # ん, compressed to 0x53
COMPRESS_BIAS = 0x829E

ASCII_SPACE = ' \t'

INTERMEDIATE_MAGIC = b'TOTOSLK1'


def _is_lead(byte):
    return 0x81 <= byte <= 0x9F or 0xE0 <= byte <= 0xFC


def decrypt(raw):
    """STR_CRYPT bytes to plain CP932: a byte below 0x81 is a compressed hiragana."""
    out = bytearray()
    i = 0
    while i < len(raw):
        b = raw[i]
        if b < 0x81:
            out += (COMPRESS_BIAS + b).to_bytes(2, 'big')
            i += 1
        else:
            out += raw[i : i + 2]  # a trail byte may be below 0x81; it stays with its lead
            i += 2
    return bytes(out)


def encrypt(raw):
    """Plain CP932 to STR_CRYPT bytes, compressing hiragana as the game does.

    Raises ValueError for single-byte characters (ASCII, half-width katakana), which STR_CRYPT
    can't hold.
    """
    out = bytearray()
    i = 0
    while i < len(raw):
        if not _is_lead(raw[i]) or i + 1 >= len(raw):
            raise ValueError(f'single-byte character {raw[i:i + 1]!r} cannot be written as STR_CRYPT')
        code = int.from_bytes(raw[i : i + 2], 'big')
        if HIRAGANA_FIRST <= code <= HIRAGANA_LAST:
            out.append(code - COMPRESS_BIAS)
        else:
            out += raw[i : i + 2]
        i += 2
    return bytes(out)


def text_bytes(ins):
    """The plain CP932 bytes of a STR_CRYPT or STR_UNCRYPT instruction."""
    return decrypt(ins.arg) if ins.op == OP_STR_CRYPT else ins.arg


class Script(NamedTuple):
    message_offsets: tuple
    choice_offsets: tuple
    code_base: int
    instructions: list


def parse_script(data):
    """Parse and validate a script."""
    data = bytes(data)
    if len(data) < 8:
        raise ScriptError('too short for a Silky Engine script')
    count_a, count_b = struct.unpack_from('<II', data)
    base = 8 + 4 * (count_a + count_b)
    if base > len(data):
        raise ScriptError(f'header of {count_a} + {count_b} offsets runs past the end of the file')
    message_offsets = struct.unpack_from(f'<{count_a}I', data, 8)
    choice_offsets = struct.unpack_from(f'<{count_b}I', data, 8 + 4 * count_a)

    instructions = sweep(data, base, OPCODES)
    check_messages(instructions, OP_MESSAGE, message_offsets)
    check_targets(instructions, OFFSET_OPS)
    starts = {ins.offset for ins in instructions}
    for offset in choice_offsets:
        if offset not in starts:
            raise ScriptError(f'choice offset {offset:#x} is not an instruction')
    return Script(message_offsets, choice_offsets, base, instructions)


def find_runs(script):
    """Yield ``(start, end, lines)`` for each run of text: instruction indices and decoded lines.

    A run is text strings joined by TO_NEW_STRING 0. A run never continues onto an instruction
    that something jumps to, and a trailing line break stays outside it. Runs that don't decode
    as CP932 are skipped.
    """
    ins = script.instructions
    targets = jump_targets(ins, OFFSET_OPS)
    i = 0
    while i < len(ins):
        if ins[i].op not in TEXT_OPS:
            i += 1
            continue
        start = i
        lines = [text_bytes(ins[i])]
        i += 1
        while (
            i + 1 < len(ins)
            and ins[i].op == OP_NEWLINE
            and ins[i].arg == LINE_BREAK
            and ins[i + 1].op in TEXT_OPS
            and ins[i].offset not in targets
            and ins[i + 1].offset not in targets
        ):
            lines.append(text_bytes(ins[i + 1]))
            i += 2
        try:
            decoded = [line.decode('cp932') for line in lines]
        except UnicodeDecodeError:
            continue
        yield start, i, decoded


def find_names(script):
    """Yield ``(index, raw)`` for each PUSH_STR that sets the speaker name."""
    ins = script.instructions
    for i, push in enumerate(ins):
        if push.op != OP_PUSH_STR or i + len(NAME_CALL) >= len(ins):
            continue
        if all(
            ins[i + 1 + k].op == op and (arg is None or ins[i + 1 + k].arg == arg)
            for k, (op, arg) in enumerate(NAME_CALL)
        ):
            yield i, push.arg


def _decode_name(raw):
    try:
        return raw.decode('cp932')
    except UnicodeDecodeError:
        return None


def should_translate(text):
    return any(not ch.isascii() for ch in text)


class SilkyMes(TranslatableFile):
    # The scripts are hard-wrapped, so insertion breaks translated lines with TO_NEW_STRING.
    # Tested in Inumani: the window is 56 half-width characters wide (the engine's own wrapping
    # breaks mid-word there) and holds four lines, one more than the game ever uses.
    default_wrap = '\n'
    default_width = 56
    lines_per_message = 4

    @staticmethod
    def get_paths(workpath):
        # The game's scripts are upper case (OT_PROLOGUE01_01.MES)
        return sorted(p for p in workpath.rglob('*') if p.suffix.lower() == '.mes' and p.is_file())

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=True, **kwargs):
        """Extract dialogue, speaker names (once per script) and choices.

        With *unwrap* (the default) a message's lines are joined so insertion can rewrap them.
        """
        data = input_file.read()
        script = parse_script(data)

        textlines = []

        def export(text, speaker=None):
            if not text or not should_translate(text) or cls._should_ignore(text, ignore_patterns):
                return None
            key = f'<<<TRANS:{len(textlines)}>>>'
            textlines.append(TextLine(key, text, b'', speaker))
            return key

        # Walk the code in order so each name is exported before the first line it introduces,
        # and each run gets the speaker set since the start of its message.
        name_at = dict(find_names(script))
        runs = {start: (end, lines) for start, end, lines in find_runs(script)}
        names = {}  # name -> key (or None when not exported)
        items = []
        speaker = None
        i = 0
        ins = script.instructions
        while i < len(ins):
            if ins[i].op == OP_MESSAGE:
                speaker = None
            if i in name_at:
                name = _decode_name(name_at[i])
                if name is not None:
                    if name not in names:
                        names[name] = export(name)
                    speaker = name.strip(ASCII_SPACE) or None
            if i in runs:
                end, lines = runs[i]
                item = {'start': i, 'end': end, 'unwrap': unwrap}
                item['choice'] = i > 0 and ins[i - 1].op == OP_LINK
                item['body'] = export(join_lines(lines, unwrap), speaker)
                if item['body']:
                    items.append(item)
                i = end
                continue
            i += 1

        metadata = {}
        raw_strings = [text_bytes(x) for x in ins if x.op in TEXT_OPS] + list(name_at.values())
        fixup = build_file_cp932_fixup(raw_strings)
        if fixup:
            metadata['cp932_fixup'] = fixup

        header = {'items': items, 'names': {name: key for name, key in names.items() if key}}
        return BytesIO(pack_intermediate(INTERMEDIATE_MAGIC, header, data)), textlines, metadata

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None, width=None, **kwargs):
        header, data = unpack_intermediate(INTERMEDIATE_MAGIC, intermediate_file.read(), 'a Silky Engine')
        script = parse_script(data)
        runs = {start: (end, lines) for start, end, lines in find_runs(script)}

        def encode(text):
            raw = text.encode('cp932')
            return apply_cp932_fixup(raw, cp932_fixup) if cp932_fixup else raw

        def translation(key):
            if key is None or key not in translation_dict:
                return None
            return translation_dict[key].text.replace('\\n', '\n').strip(ASCII_SPACE)

        replacements = {}  # start index -> (end index, new bytes)

        for item in header['items']:
            end, lines = runs.get(item['start'], (None, None))
            if end != item['end']:
                raise ValueError(f'intermediate file does not match its script at instruction {item["start"]}')
            original = join_lines(lines, item['unwrap'])
            new = translation(item['body'])
            if new is None or new == original.strip(ASCII_SPACE):
                continue
            new_lines = [new.replace('\n', ' ')] if item['choice'] else wrap(new, width)
            if len(new_lines) > cls.lines_per_message:
                logger.warning(
                    '%s needs %d lines, but the text box holds %d: %r',
                    item['body'],
                    len(new_lines),
                    cls.lines_per_message,
                    new,
                )
            replacements[item['start']] = (
                end,
                bytes([OP_NEWLINE, LINE_BREAK]).join(_string(encode(line)) for line in new_lines),
            )

        for index, raw in find_names(script):
            name = _decode_name(raw)
            new = translation(header['names'].get(name))
            if new and new != name.strip(ASCII_SPACE):
                new = new.replace('\n', ' ')
                replacements[index] = (index + 1, bytes([OP_PUSH_STR]) + encode(new) + b'\0')

        return BytesIO(rebuild(data, script, replacements))


def _string(raw):
    """A text instruction for plain CP932 *raw*: STR_CRYPT when it can hold it, else STR_UNCRYPT."""
    try:
        return bytes([OP_STR_CRYPT]) + encrypt(raw) + b'\0'
    except ValueError:
        return bytes([OP_STR_PLAIN]) + raw + b'\0'


def rebuild(data, script, replacements):
    """Re-emit *script* with ``{start: (end, new_bytes)}`` substituted, remapping every offset."""
    code, new_pos = relink(data, script.code_base, script.instructions, replacements, OFFSET_OPS)
    messages = [new_pos[o] for o in script.message_offsets]
    choices = [new_pos[o] for o in script.choice_offsets]
    return struct.pack(f'<II{len(messages)}I{len(choices)}I', len(messages), len(choices), *messages, *choices) + code
