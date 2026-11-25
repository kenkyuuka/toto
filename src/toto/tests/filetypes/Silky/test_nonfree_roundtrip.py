"""Roundtrip tests for real Silky Engine ``.mes`` scripts.

Expects ``samples/nonfree/<game>/*.MES`` as unpacked from ``Script.arc``.
Skipped automatically when the nonfree samples directory is absent or empty.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.SilkyMes import OFFSET_OPS, OP_NEWLINE, TEXT_OPS, SilkyMes, find_names, parse_script

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: "
    "once or twice she had peeped into the book her sister was reading."
)


def _collect_files():
    if not NONFREE_DIR.is_dir():
        return []
    return SilkyMes.get_paths(NONFREE_DIR)


_files = _collect_files()
_ids = [str(p.relative_to(NONFREE_DIR)) for p in _files]


def _roundtrip(original, edit=None, **kwargs):
    intermediate, lines, metadata = SilkyMes.extract_lines(BytesIO(original), **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(i, t.text)) for i, (k, t) in enumerate(trans.items())}
    return SilkyMes.insert_lines(intermediate, trans, width=SilkyMes.default_width, **metadata).read()


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
    """Replacing all text and names with English keeps every other instruction, and every
    offset and header entry still points at the instruction it pointed at before."""
    original = script_file.read_bytes()
    output = _roundtrip(original, edit=lambda i, _: ENGLISH[: 20 + (i * 7) % len(ENGLISH)])

    before, after = parse_script(original), parse_script(output)  # parse_script validates offsets

    def shape(script):
        """Non-text instructions, with offsets replaced by the index of the instruction they target."""
        names = {index for index, _ in find_names(script)}
        kept = [
            (n in names, i) for n, i in enumerate(script.instructions) if i.op not in TEXT_OPS and i.op != OP_NEWLINE
        ]
        index = {ins.offset: n for n, (_, ins) in enumerate(kept)}
        code = [
            (i.op, "name" if is_name else index.get(i.arg, "text") if i.op in OFFSET_OPS else i.arg)
            for is_name, i in kept
        ]
        return code, [index[o] for o in script.message_offsets], [index[o] for o in script.choice_offsets]

    assert shape(after) == shape(before)
    if any(ins.op in TEXT_OPS for ins in before.instructions):
        assert any(b"Alice" in ins.arg for ins in after.instructions if ins.op in TEXT_OPS)
