"""Roundtrip tests for real KiriKiri .ks/.soc files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.KiriKiriScriptV2 import KiriKiriScript

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"

_EXTENSIONS = ("*.ks", "*.soc", "*.SOC")


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
    "script_file",
    _files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _files],
)
def test_identity_roundtrip(script_file):
    """Extract → identity translate → insert produces byte-identical output."""
    original = script_file.read_bytes()
    intermediate, textlines, metadata = KiriKiriScript.extract_lines(BytesIO(original), line_end_macros=None)

    translation_dict = {tl.key: tl for tl in textlines}
    output = KiriKiriScript.insert_lines(
        intermediate,
        translation_dict,
        codec=metadata['codec'],
        bom=metadata.get('bom', b''),
        cp932_fixup=metadata.get('cp932_fixup'),
    )

    assert output.read() == original, f"roundtrip mismatch for {script_file.name}"
