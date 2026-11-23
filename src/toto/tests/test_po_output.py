"""CLI tests for ``extract --output-format po`` and reading PO translations in ``insert``."""

import io
import pathlib
import shelve
from collections import namedtuple

import pytest
from click.testing import CliRunner

import toto.util
from toto.po import escape, read_po
from toto.tests.filetypes.AdvHdWs2.test_advhd_ws2 import _make_multi_speaker_ws2
from toto.tests.filetypes.AGSD.test_agsd_spt import make_dialogue_spt
from toto.tests.filetypes.AI6WIN.test_ai6win_mes import sample as ai6win_sample
from toto.tests.filetypes.Anim.test_anim import make_define_with_brackets
from toto.tests.filetypes.BlackRainbow.test_blackrainbow_spt import sample as blackrainbow_sample
from toto.tests.filetypes.DxLib.test_unwrap import dialogue_script
from toto.tests.filetypes.Mgos.test_mgos import make_mgos_direction_script
from toto.tests.filetypes.SystemNNN.test_systemnnn_spt import sample as systemnnn_sample
from toto.tests.filetypes.SystemNNN.test_systemnnn_xtx import NAMECOL
from toto.toto import cli

KIRIKIRI_SAMPLES = pathlib.Path(__file__).parent / 'filetypes' / 'KiriKiriScriptV2' / 'samples'

# filetype, {relative path: contents}. Nested paths check that the directory layout carries over.
FIXTURES = {
    'kirikiri': ('kirikiri', {'realistic_japanese.ks': (KIRIKIRI_SAMPLES / 'realistic_japanese.ks').read_bytes()}),
    'kirikiri-escapes': (
        'kirikiri',
        {'escapes.ks': b'Alice said "Off with\\her head!"\t(quietly)\n[p]\n\n'},
    ),
    'dxlib': ('dxlib', {'STORY01': dialogue_script()[0]}),
    'anim': ('anim', {'alice_define.dat': make_define_with_brackets()}),
    'mgos': ('mgos', {'mgs/alice.o': make_mgos_direction_script()}),
    'agsd': ('agsd', {'scene/alice_01.spt': make_dialogue_spt()}),
    'advhd': ('advhd', {'rabbit.ws2': _make_multi_speaker_ws2()}),
    'blackrainbow': ('blackrainbow', {'alice.spt': blackrainbow_sample()}),
    'systemnnn': ('systemnnn', {'spt/alice.spt': systemnnn_sample()}),
    'systemnnn-xtx': ('systemnnn-xtx', {'spt/namecol.xtx': NAMECOL}),
    'ai6win': ('ai6win', {'stage/stage00.mes': ai6win_sample()}),
}


def _run(runner, *args, ok=True):
    result = runner.invoke(cli, [str(a) for a in args])
    if ok:
        assert result.exit_code == 0, result.output
    return result


def _write_sources(src, files):
    for rel, data in files.items():
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        (src / rel).write_bytes(data)


def _extract(tmp_path, name, output_format):
    """Extract fixture *name* in *output_format*; returns (filetype, base directory)."""
    filetype, files = FIXTURES[name]
    base = tmp_path / output_format
    _write_sources(base / 'src', files)
    _run(
        CliRunner(),
        'extract',
        base / 'src',
        '--outpath',
        base / 'source',
        '--workpath',
        base / 'working',
        '--filetype',
        filetype,
        '--output-format',
        output_format,
    )
    return filetype, base


def _insert(filetype, base, *extra, ok=True):
    return _run(
        CliRunner(),
        'insert',
        base / 'target',
        '--outpath',
        base / 'output',
        '--workpath',
        base / 'working',
        '--filetype',
        filetype,
        *extra,
        ok=ok,
    )


def _po_text(entries, header=True):
    """A target PO file for *entries*, given as (msgctxt, msgid, msgstr) tuples."""
    parts = ['msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n'] if header else []
    for ctx, msgid, msgstr in entries:
        parts.append(f'msgctxt "{escape(ctx)}"\nmsgid "{escape(msgid)}"\nmsgstr "{escape(msgstr)}"\n')
    return '\n'.join(parts)


def _read_po_file(path):
    with open(path, encoding='utf_8') as f:
        return read_po(f)


