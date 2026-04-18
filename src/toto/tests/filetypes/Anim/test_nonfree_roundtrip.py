"""Roundtrip tests for real Anim engine .dat files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.Anim import Anim

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"

_EXTENSIONS = ("*_define.dat", "*_sce.dat")


def _collect_files():
    if not NONFREE_DIR.is_dir():
        return []
    files = []
    for ext in _EXTENSIONS:
        files.extend(NONFREE_DIR.rglob(ext))
    return sorted(files)


_files = _collect_files()


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "dat_file",
    _files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _files],
)
def test_identity_roundtrip(dat_file):
    """Extract → identity translate → insert produces byte-identical output."""
    original = dat_file.read_bytes()
    f = BytesIO(original)
    f.name = dat_file.name

    intermediate, textlines, metadata = Anim.extract_lines(f)
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = Anim.insert_lines(
        intermediate,
        trans,
        encryption_key=metadata.get('encryption_key'),
        cp932_fixup=metadata.get('cp932_fixup'),
    )

    assert output.read() == original, f"roundtrip mismatch for {dat_file.name}"
