#!/usr/bin/env python3
"""Disassembler for mgos (.o) bytecode files.

Usage:
    python tools/mgos_disasm.py <file.o>                    # full disassembly
    python tools/mgos_disasm.py <file.o> 0x4438             # start at address
    python tools/mgos_disasm.py <file.o> 0x4438 0x4600      # address range
    python tools/mgos_disasm.py <file.o> --strings          # dump string table only
    python tools/mgos_disasm.py <file.o> --grep '（'         # find strings containing pattern
    python tools/mgos_disasm.py <file.o> --xref 0x1234      # find refs to a string offset
    python tools/mgos_disasm.py <file.o> --calls 0x3d33     # find all call sites for addr
"""

import argparse
import struct
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from toto.filetypes.Mgos import Mgos, _INSN_SIZE, _STRING_REF_OPCODES

# ── Operator mnemonics ────────────────────────────────────────────────
# Named to read naturally when scanning disassembly output.
_OP_MNEMONICS = {
    # Comparisons
    0x80: 'eq',  # ==
    0x81: 'ne',  # !=
    0x83: 'le',  # <=
    0x84: 'ge',  # >=
    0x8E: 'lt',  # <
    0x8F: 'gt',  # >
    # Shift / logical
    0x85: 'shl',  # <<
    0x86: 'shr',  # >>
    0x87: 'and',  # &&
    0x88: 'or',  # ||
    # Arithmetic (also string concat for 0x89)
    0x89: 'add',  # +  (or string concat)
    0x8A: 'sub',  # -
    0x8B: 'mul',  # *
    0x8C: 'div',  # \ (integer)
    0x8D: 'mod',  # %
    # Bitwise
    0x90: 'band',  # &
    0x91: 'bor',  # |
    0x92: 'bxor',  # ^
    # Statement / control
    0x93: 'nop',
    0x94: 'nop2',
    0x95: 'end',  # statement terminator
    0x96: 'nop3',
    # Unary / compound assignment
    0x97: 'mov',  # assignment: pop value, pop target varref, store
    0x98: 'neg',  # unary negation
    0x99: 'adda',  # add-assign (+=)
    0x9A: 'suba',  # -=
    0x9B: 'inc',  # ++
    0x9C: 'dec',  # --
    # Shortcuts
    0xC0: 'decl',  # declare variable
    0xC1: 'jf',  # jump if false  (pops addr + cond)
    0xC2: 'jmp',  # unconditional jump (pops addr)
    0xC3: 'ret',  # return
}

# Known syscall names (partial; extend as RE progresses)
_SYSCALL_NAMES = {
    0x00: 'goto',
    0x01: 'goto',
    0x02: 'ifgoto_f',
    0x03: 'ifgoto_t',
    0x11: 'return',
    0x12: 'halt',
    0x13: 'load_script',
    0x14: 'spawn',
    0x15: 'thread_id',
    0x16: 'line',
    0x17: 'funcname',
    0x18: 'send_cmd',
    0x19: 'assign_arg',
    0x1A: 'unwind',
    0x1B: 'ret_addr',
    0x1C: 'argc',
    0x1D: 'typeof',
    0x1E: 'assign',
    0x1F: 'clear_ref',
    0x20: 'new_str',
    0x21: 'dim',
    0x22: 'declare',
    0x23: 'var_query',
    0x24: 'debug_name',
    0x25: 'global_mgmt',
    0x97: 'strlen',
    0x98: 'strbef',
    0x99: 'straft',
    0xA4: 'strfind',
    0x100: 'wait?',
    0x102: 'timer?',
    0x207: 'error_dialog',
    0x40A: 'exec',
}


def build_str_lookup(data, string_table_start):
    """Parse string table, return {offset: decoded_text}."""
    entries = Mgos._parse_string_table(data, string_table_start)
    lookup = {}
    for offset, raw in entries:
        try:
            text = raw.decode('cp932')
        except (UnicodeDecodeError, ValueError):
            text = f'<raw:{raw[:20].hex()}>'
        lookup[offset] = text
    return lookup


def format_str(text, max_len=60):
    """Shorten a string for inline display."""
    r = repr(text)
    if len(r) > max_len:
        return r[: max_len - 3] + '...'
    return r


