"""AI6WIN ``.mes`` scripts (elf and Silky's; 麻呂の患者はガテン系 1-3), as unpacked from ``mes.arc``.

A script is a little-endian table of MESSAGE offsets followed by stack-machine bytecode
whose operands are big-endian. All display text is in STR_PRIMARY instructions, inline in
the code, and jumps and choice links are code-relative byte offsets. Changing the length of
any text therefore means rebuilding the code and remapping every offset and the header.
"""

import json
import logging
import re
import struct
import textwrap
from io import BytesIO
from typing import NamedTuple

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)

OP_STR = 0x0A  # STR_PRIMARY: display text
OP_MESSAGE = 0x19  # starts a message; operand is its number
OP_CHOICE = 0x1A  # starts a menu option; operand is the next option's offset
OP_ESCAPE = 0x1B  # ESCAPE 0 is a line break between strings
OFFSET_OPS = frozenset({0x14, 0x15, 0x16, OP_CHOICE})  # operand is a code offset
LINE_BREAK = 0

_OPERANDS = {
    0x0A: 'S',
    0x0B: 'S',
    0x14: 'I',
    0x15: 'I',
    0x16: 'I',
    0x19: 'I',
    0x1A: 'I',
    0x1B: 'B',
    0x32: 'i',
    0x33: 'S',
}
_NO_OPERAND = (*range(0x00, 0x14), 0x17, 0x18, 0x1D, *range(0x34, 0x44), *range(0xFA, 0x100))


def _table(extra=None):
    table = dict.fromkeys(_NO_OPERAND, '')
    table.update(_OPERANDS)
    table.update(extra or {})
    return table


# Opcode tables, in order of preference when a script fits both. They differ only in whether
# STLOC_VAR (10) and MUL (36) take a byte operand. In the elf games 36 is always followed by
# BINARY_OR (3D) as the second half of a flag-setting expression, so the plain table is right
# there; the byte-operand table is for games where the operand form exists.
OPCODE_TABLES = {'plain': _table(), 'byte-operand': _table({0x10: 'B', 0x36: 'B'})}

NAME_TAG = re.compile('〈([^〉\n]+)〉：')
ASCII_SPACE = ' \t'

INTERMEDIATE_MAGIC = b'TOTOAI61'


class ScriptError(ValueError):
    """The data is not a valid AI6WIN script."""


class Instruction(NamedTuple):
    offset: int  # code-relative
    op: int
    arg: object  # int, bytes or None
    size: int


class Script(NamedTuple):
    message_offsets: tuple
    code_base: int
    instructions: list


def _sweep(data, base, table):
    instructions = []
    pos = base
    end = len(data)
    while pos < end:
        op = data[pos]
        kind = table.get(op)
        if kind is None:
            raise ScriptError(f'unknown opcode {op:#04x} at {pos:#x}')
        q = pos + 1
        if kind == 'S':
            nul = data.find(b'\0', q)
            if nul < 0:
                raise ScriptError(f'unterminated string at {pos:#x}')
            arg = data[q:nul]
            q = nul + 1
        elif kind in ('I', 'i'):
            if q + 4 > end:
                raise ScriptError(f'truncated operand at {pos:#x}')
            (arg,) = struct.unpack_from('>' + kind, data, q)
            q += 4
        elif kind == 'B':
            if q >= end:
                raise ScriptError(f'truncated operand at {pos:#x}')
            arg = data[q]
            q += 1
        else:
            arg = None
        instructions.append(Instruction(pos - base, op, arg, q - pos))
        pos = q
    return instructions


def _validate(instructions, message_offsets):
    starts = {ins.offset for ins in instructions}
    messages = [ins for ins in instructions if ins.op == OP_MESSAGE]
    if tuple(m.offset for m in messages) != message_offsets:
        raise ScriptError('header does not match the MESSAGE instructions')
    if any(m.arg != n for n, m in enumerate(messages)):
        raise ScriptError('MESSAGE numbers are not sequential')
    for ins in instructions:
        if ins.op in OFFSET_OPS and ins.arg not in starts:
            raise ScriptError(f'offset {ins.arg:#x} at {ins.offset:#x} is not an instruction')


