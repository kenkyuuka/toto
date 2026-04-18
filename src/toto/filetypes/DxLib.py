import logging
import re
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


class DxLib(TranslatableFile):
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
    def extract_lines(cls, input_file, ignore_patterns=()):
        # assume we're starting with the med unpacked, so this is getting text from the individual
        # files
        textlines = []
        intermediate_file = BytesIO()

        offset = cls.get_strings_offset(input_file)

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
        return intermediate_file, textlines, metadata

    @classmethod
    def insert_lines(cls, intermediate_file, translation_dict, cp932_fixup=None):
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
        output_file.seek(0)

        return output_file
