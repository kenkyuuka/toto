"""Roundtrip tests for real System-NNN ``spt/`` and ``nya/`` folders.

Expects ``samples/nonfree/<game>/`` to hold a copy of a game's ``spt/`` folder (with its
``nya/`` folder inside as ``nya/``). Skipped automatically when the directory is absent.
"""

from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.SystemNnnSpt import SystemNnnSpt, crypt, iter_records, read_table
from toto.filetypes.SystemNnnXtx import SystemNnnXtx, tokens

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: "
    "once or twice she had peeped into the book her sister was reading."
)


def _collect(pattern):
    return sorted(NONFREE_DIR.rglob(pattern)) if NONFREE_DIR.is_dir() else []


_spt = _collect("*.spt")
_xtx = _collect("*.xtx")


def _ids(files):
    return [str(p.relative_to(NONFREE_DIR)) for p in files]


def _roundtrip(handler, original, edit=None, **kwargs):
    intermediate, lines, metadata = handler.extract_lines(BytesIO(original), **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(i, t.text)) for i, (k, t) in enumerate(trans.items())}
    insert_kwargs = {"width": handler.default_width} if handler.default_wrap is not None else {}
    return handler.insert_lines(intermediate, trans, **metadata, **insert_kwargs).read(), lines


@pytest.mark.nonfree
@pytest.mark.parametrize("unwrap", [True, False], ids=["unwrap", "no-unwrap"])
@pytest.mark.parametrize("script_file", _spt, ids=_ids(_spt))
def test_spt_identity_roundtrip(script_file, unwrap):
    """Extract → identity translate → insert produces byte-identical output."""
    original = script_file.read_bytes()
    output, _ = _roundtrip(SystemNnnSpt, original, unwrap=unwrap)
    assert output == original


@pytest.mark.nonfree
@pytest.mark.parametrize("script_file", _spt, ids=_ids(_spt))
def test_spt_translated_script_is_consistent(script_file):
    """Replacing every line with English keeps all records, and every string the handler
    did not extract, unchanged."""
    original = script_file.read_bytes()
    output, lines = _roundtrip(SystemNnnSpt, original, edit=lambda i, _: ENGLISH[: 10 + (i * 7) % len(ENGLISH)])
    before, after = crypt(original), crypt(output)

    old = [w for _, w in iter_records(before)]
    new = [w for _, w in iter_records(after)]
    assert sum(len(w) for w in new) * 4 == len(after)
    assert len(old) == len(new)
    for a, b in zip(old, new, strict=True):
        assert a[1:3] == b[1:3]
        if a[2] != 0x55550002 and a[0] != 0x20:
            assert a == b

    old_strings = [s[: s.index(b"\0")] for s in read_table(before, "strings")]
    new_strings = [s[: s.index(b"\0")] for s in read_table(after, "strings")]
    assert len(old_strings) == len(new_strings)
    changed = [i for i, (a, b) in enumerate(zip(old_strings, new_strings, strict=True)) if a != b]
    for i in changed:  # only choices change; prompts keep their marker byte
        assert new_strings[i].lstrip(b"%").startswith(b"Alice")
    if lines:
        new_messages = read_table(after, "messages")
        assert len(new_messages) == len(read_table(before, "messages"))
        assert all(b"Alice" in m for m in new_messages)


@pytest.mark.nonfree
@pytest.mark.parametrize("list_file", _xtx, ids=_ids(_xtx))
def test_xtx_identity_roundtrip(list_file):
    original = list_file.read_bytes()
    output, _ = _roundtrip(SystemNnnXtx, original)
    assert output == original


@pytest.mark.nonfree
@pytest.mark.parametrize("list_file", _xtx, ids=_ids(_xtx))
def test_xtx_translation_keeps_token_count(list_file):
    original = list_file.read_bytes()
    output, _ = _roundtrip(SystemNnnXtx, original, edit=lambda i, _: f"Alice, number {i}")
    assert len(list(tokens(output))) == len(list(tokens(original)))
