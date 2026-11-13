"""Roundtrip tests for real DxLib script files.

Skipped automatically when the nonfree samples directory is absent or empty.
"""

import struct
from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.DxLib import DxLib, Script, _code_refs

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
    intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(original), unwrap=False)
    trans = {t.key: t for t in textlines}
    intermediate.seek(0)
    output = DxLib.insert_lines(intermediate, trans, cp932_fixup=metadata.get('cp932_fixup'))

    assert output.read() == original, f"roundtrip mismatch for {script_file.name}"


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "script_file",
    _files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _files],
)
def test_unwrap_identity_roundtrip(script_file):
    """Unwrapped extract → identity translate → insert at the default width is byte-identical."""
    original = script_file.read_bytes()
    intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(original), unwrap=True)
    trans = {t.key: t for t in textlines}
    output = DxLib.insert_lines(intermediate, trans, width=DxLib.default_width, **metadata)

    assert output.read() == original, f"roundtrip mismatch for {script_file.name}"


@pytest.mark.nonfree
@pytest.mark.parametrize(
    "script_file",
    _files,
    ids=[str(p.relative_to(NONFREE_DIR)) for p in _files],
)
def test_unwrap_rewrap_produces_valid_script(script_file):
    """Replacing every message with long English text gives a script that still parses,
    with every label and jump landing on a record of the same kind as before."""
    original = script_file.read_bytes()
    intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(original), unwrap=True)
    english = "Alice was beginning to get very tired of sitting by her sister on the bank."
    trans = {t.key: t._replace(text=english * (1 + i % 3)) for i, t in enumerate(textlines)}
    output = DxLib.insert_lines(intermediate, trans, width=DxLib.default_width, **metadata).read()

    before, after = Script.parse(original), Script.parse(output)
    assert _targets(after) == _targets(before)
    assert struct.unpack_from("<I", output, 0)[0] == struct.unpack_from("<I", original, 0)[0] - len(original) + len(
        output
    )


def _targets(script):
    by_off = {r.offset: r.op for r in script.records}
    by_off[script.code_end] = "END"
    found = [("label", by_off[o]) for o in script.label_offsets]
    for r in script.records:
        found += [(r.op, by_off[o]) for o in _code_refs(r.payload)]
    return found
