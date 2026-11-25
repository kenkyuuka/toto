"""Bytecode plumbing shared by the ``.mes`` handlers of elf and Silky's engines (AI6WIN, Silky Engine).

Both engines compile scripts to a stack machine whose instructions are a one-byte opcode and at
most one operand: a NUL-terminated string, a big-endian u32, or a byte. Jumps are byte offsets
relative to the start of the code, so changing the length of any text means re-emitting the code
and remapping every offset. The engines differ in their headers, opcode tables and text encoding,
which stay in the handlers.
"""

import json
import struct
import textwrap
from typing import NamedTuple


class ScriptError(ValueError):
    """The data is not a valid script for the handler's engine."""


class Instruction(NamedTuple):
    offset: int  # code-relative
    op: int
    arg: object  # int, bytes or None
    size: int


def sweep(data, base, table):
    """Decode *data* from *base* to EOF into instructions, using *table* (opcode -> operand kind).

    Operand kinds: ``'S'`` NUL-terminated string, ``'I'``/``'i'`` big-endian u32/s32, ``'B'`` a
    byte, ``''`` none. An opcode missing from the table means the sweep has lost sync.
    """
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


def check_messages(instructions, message_op, message_offsets):
    """Check that the MESSAGE instructions sit at *message_offsets* and are numbered 0, 1, 2 …"""
    messages = [ins for ins in instructions if ins.op == message_op]
    if tuple(m.offset for m in messages) != tuple(message_offsets):
        raise ScriptError('header does not match the MESSAGE instructions')
    if any(m.arg != n for n, m in enumerate(messages)):
        raise ScriptError('MESSAGE numbers are not sequential')


def check_targets(instructions, offset_ops):
    """Check that the operand of every instruction in *offset_ops* is an instruction start."""
    starts = {ins.offset for ins in instructions}
    for ins in instructions:
        if ins.op in offset_ops and ins.arg not in starts:
            raise ScriptError(f'offset {ins.arg:#x} at {ins.offset:#x} is not an instruction')


def jump_targets(instructions, offset_ops):
    return {ins.arg for ins in instructions if ins.op in offset_ops}


def relink(data, base, instructions, replacements, offset_ops):
    """Re-emit the code with ``{start: (end, new_bytes)}`` substituted and every offset remapped.

    A replacement takes the place of instructions ``start`` to ``end - 1``, and offsets to any of
    them land on its first byte. Replacement bytes are emitted as given, so they mustn't contain
    offset-bearing instructions. Returns ``(code, new_offset)`` where *new_offset* maps each old
    instruction offset to its new one, for remapping header tables.
    """
    new_pos = {}
    chunks = []  # (op, bytes); op None for replacements
    pos = 0
    i = 0
    while i < len(instructions):
        if i in replacements:
            end, raw = replacements[i]
            for j in range(i, end):
                new_pos[instructions[j].offset] = pos
            chunks.append((None, raw))
            pos += len(raw)
            i = end
            continue
        ins = instructions[i]
        start = base + ins.offset
        new_pos[ins.offset] = pos
        chunks.append((ins.op, data[start : start + ins.size]))
        pos += ins.size
        i += 1

    code = bytearray()
    for op, raw in chunks:
        if op in offset_ops:
            (target,) = struct.unpack_from('>I', raw, 1)
            code += raw[:1] + struct.pack('>I', new_pos[target]) + raw[5:]
        else:
            code += raw
    return bytes(code), new_pos


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


def pack_intermediate(magic, header, data):
    """An intermediate file: *magic*, a JSON *header* and the original script *data*."""
    raw = json.dumps(header).encode('utf-8')
    return magic + struct.pack('<I', len(raw)) + raw + data


def unpack_intermediate(magic, raw, what):
    if not raw.startswith(magic):
        raise ValueError(f'not {what} intermediate file')
    pos = len(magic)
    (n,) = struct.unpack_from('<I', raw, pos)
    pos += 4
    return json.loads(raw[pos : pos + n].decode('utf-8')), raw[pos + n :]