def parse_script(data):
    """Parse and validate a script, choosing whichever opcode table it is consistent with."""
    data = bytes(data)
    if len(data) < 4:
        raise ScriptError('too short for an AI6WIN script')
    (count,) = struct.unpack_from('<I', data)
    base = 4 + 4 * count
    if base > len(data):
        raise ScriptError(f'header of {count} messages runs past the end of the file')
    message_offsets = struct.unpack_from(f'<{count}I', data, 4)

    parsed, errors = [], []
    for table in OPCODE_TABLES.values():
        try:
            instructions = _sweep(data, base, table)
            _validate(instructions, message_offsets)
        except ScriptError as e:
            errors.append(e)
        else:
            parsed.append(instructions)
    if not parsed:
        raise errors[0]
    if len(parsed) > 1:
        texts = [[(i.offset, i.arg) for i in instructions if i.op == OP_STR] for instructions in parsed]
        if texts[0] != texts[1]:
            raise ScriptError('ambiguous: both opcode tables fit but place the text differently')
    return Script(message_offsets, base, parsed[0])


def _jump_targets(script):
    return {ins.arg for ins in script.instructions if ins.op in OFFSET_OPS}


def find_runs(script):
    """Yield ``(start, end, lines)`` for each run of text: instruction indices and decoded lines.

    A run is STR_PRIMARY strings joined directly (shown together) or by ESCAPE 0 (a line
    break). A run never continues onto an instruction that something jumps to. Runs that
    don't decode as CP932 are skipped.
    """
    ins = script.instructions
    targets = _jump_targets(script)
    i = 0
    while i < len(ins):
        if ins[i].op != OP_STR:
            i += 1
            continue
        start = i
        lines = [[ins[i].arg]]
        i += 1
        while i < len(ins) and ins[i].offset not in targets:
            if ins[i].op == OP_STR:
                lines[-1].append(ins[i].arg)
                i += 1
            elif (
                ins[i].op == OP_ESCAPE
                and ins[i].arg == LINE_BREAK
                and i + 1 < len(ins)
                and ins[i + 1].op == OP_STR
                and ins[i + 1].offset not in targets
            ):
                lines.append([ins[i + 1].arg])
                i += 2
            else:
                break
        try:
            decoded = [b''.join(parts).decode('cp932') for parts in lines]
        except UnicodeDecodeError:
            continue
        yield start, i, decoded


def _is_word_char(ch):
    return ch.isascii() and not ch.isspace()


def join_lines(lines, unwrap):
    """One string for a run's lines: joined with a space between words (unwrap), or newlines."""
    if not unwrap:
        return '\n'.join(lines)
    out = lines[0]
    for line in lines[1:]:
        if out and line and _is_word_char(out[-1]) and _is_word_char(line[0]):
            out += ' '
        out += line
    return out


def split_name(text):
    """Split ``〈name〉：body`` into ``(name, body)``; name is None for narration."""
    m = NAME_TAG.match(text)
    if m:
        return m.group(1), text[m.end() :]
    return None, text


def should_translate(text):
    return any(not ch.isascii() for ch in text)


def _intermediate(header, data):
    raw = json.dumps(header).encode('utf-8')
    return INTERMEDIATE_MAGIC + struct.pack('<I', len(raw)) + raw + data


def _read_intermediate(raw):
    if not raw.startswith(INTERMEDIATE_MAGIC):
        raise ValueError('not an AI6WIN intermediate file')
    pos = len(INTERMEDIATE_MAGIC)
    (n,) = struct.unpack_from('<I', raw, pos)
    pos += 4
    return json.loads(raw[pos : pos + n].decode('utf-8')), raw[pos + n :]


