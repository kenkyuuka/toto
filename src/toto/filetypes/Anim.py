from __future__ import annotations

import logging
import re
import unicodedata
from io import BytesIO

from ..util import TextLine, apply_cp932_fixup, build_file_cp932_fixup
from .TranslatableFile import TranslatableFile

logger = logging.getLogger(__name__)


def byte_add(*args):
    return sum(args) & 0xFF


# Matches strings containing 2+ consecutive hiragana, katakana, or kanji
_JP_TEXT_RE = re.compile(r'[\u3040-\u309F\u30A0-\u30FF\u4E00-\u9FFF]{2,}')


class Anim(TranslatableFile):
    @staticmethod
    def get_paths(workpath):
        return list(workpath.glob('*_define.dat')) + list(workpath.glob('*_sce.dat'))

    @classmethod
    def extract_lines(cls, input_file, ignore_patterns=()):
        """Extract translatable lines from input_file.

        Return a tuple (output_file, textlines). output_file is a bytestream that can be written out as
        an intermediate file (to be passed back to insert_lines later). textlines is a sequence of
        TextLines to be translated.
        """
        textlines = []
        intermediate_file = BytesIO()

        line_index = 0
        raw_translated_chunks = []

        raw = input_file.read()
        encryption_key = bytes(raw[4:20])
        data = cls.decrypt(raw)
        if input_file.name.lower().endswith('_sce.dat'):
            offset = int.from_bytes(data[4:8], byteorder='little')
            intermediate_file.write(data[:offset])
            buff = bytearray()
            for i in data[offset:]:
                if i == 0:
                    # we have reached the end of a string
                    if buff:
                        debuff = bytes(buff).decode('cp932', errors='replace')
                        if (
                            debuff
                            and not ('a' <= debuff[0] <= 'z')
                            and not ('A' <= debuff[0] <= 'Z')
                            and not cls._should_ignore(debuff, ignore_patterns)
                        ):
                            key = f'<<<TRANS:{line_index}>>>'
                            textlines.append(TextLine(key, debuff, b'\0'))
                            intermediate_file.write(key.encode('cp932'))
                            raw_translated_chunks.append(bytes(buff))
                            line_index += 1
                        else:
                            intermediate_file.write(bytes(buff) + b'\0')
                        buff = bytearray()
                    else:
                        intermediate_file.write(b'\0')
                else:
                    buff.append(i)
            if buff:
                debuff = bytes(buff).decode('cp932', errors='replace')
                if (
                    debuff
                    and not ('a' <= debuff[0] <= 'z')
                    and not ('A' <= debuff[0] <= 'Z')
                    and not cls._should_ignore(debuff, ignore_patterns)
                ):
                    key = f'<<<TRANS:{line_index}>>>'
                    textlines.append(TextLine(key, debuff, b'\0'))
                    intermediate_file.write(key.encode('cp932'))
                    raw_translated_chunks.append(bytes(buff))
                    line_index += 1
                else:
                    intermediate_file.write(bytes(buff) + b'\0')
        if input_file.name.lower().endswith('_define.dat'):
            i = 0
            non_trans = bytearray()
            while i < len(data):
                if data[i] == 0:
                    non_trans.append(0)
                    i += 1
                    continue
                # find the end of this null-terminated string
                end = i
                while end < len(data) and data[end]:
                    end += 1
                try:
                    text = bytes(data[i:end]).decode('cp932', errors='strict')
                except UnicodeDecodeError:
                    text = None
                has_control = text and any(c != '\t' and unicodedata.category(c) == 'Cc' for c in text)
                if (
                    text
                    and not has_control
                    and _JP_TEXT_RE.search(text)
                    and not cls._should_ignore(text, ignore_patterns)
                ):
                    if non_trans:
                        intermediate_file.write(bytes(non_trans))
                        non_trans = bytearray()
                    key = f'<<<TRANS:{line_index}>>>'
                    textlines.append(TextLine(key, text, b'\0'))
                    intermediate_file.write(key.encode('cp932'))
                    raw_translated_chunks.append(bytes(data[i:end]))
                    line_index += 1
                    # skip past the null terminator; eol restores it on insert
                    if end < len(data) and data[end] == 0:
                        end += 1
                else:
                    non_trans.extend(data[i:end])
                i = end
            if non_trans:
                intermediate_file.write(bytes(non_trans))

        intermediate_file.seek(0)

        cp932_fixup = build_file_cp932_fixup(raw_translated_chunks)
        metadata = {'encryption_key': encryption_key}
        if cp932_fixup:
            metadata['cp932_fixup'] = cp932_fixup
        return (intermediate_file, textlines, metadata)

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, encryption_key=None, cp932_fixup=None):
        def get_trans(m):
            t = translation_dict[m.group(0).decode('cp932')]
            encoded = t.text.strip('\n\r').encode('cp932')
            if cp932_fixup:
                encoded = apply_cp932_fixup(encoded, cp932_fixup)
            return encoded + t.eol

        output_file = BytesIO()

        data = intermediate_file.read()
        new_data = re.sub(rb'<<<TRANS:\d+>>>', get_trans, data)
        output_file.write(cls.encrypt(new_data, key=encryption_key))

        output_file.seek(0)
        return output_file

    @classmethod
    def decrypt(cls, data: bytes):
        key = bytearray(data[4:20])
        data = bytearray(data[20:])
        length = len(data)
        v = 0
        for i in range(length):
            data[i] = key[v] ^ data[i]
            v += 1
            if v == 16:
                v = 0
                key = cls.switch_key(key, data[i - 1])
        return data

    @classmethod
    def encrypt(cls, data: bytes, key: bytes | None = None):
        length = len(data)
        enc_key: bytearray = bytearray(key) if key is not None else bytearray(b'\x00' * 16)
        new_data = b'\x00\x00\x00\x01' + bytes(enc_key) + b'\x00' * length
        new_data = bytearray(new_data)

        v = 0
        for i in range(length):
            new_data[20 + i] = enc_key[v] ^ data[i]
            v += 1
            if v == 16:
                v = 0
                enc_key = cls.switch_key(enc_key, data[i - 1])

        return new_data

    @staticmethod
    def switch_key(key: bytearray, ch: int):
        t = ch
        ch &= 7
        if ch == 0:
            key[0] = byte_add(key[0], t)
            key[3] = byte_add(key[3], t, 2)
            key[4] = byte_add(key[2], t, 11)
            key[8] = byte_add(key[6], 7)
        elif ch == 1:
            key[2] = byte_add(key[9], key[10])
            key[6] = byte_add(key[7], key[15])
            key[8] = byte_add(key[8], key[1])
            key[15] = byte_add(key[5], key[3])
        elif ch == 2:
            key[1] = byte_add(key[1], key[2])
            key[5] = byte_add(key[5], key[6])
            key[7] = byte_add(key[7], key[8])
            key[10] = byte_add(key[10], key[11])
        elif ch == 3:
            key[9] = byte_add(key[2], key[1])
            key[11] = byte_add(key[6], key[5])
            key[12] = byte_add(key[8], key[7])
            key[13] = byte_add(key[11], key[10])
        elif ch == 4:
            key[0] = byte_add(key[1], 111)
            key[3] = byte_add(key[4], 71)
            key[4] = byte_add(key[5], 17)
            key[14] = byte_add(key[15], 64)
        elif ch == 5:
            key[2] = byte_add(key[2], key[10])
            key[4] = byte_add(key[5], key[12])
            key[6] = byte_add(key[8], key[14])
            key[8] = byte_add(key[11], key[0])
        elif ch == 6:
            key[9] = byte_add(key[11], key[1])
            key[11] = byte_add(key[13], key[3])
            key[13] = byte_add(key[15], key[5])
            key[15] = byte_add(key[9], key[7])
            key[1] = byte_add(key[9], key[5])
            key[2] = byte_add(key[10], key[6])
            key[3] = byte_add(key[11], key[7])
            key[4] = byte_add(key[12], key[8])
        elif ch == 7:
            key[1] = byte_add(key[9], key[5])
            key[2] = byte_add(key[10], key[6])
            key[3] = byte_add(key[11], key[7])
            key[4] = byte_add(key[12], key[8])
        return key
