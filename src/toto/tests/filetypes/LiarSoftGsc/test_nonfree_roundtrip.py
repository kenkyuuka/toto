"""Roundtrip tests for real Liar-soft GSC files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.LiarSoftGsc import LiarSoftGsc, build_gsc, parse_gsc

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"


def _collect_gsc_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob("*.gsc"))


_gsc_files = _collect_gsc_files()
_ids = [str(p.relative_to(NONFREE_DIR)) for p in _gsc_files]


@pytest.mark.nonfree
@pytest.mark.parametrize("gsc_file", _gsc_files, ids=_ids)
def test_parse_build_identity(gsc_file):
    original = gsc_file.read_bytes()
    assert build_gsc(parse_gsc(original)) == original


@pytest.mark.nonfree
@pytest.mark.parametrize("gsc_file", _gsc_files, ids=_ids)
def test_identity_roundtrip(gsc_file):
    """Extract -> identity translate -> insert produces byte-identical output."""
    original = gsc_file.read_bytes()
    intermediate, textlines, metadata = LiarSoftGsc.extract_lines(BytesIO(original))
    trans = {t.key: t for t in textlines}
    output = LiarSoftGsc.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
    assert output.read() == original, f"roundtrip mismatch for {gsc_file.name}"
