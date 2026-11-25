"""Tests for the group_starts and strip_continuation_indent options of KiriKiriScript.extract_lines."""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.KiriKiriScriptV2 import KiriKiriScript

TEST_DATA = Path(__file__).parent / "samples"

NARRATION = ['アリスは土手の上で姉のそばに座っていたが、', '何もすることがなくて', 'とても退屈し始めていた。']
RABBIT = '「ああ、大変だ！　大変だ！[r]', '　遅刻してしまう！」[p]'
DINAH = '「ねえ、ダイナ、', '　コウモリは猫を食べるかしら？', '　猫はコウモリを食べるかしら？」'
FALLING = '　ずっと、ずっと、ずっと落ちていった。', 'この落下は終わらないのかしら？'


def extract(**kwargs):
    data = (TEST_DATA / 'group_options.ks').read_bytes()
    return KiriKiriScript.extract_lines(BytesIO(data), codec='utf-8', **kwargs)


def identity_insert(**kwargs):
    inter, textlines, meta = extract(**kwargs)
    trans = {tl.key: tl for tl in textlines}
    return KiriKiriScript.insert_lines(inter, trans, codec=meta['codec']).read().decode('utf-8')


@pytest.mark.unit
class TestDefaultGrouping:
    def test_unprefixed_narration_lines_are_separate(self):
        _, textlines, _ = extract()
        assert [tl.text for tl in textlines[:3]] == NARRATION

    def test_quote_group_keeps_indent(self):
        _, textlines, _ = extract()
        assert textlines[3].text == '「ああ、大変だ！　大変だ！　遅刻してしまう！」'
        assert textlines[3].eol == '[p]'
        assert textlines[4].text == ''.join(DINAH)

    def test_indented_narration_starts_group(self):
        _, textlines, _ = extract()
        assert [tl.text for tl in textlines[5:]] == [''.join(FALLING)]


@pytest.mark.unit
class TestGroupAllLines:
    def test_narration_joined(self):
        _, textlines, _ = extract(group_starts=('',))
        assert textlines[0].text == ''.join(NARRATION)
        assert textlines[0].eol == ''

    def test_group_closed_by_tag_line(self):
        _, textlines, _ = extract(group_starts=('',))
        assert [tl.text for tl in textlines] == [
            ''.join(NARRATION),
            '「ああ、大変だ！　大変だ！　遅刻してしまう！」',
            ''.join(DINAH),
            ''.join(FALLING),
        ]

    def test_identity_insert(self):
        lines = identity_insert(group_starts=('',)).split('\r\n')
        assert lines[2] == ''.join(NARRATION)
        assert lines[3] == '[tp]'
        assert lines[-3] == ''.join(FALLING)
        assert lines[-2] == '[hitret ]'


@pytest.mark.unit
class TestCustomGroupStarts:
    def test_only_given_prefixes_start_groups(self):
        _, textlines, _ = extract(group_starts=('「',))
        # Indented narration no longer starts a group.
        assert [tl.text for tl in textlines[-2:]] == list(FALLING)
        assert textlines[3].text == '「ああ、大変だ！　大変だ！　遅刻してしまう！」'


@pytest.mark.unit
class TestStripContinuationIndent:
    def test_r_continuation(self):
        _, textlines, _ = extract(strip_continuation_indent=True)
        assert textlines[3].text == '「ああ、大変だ！　大変だ！遅刻してしまう！」'

    def test_group_start_and_closing_lines(self):
        _, textlines, _ = extract(strip_continuation_indent=True)
        assert textlines[4].text == '「ねえ、ダイナ、コウモリは猫を食べるかしら？猫はコウモリを食べるかしら？」'

    def test_first_line_indent_kept(self):
        _, textlines, _ = extract(strip_continuation_indent=True)
        assert textlines[5].text == ''.join(FALLING)

    def test_plain_continuation(self):
        # With only 「 as a group start, the indented middle line is a plain continuation.
        data = '「チェシャ猫さん、[r]\r\n　どの道を行けばいいの？\r\n　　教えてちょうだい」[p]\r\n'.encode()
        _, textlines, _ = KiriKiriScript.extract_lines(
            BytesIO(data), codec='utf-8', group_starts=('「',), strip_continuation_indent=True
        )
        assert [tl.text for tl in textlines] == ['「チェシャ猫さん、どの道を行けばいいの？教えてちょうだい」']

    def test_off_by_default(self):
        _, with_default, _ = extract()
        _, without, _ = extract(strip_continuation_indent=False)
        assert with_default == without
