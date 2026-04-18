"""Roundtrip tests for real AGSD SPT files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.AgsdSpt import AgsdSpt

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"


def _collect_spt_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob("*.spt"))


_spt_files = _collect_spt_files()


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "spt_file",
    _spt_files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _spt_files],
)
def test_identity_roundtrip(spt_file):
    """Extract -> identity translate -> insert produces byte-identical output."""
    original = spt_file.read_bytes()
    intermediate, textlines, metadata = AgsdSpt.extract_lines(BytesIO(original))
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = AgsdSpt.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
    assert output.read() == original, f"roundtrip mismatch for {spt_file.name}"
