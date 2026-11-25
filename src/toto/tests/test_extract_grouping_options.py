"""Tests for the line-grouping options of extract (--group-start, --group-all-lines, ...)."""

import shutil
from pathlib import Path

import pytest
from click.testing import CliRunner

from toto.toto import cli

SAMPLE = Path(__file__).parent / 'filetypes' / 'KiriKiriScriptV2' / 'samples' / 'group_options.ks'

NARRATION = 'アリスは土手の上で姉のそばに座っていたが、何もすることがなくてとても退屈し始めていた。'


def run_extract(tmp_path, *options, filetype='kirikiri', source=None):
    if source is None:
        orig = tmp_path / 'orig'
        orig.mkdir()
        shutil.copy(SAMPLE, orig / 'group_options.ks')
        source = orig / 'group_options.ks'
    outpath = tmp_path / 'source'
    args = [
        'extract',
        f'--filetype={filetype}',
        '--codec=utf-8',
        '--workpath',
        str(tmp_path / 'working'),
        '--outpath',
        str(outpath),
        *options,
        str(source),
    ]
    result = CliRunner().invoke(cli, args)
    trans = outpath / 'group_options.ks.trans000.txt'
    lines = trans.read_text(encoding='utf-8').splitlines() if trans.exists() else []
    return result, lines


@pytest.mark.integration
class TestGroupingOptions:
    def test_default_splits_narration(self, tmp_path):
        result, lines = run_extract(tmp_path)
        assert result.exit_code == 0, result.output
        assert lines[0] == 'アリスは土手の上で姉のそばに座っていたが、'
        assert len(lines) == 6

    def test_group_all_lines(self, tmp_path):
        result, lines = run_extract(tmp_path, '--group-all-lines')
        assert result.exit_code == 0, result.output
        assert lines[0] == NARRATION
        assert len(lines) == 4

    def test_group_start(self, tmp_path):
        result, lines = run_extract(tmp_path, '--group-start', '「')
        assert result.exit_code == 0, result.output
        # Indented narration no longer starts a group.
        assert lines[-2:] == ['　ずっと、ずっと、ずっと落ちていった。', 'この落下は終わらないのかしら？']

    def test_group_start_repeated(self, tmp_path):
        result, lines = run_extract(tmp_path, '--group-start', '「', '--group-start', '　')
        assert result.exit_code == 0, result.output
        assert lines[-1] == '　ずっと、ずっと、ずっと落ちていった。この落下は終わらないのかしら？'

    def test_empty_group_start_rejected(self, tmp_path):
        result, _ = run_extract(tmp_path, '--group-start', '')
        assert result.exit_code != 0
        assert '--group-all-lines' in result.output

    def test_group_all_lines_with_group_start_rejected(self, tmp_path):
        result, _ = run_extract(tmp_path, '--group-all-lines', '--group-start', '「')
        assert result.exit_code != 0
        assert 'cannot be used together' in result.output

    def test_line_end_macros(self, tmp_path):
        result, lines = run_extract(tmp_path, '--line-end-macros', r'\[p\]')
        assert result.exit_code == 0, result.output
        # [r] is no longer a macro, so it stays in the text.
        assert lines[3] == '「ああ、大変だ！　大変だ！[r]遅刻してしまう！」'

    def test_empty_line_end_macros_disables_grouping(self, tmp_path):
        result, lines = run_extract(tmp_path, '--line-end-macros', '')
        assert result.exit_code == 0, result.output
        assert lines[3:5] == ['「ああ、大変だ！　大変だ！[r]', '　遅刻してしまう！」[p]']

    def test_invalid_line_end_macros(self, tmp_path):
        result, _ = run_extract(tmp_path, '--line-end-macros', r'(\[p\]')
        assert result.exit_code != 0
        assert 'Invalid regex' in result.output

    def test_continuation_indent_stripped_by_default(self, tmp_path):
        result, lines = run_extract(tmp_path)
        assert result.exit_code == 0, result.output
        assert lines[3] == '「ああ、大変だ！　大変だ！遅刻してしまう！」'

    def test_strip_continuation_indent(self, tmp_path):
        result, lines = run_extract(tmp_path, '--strip-continuation-indent')
        assert result.exit_code == 0, result.output
        assert lines[3] == '「ああ、大変だ！　大変だ！遅刻してしまう！」'

    def test_no_strip_continuation_indent(self, tmp_path):
        result, lines = run_extract(tmp_path, '--no-strip-continuation-indent')
        assert result.exit_code == 0, result.output
        assert lines[3] == '「ああ、大変だ！　大変だ！　遅刻してしまう！」'

    def test_unsupported_handler_warns(self, tmp_path):
        empty = tmp_path / 'empty'
        empty.mkdir()
        result, _ = run_extract(
            tmp_path, '--group-all-lines', '--strip-continuation-indent', filetype='agsd', source=empty
        )
        assert result.exit_code == 0, result.output
        assert 'agsd handler does not support --group-all-lines; ignored.' in result.output
        assert 'agsd handler does not support --strip-continuation-indent; ignored.' in result.output

    def test_no_strip_continuation_indent_unsupported_handler_warns(self, tmp_path):
        empty = tmp_path / 'empty'
        empty.mkdir()
        result, _ = run_extract(tmp_path, '--no-strip-continuation-indent', filetype='agsd', source=empty)
        assert result.exit_code == 0, result.output
        assert 'agsd handler does not support --no-strip-continuation-indent; ignored.' in result.output

    def test_no_warning_without_options(self, tmp_path):
        empty = tmp_path / 'empty'
        empty.mkdir()
        result, _ = run_extract(tmp_path, filetype='agsd', source=empty)
        assert result.exit_code == 0, result.output
        assert 'does not support' not in result.output
