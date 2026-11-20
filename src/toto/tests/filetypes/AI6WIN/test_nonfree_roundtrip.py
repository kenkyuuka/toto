"""Roundtrip tests for real AI6WIN ``.mes`` scripts.

Expects ``samples/nonfree/<game>/*.mes`` as extracted from ``mes.arc`` by ``tamago ai6win extract``.
Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.Ai6WinMes import OFFSET_OPS, OP_ESCAPE, OP_STR, Ai6WinMes, parse_script

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: "
    "once or twice she had peeped into the book her sister was reading."
)


def _collect_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob("*.mes"))


_files = _collect_files()
_ids = [str(p.relative_to(NONFREE_DIR)) for p in _files]


def _roundtrip(original, edit=None, **kwargs):
    intermediate, lines, metadata = Ai6WinMes.extract_lines(BytesIO(original), **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(i, t.text)) for i, (k, t) in enumerate(trans.items())}
    return Ai6WinMes.insert_lines(intermediate, trans, width=Ai6WinMes.default_width, **metadata).read()


@pytest.mark.nonfree
@pytest.mark.parametrize("unwrap", [True, False], ids=["unwrap", "no-unwrap"])
@pytest.mark.parametrize("script_file", _files, ids=_ids)
def test_identity_roundtrip(script_file, unwrap):
    """Extract → identity translate → insert produces byte-identical output."""
    original = script_file.read_bytes()
    assert _roundtrip(original, unwrap=unwrap) == original


@pytest.mark.nonfree
@pytest.mark.parametrize("script_file", _files, ids=_ids)
def test_translated_script_is_consistent(script_file):
    """Replacing all text with wrapped English keeps every other instruction, and every
    offset still points at the instruction it pointed at before."""
    original = script_file.read_bytes()
    output = _roundtrip(original, edit=lambda i, _: ENGLISH[: 20 + (i * 7) % len(ENGLISH)])

    before, after = parse_script(original), parse_script(output)  # parse_script validates offsets

    def code(script):
        """Non-text instructions, with offsets replaced by the index of the instruction they target."""
        index = {
            ins.offset: n for n, ins in enumerate(i for i in script.instructions if i.op not in (OP_STR, OP_ESCAPE))
        }
        out = []
        for ins in script.instructions:
            if ins.op in (OP_STR, OP_ESCAPE):
                continue
            out.append((ins.op, index.get(ins.arg, "text") if ins.op in OFFSET_OPS else ins.arg))
        return out

    assert code(after) == code(before)
    assert len(after.message_offsets) == len(before.message_offsets)
    if any(ins.op == OP_STR for ins in before.instructions):
        assert any(b"Alice" in ins.arg for ins in after.instructions if ins.op == OP_STR)
