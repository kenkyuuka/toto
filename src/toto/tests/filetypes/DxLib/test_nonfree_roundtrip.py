"""Roundtrip tests for real DxLib script files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.DxLib import DxLib

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"


def _collect_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(p for p in NONFREE_DIR.rglob("*") if p.is_file())


_files = _collect_files()


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "script_file",
    _files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _files],
)
def test_identity_roundtrip(script_file):
    """Extract → identity translate → insert produces byte-identical output."""
    original = script_file.read_bytes()
    intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(original))
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = DxLib.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))

    assert output.read() == original, f"roundtrip mismatch for {script_file.name}"
