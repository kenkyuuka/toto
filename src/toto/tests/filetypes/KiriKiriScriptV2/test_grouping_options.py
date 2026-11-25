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

# The two quotes as extracted, with and without their continuation lines' indent.
RABBIT_STRIPPED = '「ああ、大変だ！　大変だ！遅刻してしまう！」'
RABBIT_INDENTED = '「ああ、大変だ！　大変だ！　遅刻してしまう！」'
DINAH_STRIPPED = '「ねえ、ダイナ、コウモリは猫を食べるかしら？猫はコウモリを食べるかしら？」'
DINAH_INDENTED = ''.join(DINAH)


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

    def test_quote_groups(self):
        _, textlines, _ = extract()
        assert textlines[3].text == RABBIT_STRIPPED
        assert textlines[3].eol == '[p]'
        assert textlines[4].text == DINAH_STRIPPED

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
            RABBIT_STRIPPED,
            DINAH_STRIPPED,
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
        assert textlines[3].text == RABBIT_STRIPPED


@pytest.mark.unit
class TestStripContinuationIndent:
    """Continuation indents are stripped by default."""

    def test_on_by_default(self):
        _, with_default, _ = extract()
        _, stripped, _ = extract(strip_continuation_indent=True)
        assert with_default == stripped

    def test_r_continuation(self):
        _, textlines, _ = extract()
        assert textlines[3].text == RABBIT_STRIPPED

    def test_group_start_and_closing_lines(self):
        _, textlines, _ = extract()
        assert textlines[4].text == DINAH_STRIPPED

    def test_first_line_indent_kept(self):
        _, textlines, _ = extract()
        assert textlines[5].text == ''.join(FALLING)

    def test_plain_continuation(self):
        # With only 「 as a group start, the indented middle line is a plain continuation.
        data = '「チェシャ猫さん、[r]\r\n　どの道を行けばいいの？\r\n　　教えてちょうだい」[p]\r\n'.encode()
        _, textlines, _ = KiriKiriScript.extract_lines(BytesIO(data), codec='utf-8', group_starts=('「',))
        assert [tl.text for tl in textlines] == ['「チェシャ猫さん、どの道を行けばいいの？教えてちょうだい」']

    def test_single_line_unaffected(self):
        data = '　「時間がない！」[p]\r\n'.encode()
        _, textlines, _ = KiriKiriScript.extract_lines(BytesIO(data), codec='utf-8')
        assert [tl.text for tl in textlines] == ['　「時間がない！」']


@pytest.mark.unit
class TestKeepContinuationIndent:
    def test_quote_groups_keep_indent(self):
        _, textlines, _ = extract(strip_continuation_indent=False)
        assert textlines[3].text == RABBIT_INDENTED
        assert textlines[4].text == DINAH_INDENTED

    def test_identity_insert(self):
        lines = identity_insert(strip_continuation_indent=False).split('\r\n')
        assert lines[7] == RABBIT_INDENTED + '[p]'
