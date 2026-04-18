"""Roundtrip tests for real AdvHD WS2 files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.AdvHdWs2 import AdvHdWs2

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"


def _collect_ws2_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob("*.ws2"))


_ws2_files = _collect_ws2_files()


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "ws2_file",
    _ws2_files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _ws2_files],
)
def test_identity_roundtrip(ws2_file):
    """Extract -> identity translate -> insert produces byte-identical output."""
    original = ws2_file.read_bytes()
    intermediate, textlines, metadata = AdvHdWs2.extract_lines(BytesIO(original))
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = AdvHdWs2.insert_lines(intermediate, trans)
    assert output.read() == original, f"roundtrip mismatch for {ws2_file.name}"