def disassemble(data, bc_start, bc_end, str_lookup, out=sys.stdout):
    """Disassemble bytecode range, printing one instruction per line."""
    pos = bc_start
    while pos < bc_end:
        byte = data[pos]
        size = _INSN_SIZE.get(byte)
        if size is None:
            out.write(f'{pos:06x}:  .byte {byte:02x}\n')
            pos += 1
            continue
        if pos + size > len(data):
            out.write(f'{pos:06x}:  .byte {byte:02x}  ; truncated\n')
            break

        raw_hex = data[pos : pos + size].hex()
        lo = byte & 0x0F
        hi = byte >> 4

        # Single-byte instructions
        if size == 1:
            if byte in _OP_MNEMONICS:
                out.write(f'{pos:06x}:  {raw_hex:<12s} {_OP_MNEMONICS[byte]}\n')
            elif 0xD0 <= byte <= 0xD4:
                out.write(f'{pos:06x}:  {raw_hex:<12s} lit.i    {byte - 0xD0}\n')
            else:
                out.write(f'{pos:06x}:  {raw_hex:<12s} ??? {byte:#04x}\n')
            pos += size
            continue

        # Multi-byte: read operand
        if hi == 0:
            operand = struct.unpack_from('<I', data, pos + 1)[0]
            signed_operand = struct.unpack_from('<i', data, pos + 1)[0]
        elif hi == 1:
            operand = struct.unpack_from('<H', data, pos + 1)[0]
            signed_operand = operand
        else:  # hi == 2
            operand = data[pos + 1]
            signed_operand = operand

        if lo == 0:  # push int
            if byte == 0x01:  # float
                fval = struct.unpack_from('<f', data, pos + 1)[0]
                out.write(f'{pos:06x}:  {raw_hex:<12s} lit.f    {fval}\n')
            else:
                out.write(f'{pos:06x}:  {raw_hex:<12s} lit.i    {signed_operand}\n')
        elif lo == 1:  # push float (0x01 only, handled above for dword)
            fval = struct.unpack_from('<f', data, pos + 1)[0]
            out.write(f'{pos:06x}:  {raw_hex:<12s} lit.f    {fval}\n')
        elif lo == 2:  # string ref
            text = str_lookup.get(operand, f'???@{operand:#x}')
            out.write(f'{pos:06x}:  {raw_hex:<12s} lit.s    {format_str(text)}\n')
        elif lo == 3:  # code addr
            out.write(f'{pos:06x}:  {raw_hex:<12s} lit.a    {operand:#06x}\n')
        elif lo == 5:  # syscall
            name = _SYSCALL_NAMES.get(operand, '')
            label = f'  ; {name}' if name else ''
            out.write(f'{pos:06x}:  {raw_hex:<12s} sys      {operand:#04x}{label}\n')
        elif lo == 6:  # call
            out.write(f'{pos:06x}:  {raw_hex:<12s} call     {operand:#06x}\n')
        elif lo == 7:  # varref
            out.write(f'{pos:06x}:  {raw_hex:<12s} var      v{operand}\n')
        else:
            out.write(f'{pos:06x}:  {raw_hex:<12s} ???      lo={lo:#x}\n')

        pos += size


def find_xrefs(data, bc_end, target_offset, str_lookup):
    """Find all bytecode instructions that reference a given string offset."""
    pos = 0
    results = []
    while pos < bc_end:
        byte = data[pos]
        size = _INSN_SIZE.get(byte)
        if size is None:
            break
        if byte in _STRING_REF_OPCODES:
            fmt, op_size = _STRING_REF_OPCODES[byte]
            if op_size == 1:
                offset = data[pos + 1]
            else:
                offset = struct.unpack_from(fmt, data, pos + 1)[0]
            if offset == target_offset:
                results.append(pos)
        pos += size
    return results


def find_calls(data, bc_end, target_addr):
    """Find all call/jmp instructions targeting a given address."""
    pos = 0
    results = []
    while pos < bc_end:
        byte = data[pos]
        size = _INSN_SIZE.get(byte)
        if size is None:
            break
        lo = byte & 0x0F
        hi = byte >> 4
        if lo == 6:  # call
            if hi == 0:
                operand = struct.unpack_from('<I', data, pos + 1)[0]
            elif hi == 1:
                operand = struct.unpack_from('<H', data, pos + 1)[0]
            else:
                operand = data[pos + 1]
            if operand == target_addr:
                results.append(pos)
        pos += size
    return results


def main():
    parser = argparse.ArgumentParser(description='mgos bytecode disassembler')
    parser.add_argument('file', help='.o bytecode file')
    parser.add_argument('start', nargs='?', help='start address (hex)')
    parser.add_argument('stop', nargs='?', help='stop address (hex)')
    parser.add_argument('--strings', action='store_true', help='dump string table')
    parser.add_argument('--grep', help='search strings for pattern')
    parser.add_argument('--xref', help='find bytecode refs to string at offset (hex)')
    parser.add_argument('--calls', help='find all call sites for address (hex)')
    args = parser.parse_args()

    data = open(args.file, 'rb').read()
    bc_end, refs = Mgos._walk_bytecode(data)
    str_lookup = build_str_lookup(data, bc_end)

    if args.strings:
        for offset in sorted(str_lookup):
            text = str_lookup[offset]
            print(f'{offset:06x}: {format_str(text, 120)}')
        return

    if args.grep:
        pattern = args.grep
        for offset in sorted(str_lookup):
            text = str_lookup[offset]
            if pattern in text:
                print(f'{offset:06x}: {format_str(text, 120)}')
        return

    if args.xref:
        target = int(args.xref, 16)
        text = str_lookup.get(target, '???')
        print(f'Cross-references to string @{target:#06x} = {format_str(text)}:')
        for addr in find_xrefs(data, bc_end, target, str_lookup):
            print(f'  {addr:06x}')
        return

    if args.calls:
        target = int(args.calls, 16)
        print(f'Call sites for {target:#06x}:')
        for addr in find_calls(data, bc_end, target):
            print(f'  {addr:06x}')
        return

    # Disassemble
    start = int(args.start, 16) if args.start else 0
    stop = int(args.stop, 16) if args.stop else bc_end

    print(f'; file: {args.file}')
    print(f'; bytecode: 0x000000 - {bc_end:#08x}  ({bc_end} bytes)')
    print(f'; strings:  {bc_end:#08x} - {len(data):#08x}  ({len(data) - bc_end} bytes, {len(str_lookup)} entries)')
    print(f'; range:    {start:#08x} - {stop:#08x}')
    print()
    disassemble(data, start, stop, str_lookup)


if __name__ == '__main__':
    main()
