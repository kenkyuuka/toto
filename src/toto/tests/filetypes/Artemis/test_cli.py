"""CLI extract/insert tests for Artemis ``.txt`` scenario scripts."""

import pytest
from click.testing import CliRunner

from toto.toto import cli

from .test_artemis_txt import sample


def _run(runner, *args):
    result = runner.invoke(cli, [str(a) for a in args])
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_extract_translate_insert(tmp_path):
    """Nested scripts keep their layout, dialogue is wrapped at 50 columns, choices are not."""
    src, workpath, transdir, outpath = (tmp_path / d for d in ('src', 'working', 'trans', 'output'))
    (src / '00_com').mkdir(parents=True)
    (src / '00_com' / 's001a.txt').write_bytes(sample())

    common = ['--workpath', workpath, '--filetype', 'artemis']
    runner = CliRunner()
    _run(runner, 'extract', src, '--outpath', transdir, *common)

    trans_file = transdir / '00_com' / 's001a.txt.trans000.txt'
    source = trans_file.read_text(encoding='utf_8').splitlines()
    assert '「白ウサギを見なかった？時計を持ってたの」' in source
    english = {
        '「白ウサギを見なかった？時計を持ってたの」': (
            '"Have you seen the White Rabbit? He had a pocket watch, and he was terribly late."'
        ),
        '追いかける': 'Follow the rabbit',
    }
    trans_file.write_text('\n'.join(english.get(s, s) for s in source) + '\n', encoding='utf_8')

    _run(runner, 'insert', transdir, '--outpath', outpath, *common)

    out = (outpath / '00_com' / 's001a.txt').read_bytes().decode('cp932')
    assert (
        '[name アリス alc_0001]\r\n'
        '"Have you seen the White Rabbit? He had a pocket\r\n'
        'watch, and he was terribly late."\r\n\r\n'
    ) in out
    assert '[sel_text label="分岐A" text="Follow the rabbit" exp=""]' in out
    assert '[sel_text label="分岐B" text="昼寝する" exp=""]' in out


@pytest.mark.integration
def test_po_output_has_speakers(tmp_path):
    src = tmp_path / 'src'
    src.mkdir()
    (src / 's001a.txt').write_bytes(sample())
    _run(
        CliRunner(),
        'extract',
        src,
        '--outpath',
        tmp_path / 'po',
        '--workpath',
        tmp_path / 'working',
        '--filetype',
        'artemis',
        '--output-format',
        'po',
    )
    po = (tmp_path / 'po' / 's001a.txt.po').read_text(encoding='utf_8')
    assert '#. Speaker: アリス\n' in po
    assert '#. Speaker: 白ウサギ\n' in po
