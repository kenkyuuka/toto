"""Tests that an unchanged extract/insert round trip through the CLI reproduces the input."""

import pytest
from click.testing import CliRunner

from toto.toto import cli

SCRIPT_LINES = [
    '*start',
    'Alice followed the White Rabbit.',
    '[p]',
    'Down, down, down. Would the fall never come to an end?',
    '[p]',
]


def _roundtrip(tmp_path, data, extra_args=()):
    src = tmp_path / 'src'
    workpath = tmp_path / 'working'
    transdir = tmp_path / 'trans'
    outpath = tmp_path / 'output'
    for d in (src, workpath, transdir, outpath):
        d.mkdir()
    (src / 'rabbit.ks').write_bytes(data)

    runner = CliRunner()
    result = runner.invoke(
        cli,
        [
            'extract',
            str(src / 'rabbit.ks'),
            '--outpath',
            str(transdir),
            '--workpath',
            str(workpath),
            '--filetype',
            'kirikiri',
        ],
    )
    assert result.exit_code == 0, result.output
    assert list(transdir.glob('*.trans*.txt')), "Expected at least one trans file"

    result = runner.invoke(
        cli,
        [
            'insert',
            str(transdir),
            '--outpath',
            str(outpath),
            '--workpath',
            str(workpath),
            '--filetype',
            'kirikiri',
            *extra_args,
        ],
    )
    assert result.exit_code == 0, result.output
    return (outpath / 'rabbit.ks').read_bytes()


@pytest.mark.integration
@pytest.mark.parametrize('newline', ['\r\n', '\n'], ids=['crlf', 'lf'])
def test_unchanged_roundtrip_preserves_line_endings(tmp_path, newline):
    data = b'\xff\xfe' + newline.join([*SCRIPT_LINES, '']).encode('utf_16_le')
    assert _roundtrip(tmp_path, data) == data


@pytest.mark.integration
def test_unchanged_roundtrip_short_lines_with_width(tmp_path):
    """Lines that fit within --width are inserted without the trans file's line terminator."""
    data = '\r\n'.join([*SCRIPT_LINES, '']).encode('ascii')
    assert _roundtrip(tmp_path, data, ['--width', '200', '--codec', 'ascii']) == data