def _translate_po(base, translate):
    """Write a target PO for every source PO; *translate* maps an entry to its msgstr."""
    for po in (base / 'source').rglob('*.po'):
        target = base / 'target' / po.relative_to(base / 'source')
        target.parent.mkdir(parents=True, exist_ok=True)
        entries = [(e.msgctxt, e.msgid, translate(e)) for e in _read_po_file(po)]
        target.write_text(_po_text(entries), encoding='utf_8')


def _copy_txt(base):
    for txt in (base / 'source').rglob('*.trans*.txt'):
        target = base / 'target' / txt.relative_to(base / 'source')
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(txt.read_bytes())


def _outputs(base):
    out = base / 'output'
    return {p.relative_to(out): p.read_bytes() for p in sorted(out.rglob('*')) if p.is_file()}


def _txt_lines(base):
    """The txt output's lines per script, in batch order."""
    lines = {}
    for txt in sorted((base / 'source').rglob('*.trans*.txt')):
        script = txt.relative_to(base / 'source').as_posix().rsplit('.trans', 1)[0]
        lines.setdefault(script, []).extend(txt.read_text(encoding='utf_8').split('\n')[:-1])
    return lines


@pytest.mark.integration
@pytest.mark.parametrize('name', FIXTURES)
@pytest.mark.parametrize('mode', ['copy', 'empty'])
def test_po_insert_matches_txt(tmp_path, name, mode):
    """An untranslated PO (msgstr copied or left empty) inserts exactly like an untranslated txt."""
    filetype, txt_base = _extract(tmp_path, name, 'txt')
    _copy_txt(txt_base)
    _insert(filetype, txt_base)

    _, po_base = _extract(tmp_path, name, 'po')
    assert not list((po_base / 'source').rglob('*.trans*.txt'))
    assert list((po_base / 'source').rglob('*.po'))
    _translate_po(po_base, (lambda e: e.msgid) if mode == 'copy' else (lambda _e: ''))
    _insert(filetype, po_base)

    assert _outputs(po_base) == _outputs(txt_base)


@pytest.mark.integration
@pytest.mark.parametrize('name', FIXTURES)
def test_msgids_match_txt_lines(tmp_path, name):
    """The PO source text is character for character the txt line, so translation memories carry over."""
    _, txt_base = _extract(tmp_path, name, 'txt')
    _, po_base = _extract(tmp_path, name, 'po')
    expected = {script: [line for line in lines if line] for script, lines in _txt_lines(txt_base).items()}
    actual = {}
    for po in sorted((po_base / 'source').rglob('*.po')):
        script = po.relative_to(po_base / 'source').as_posix().removesuffix('.po')
        actual[script] = [e.msgid for e in _read_po_file(po)]
    assert actual == expected


@pytest.mark.integration
def test_po_translation_is_inserted(tmp_path):
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    (po,) = (base / 'source').glob('*.po')
    (entry,) = _read_po_file(po)
    assert entry.msgid == 'Alice said "Off with\\her head!"\t(quietly)'
    (base / 'target').mkdir()
    (base / 'target' / 'escapes.ks.po').write_text(
        _po_text([(entry.msgctxt, entry.msgid, 'Alice dit << \\ >>\t(tout bas)')]), encoding='utf_8'
    )
    _insert(filetype, base)
    assert (base / 'output' / 'escapes.ks').read_bytes() == b'Alice dit << \\ >>\t(tout bas)\n[p]\n\n'


@pytest.mark.integration
def test_po_entries_matched_by_key_not_position(tmp_path):
    filetype, base = _extract(tmp_path, 'dxlib', 'po')
    (po,) = (base / 'source').glob('*.po')
    entries = _read_po_file(po)
    (base / 'target').mkdir()
    reordered = [(e.msgctxt, e.msgid, e.msgid) for e in reversed(entries)]
    (base / 'target' / 'STORY01.po').write_text(_po_text(reordered), encoding='utf_8')
    _insert(filetype, base)
    assert (base / 'output' / 'STORY01').read_bytes() == FIXTURES['dxlib'][1]['STORY01']


@pytest.mark.integration
def test_no_po_for_script_without_text(tmp_path):
    base = tmp_path
    _write_sources(base / 'src', {'no_text.ks': (KIRIKIRI_SAMPLES / 'no_text.ks').read_bytes()})
    _run(
        CliRunner(),
        'extract',
        base / 'src',
        '--outpath',
        base / 'source',
        '--workpath',
        base / 'working',
        '--filetype',
        'kirikiri',
        '--output-format',
        'po',
    )
    assert not list((base / 'source').rglob('*.po'))


