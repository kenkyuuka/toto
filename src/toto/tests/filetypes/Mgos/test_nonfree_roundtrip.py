"""Roundtrip tests for real mgos .o files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.Mgos import Mgos

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"


def _collect_o_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob("*.o"))


_o_files = _collect_o_files()


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "o_file",
    _o_files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _o_files],
)
def test_identity_roundtrip(o_file):
    """Extract → identity translate → insert produces byte-identical output."""
    original = o_file.read_bytes()
    intermediate, textlines, metadata = Mgos.extract_lines(BytesIO(original))
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = Mgos.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))
    assert output.read() == original, f"roundtrip mismatch for {o_file.name}"
