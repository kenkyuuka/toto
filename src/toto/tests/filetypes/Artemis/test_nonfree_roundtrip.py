"""Roundtrip tests for real Artemis ``.txt`` scenario scripts.

Expects ``samples/nonfree/<game>/**/*.txt`` as extracted from ``.pfs`` by ``tamago pfs extract``.
Skipped automatically when the nonfree samples directory is absent or empty.
"""

import logging
from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.ArtemisTxt import ArtemisTxt

NONFREE_DIR = Path(__file__).parent / 'samples' / 'nonfree'

ENGLISH = (
    'Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: '
    'once or twice she had peeped into the book her sister was reading.'
)


def _collect_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob('*.txt'))


_files = _collect_files()
_ids = [str(p.relative_to(NONFREE_DIR)) for p in _files]


def _roundtrip(original, edit=None, **kwargs):
    intermediate, lines, metadata = ArtemisTxt.extract_lines(BytesIO(original), **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(i, t.text)) for i, (k, t) in enumerate(trans.items())}
    return ArtemisTxt.insert_lines(intermediate, trans, width=ArtemisTxt.default_width, **metadata).read(), lines


@pytest.mark.nonfree
@pytest.mark.parametrize('unwrap', [True, False], ids=['unwrap', 'no-unwrap'])
@pytest.mark.parametrize('script_file', _files, ids=_ids)
def test_identity_roundtrip(script_file, unwrap):
    """Extract → identity translate → insert produces byte-identical output."""
    original = script_file.read_bytes()
    assert _roundtrip(original, unwrap=unwrap)[0] == original


@pytest.mark.nonfree
@pytest.mark.parametrize('script_file', _files, ids=_ids)
def test_translated_script_keeps_its_structure(script_file, caplog):
    """Replacing all text with English leaves every non-text line alone, and re-extracting the
    result finds the same number of entries."""
    original = script_file.read_bytes()
    with caplog.at_level(logging.WARNING):
        output, lines = _roundtrip(original, edit=lambda i, _: ENGLISH[: 20 + (i * 7) % len(ENGLISH)])
    assert 'command' not in caplog.text

    def structure(data):
        """Blank and command lines, in order; tag lines with a translated ``text=`` value are masked."""
        out = []
        for line in data.decode('cp932').split('\r\n'):
            s = line.strip(' \t')
            if not s:
                out.append('')
            elif s.startswith(('[', '*', '//', ';', '@', '/*', '*/')):
                out.append('<text attr>' if ('[sel_text' in s or '[title' in s) else line)
        return out

    assert structure(output) == structure(original)
    _, again, _ = ArtemisTxt.extract_lines(BytesIO(output))
    assert len(again) == len(lines)
    assert [t.speaker for t in again] == [t.speaker for t in lines]
