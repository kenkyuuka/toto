"""Tests that an unchanged extract/insert round trip through the CLI reproduces the input."""

import pytest
from click.testing import CliRunner

from toto.tests.test_po_output import FIXTURES, _copy_txt, _extract, _insert, _outputs
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


@pytest.mark.integration
@pytest.mark.parametrize('name', FIXTURES)
def test_missing_translation_files_keep_source_text(tmp_path, name):
    """A script without translation files is written as if every line were left untranslated."""
    filetype, txt_base = _extract(tmp_path, name, 'txt')
    _copy_txt(txt_base)
    _insert(filetype, txt_base)

    (txt_base / 'target').rename(tmp_path / 'unused')
    (txt_base / 'target').mkdir()
    (txt_base / 'output').rename(tmp_path / 'expected')
    result = _insert(filetype, txt_base)

    assert _outputs(txt_base) == {
        p.relative_to(tmp_path / 'expected'): p.read_bytes()
        for p in sorted((tmp_path / 'expected').rglob('*'))
        if p.is_file()
    }
    assert 'no translations' in result.output


@pytest.mark.integration
def test_missing_later_batch_keeps_source_text(tmp_path):
    """Lines in a missing batch keep their source text; earlier batches are still applied."""
    lines = [f'Alice counted tart {i}.' for i in range(10002)]
    fixture = ('kirikiri', {'tarts.ks': '\n'.join([*lines, '']).encode('ascii')})
    filetype, base = _extract(tmp_path, 'tarts', 'txt', fixture)
    (base / 'target').mkdir()
    first = (base / 'source' / 'tarts.ks.trans000.txt').read_text(encoding='utf_8')
    (base / 'target' / 'tarts.ks.trans000.txt').write_text(first.replace('Alice', 'Alicia'), encoding='utf_8')
    _insert(filetype, base)
    out = (base / 'output' / 'tarts.ks').read_text(encoding='ascii').split('\n')
    assert out[0] == 'Alicia counted tart 0.'
    assert out[9999] == 'Alicia counted tart 9999.'
    assert out[10000:] == ['Alice counted tart 10000.', 'Alice counted tart 10001.', '']