@pytest.mark.integration
def test_po_file_layout(tmp_path):
    _, base = _extract(tmp_path, 'mgos', 'po')
    po = base / 'source' / 'mgs' / 'alice.o.po'
    raw = po.read_bytes()
    assert raw.startswith(b'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n')
    assert b'\r' not in raw
    raw.decode('utf_8')
    assert (base / 'working' / 'mgs' / 'alice.o').exists()


@pytest.mark.integration
def test_insert_rejects_po_and_txt_together(tmp_path):
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    _translate_po(base, lambda e: e.msgid)
    (base / 'target' / 'escapes.ks.trans000.txt').write_text('stale\n', encoding='utf_8')
    result = _insert(filetype, base, ok=False)
    assert result.exit_code != 0
    assert 'escapes.ks.po' in result.output and 'escapes.ks.trans000.txt' in result.output


@pytest.mark.integration
def test_insert_warns_about_unknown_key(tmp_path):
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    (entry,) = _read_po_file(base / 'source' / 'escapes.ks.po')
    (base / 'target').mkdir()
    (base / 'target' / 'escapes.ks.po').write_text(
        _po_text([(entry.msgctxt, entry.msgid, ''), ('<<<TRANS:999>>>', 'Jabberwocky', 'Bredouillon')]),
        encoding='utf_8',
    )
    result = _insert(filetype, base)
    assert '<<<TRANS:999>>>' in result.output
    assert (base / 'output' / 'escapes.ks').read_bytes() == FIXTURES['kirikiri-escapes'][1]['escapes.ks']


@pytest.mark.integration
def test_insert_rejects_duplicate_key(tmp_path):
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    (entry,) = _read_po_file(base / 'source' / 'escapes.ks.po')
    (base / 'target').mkdir()
    (base / 'target' / 'escapes.ks.po').write_text(
        _po_text([(entry.msgctxt, entry.msgid, 'one'), (entry.msgctxt, entry.msgid, 'two')]), encoding='utf_8'
    )
    result = _insert(filetype, base, ok=False)
    assert result.exit_code != 0
    assert entry.msgctxt in result.output


@pytest.mark.integration
def test_insert_reports_malformed_po(tmp_path):
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    (base / 'target').mkdir()
    (base / 'target' / 'escapes.ks.po').write_text('msgid "oops\n', encoding='utf_8')
    result = _insert(filetype, base, ok=False)
    assert result.exit_code != 0
    assert 'escapes.ks.po' in result.output and 'line 1' in result.output


@pytest.mark.integration
def test_skip_identical_with_po(tmp_path):
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    _translate_po(base, lambda _e: '')
    _insert(filetype, base, '--skip-identical')
    assert not (base / 'output' / 'escapes.ks').exists()


@pytest.mark.integration
def test_insert_reads_shelf_from_three_field_textline(tmp_path, monkeypatch):
    """Shelves written before TextLine gained ``speaker`` still work with txt and PO translations."""
    filetype, base = _extract(tmp_path, 'kirikiri-escapes', 'po')
    shelf_path = str(base / 'working' / 'escapes.ks.shelf')
    with shelve.open(shelf_path) as shelf:
        lines, metadata = shelf['lines'], shelf['metadata']

    old = namedtuple('TextLine', ['key', 'text', 'eol'], module='toto.util')
    with monkeypatch.context() as m:
        m.setattr(toto.util, 'TextLine', old)
        with shelve.open(shelf_path, flag='n') as shelf:
            shelf['lines'] = {k: old(t.key, t.text, t.eol) for k, t in lines.items()}
            shelf['metadata'] = metadata

    with shelve.open(shelf_path) as shelf:
        (loaded,) = shelf['lines'].values()
    assert type(loaded) is toto.util.TextLine and loaded.speaker is None

    _translate_po(base, lambda _e: 'Alice said nothing.')
    _insert(filetype, base)
    assert (base / 'output' / 'escapes.ks').read_bytes() == b'Alice said nothing.\n[p]\n\n'


@pytest.mark.unit
def test_po_text_helper_reads_back():
    text = _po_text([('k', 'a "b"', 'c\\n')])
    assert [(e.msgctxt, e.msgid, e.msgstr) for e in read_po(io.StringIO(text))] == [('k', 'a "b"', 'c\\n')]
