import re
import textwrap

from .util import TextLine


class SilkyEngineMES:
    @staticmethod
    def get_paths(workpath):
        return workpath.glob('*.txt')

    @staticmethod
    def extract_lines(keybase, text):
        newlines = []
        trans = []
        group = ''
        key = None
        for i, line in enumerate(text.splitlines()):
            if line == '#1-STR_UNCRYPT' and not key:
                key = f'<<<TRANS:{keybase}-{i}>>>'
                newlines.append(line)
            elif key and line in {'#1-TO_NEW_STRING', '[0]', '#1-STR_UNCRYPT', '#3'}:
                continue
            elif key and line.startswith('["'):
                group += line[2:-2]
            elif key and line in {'#1-PUSH', '#1-RETURN'}:
                newlines.append(key)
                newlines.append(line)
                trans.append(TextLine(key, group, ''))
                key = None
                group = ''
            elif key:
                raise ValueError(f"Unknown line ({keybase}#{i}): {line!r}.")
            else:
                newlines.append(line)

        return newlines, trans

    @staticmethod
    def insert_lines(keybase, text, trans, width=65, wrap=None):
        newlines = []
        for line in text.splitlines():
            if m := re.match(rf'<<<TRANS:{keybase}-\d+>>>', line):
                if width:
                    lines = textwrap.wrap(trans[m.group(0)].text.strip(), width=width)
                    lines = ['["' + x + '"]' for x in lines]
                    text = '\n#1-TO_NEW_STRING\n[0]\n#1-STR_UNCRYPT\n'.join(lines)
                else:
                    text = '["' + trans[m.group(0)].text.strip() + '"]'
                newlines.append(text + trans[m.group(0)].eol)
            else:
                newlines.append(line)

        return newlines