class Ai6WinMes(TranslatableFile):
    # The engine's own wrapping doesn't suit English, so insertion breaks lines with
    # ESCAPE 0. About 60 characters, counting the 〈name〉： tag, fit the window.
    default_wrap = '\n'
    default_width = 60

    @staticmethod
    def get_paths(workpath):
        return sorted(workpath.glob('**/*.mes'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=(), unwrap=True, **kwargs):
        """Extract dialogue bodies, speaker names (once per script) and choices.

        With *unwrap* (the default) a message's lines are joined so insertion can rewrap them.
        """
        data = input_file.read()
        script = parse_script(data)

        textlines = []
        names = {}

        def export(text):
            if not text or not should_translate(text) or cls._should_ignore(text, ignore_patterns):
                return None
            key = f'<<<TRANS:{len(textlines)}>>>'
            textlines.append(TextLine(key, text, b''))
            return key

        items = []
        for start, end, lines in find_runs(script):
            name, body = split_name(join_lines(lines, unwrap))
            item = {'start': start, 'end': end, 'unwrap': unwrap}
            item['choice'] = start > 0 and script.instructions[start - 1].op == OP_CHOICE
            if name is not None:
                if name not in names:
                    names[name] = export(name)
                item['name'] = names[name]
            item['body'] = export(body)
            if item.get('name') or item['body']:
                items.append(item)

        metadata = {}
        fixup = build_file_cp932_fixup(ins.arg for ins in script.instructions if ins.op == OP_STR)
        if fixup:
            metadata['cp932_fixup'] = fixup

        intermediate = BytesIO(_intermediate({'items': items}, data))
        return intermediate, textlines, metadata

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None, width=None, **kwargs):
        header, data = _read_intermediate(intermediate_file.read())
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
            end, lines = runs[item['start']]
            if end != item['end']:
                raise ValueError(f'intermediate file does not match its script at instruction {item["start"]}')
            new = cls._new_lines(lines, item, translation, width)
            if new is not None:
                replacements[item['start']] = (
                    end,
                    bytes([OP_ESCAPE, LINE_BREAK]).join(bytes([OP_STR]) + encode(line) + b'\0' for line in new),
                )

        return BytesIO(rebuild(data, script, replacements))

    @classmethod
    def _new_lines(cls, lines, item, translation, width):
        """The new lines of a run, or None when nothing in it changed."""
        name, body = split_name(join_lines(lines, item['unwrap']))

        new_name = translation(item.get('name'))
        name_changed = bool(new_name) and new_name != name.strip(ASCII_SPACE)
        if name_changed:
            name = new_name.replace('\n', ' ')  # the tag must stay on one line

        new_body = translation(item.get('body'))
        body_changed = new_body is not None and new_body != body.strip(ASCII_SPACE)

        tag = f'〈{name}〉：' if name is not None else ''
        if body_changed:
            if item['choice']:
                return [tag + new_body.replace('\n', ' ')]
            return wrap(new_body, width, tag)
        if name_changed:
            # Keep the body's own lines: untranslated Japanese is left for the engine to wrap
            return [tag + split_name(lines[0])[1], *lines[1:]]
        return None


def wrap(text, width, tag=''):
    """Break *text* into lines of at most *width* characters, *tag* leading the first line.

    A newline in *text* is a hard break. With no *width* only the hard breaks are made.
    """
    lines = []
    for piece in text.split('\n'):
        first = '' if lines else tag
        if width:
            wrapped = textwrap.wrap(piece, width=width, initial_indent=first, break_on_hyphens=False)
        else:
            wrapped = [first + piece]
        lines.extend(wrapped or [first])
    return lines


def rebuild(data, script, replacements):
    """Re-emit *script* with ``{start: (end, new_bytes)}`` substituted, remapping every offset."""
    base = script.code_base
    ins = script.instructions
    new_pos = {}
    chunks = []  # (op, bytes)
    pos = 0
    i = 0
    while i < len(ins):
        if i in replacements:
            end, raw = replacements[i]
            for j in range(i, end):
                new_pos[ins[j].offset] = pos
            chunks.append((OP_STR, raw))
            pos += len(raw)
            i = end
            continue
        start = base + ins[i].offset
        new_pos[ins[i].offset] = pos
        chunks.append((ins[i].op, data[start : start + ins[i].size]))
        pos += ins[i].size
        i += 1

    code = bytearray()
    for op, raw in chunks:
        if op in OFFSET_OPS:
            (target,) = struct.unpack_from('>I', raw, 1)
            code += raw[:1] + struct.pack('>I', new_pos[target]) + raw[5:]
        else:
            code += raw
    offsets = [new_pos[o] for o in script.message_offsets]
    return struct.pack(f'<I{len(offsets)}I', len(offsets), *offsets) + bytes(code)
