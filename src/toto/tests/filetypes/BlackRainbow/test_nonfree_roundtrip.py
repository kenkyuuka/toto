"""Roundtrip tests for real BLACKRAINBOW ``.spt`` scripts.

Expects ``samples/nonfree/<game>/*.spt`` as extracted by ``tamago blackrainbow extract``.
Skipped automatically when the nonfree samples directory is absent or empty.
"""

import struct
from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.BlackRainbowSpt import BlackRainbowSpt, Message, parse_records

NONFREE_DIR = Path(__file__).parent / "samples" / "nonfree"

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, and of having nothing to do: "
    "once or twice she had peeped into the book her sister was reading."
)


def _collect_files():
    if not NONFREE_DIR.is_dir():
        return []
    return sorted(NONFREE_DIR.rglob("*.spt"))


_files = _collect_files()
_ids = [str(p.relative_to(NONFREE_DIR)) for p in _files]


def _roundtrip(original, edit=None, **kwargs):
    intermediate, lines, metadata = BlackRainbowSpt.extract_lines(BytesIO(original), **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(i, t.text)) for i, (k, t) in enumerate(trans.items())}
    return BlackRainbowSpt.insert_lines(intermediate, trans, width=BlackRainbowSpt.default_width, **metadata).read()


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
    """Replacing all text with English keeps every record and non-text payload; long messages
    gain continuation pages that share the message ID and speaker but play no voice."""
    original = script_file.read_bytes()
    output = _roundtrip(original, edit=lambda i, _: ENGLISH[: 20 + (i * 7) % len(ENGLISH)])

    before, after = parse_records(original), parse_records(output)
    assert struct.unpack_from("<II", output, 0x44) == (len(after), len(output) - 0x4C)
    kept = []
    for op, pl in after:
        if op == 0x08 and kept and kept[-1][0] == 0x08:
            prev, page = Message.parse(kept[-1][1]), Message.parse(pl)
            if page.msg_id == prev.msg_id:
                assert (page.voice, page.speaker, page.name) == (0xFFFFFFFF, prev.speaker, prev.name)
                continue
        kept.append((op, pl))
    assert [op for op, _ in kept] == [op for op, _ in before]
    for (op, old), (_, new) in zip(before, kept, strict=True):
        if op == 0x08:
            a, b = Message.parse(old), Message.parse(new)
            assert (a.msg_id, a.voice, a.speaker, a.name) == (b.msg_id, b.voice, b.speaker, b.name)
        elif op not in (0x0E, 0x1D, 0x1E):
            assert new == old


EXPECTED_KEYS = {
    "saiminjutsu2": "01000000",
    "urasaiminjutsu2dl": "01000000",
    "kokukouhon2": "01000000",
    "saiminjutsu_fullvoice": "2bc52a3d",
}


@pytest.mark.nonfree
@pytest.mark.parametrize("script_file", _files, ids=_ids)
def test_detected_key(script_file):
    """Each game's scripts are detected with that game's key (or none, for scripts without messages)."""
    _, _, metadata = BlackRainbowSpt.extract_lines(BytesIO(script_file.read_bytes()))
    expected = EXPECTED_KEYS[script_file.parent.name]
    assert metadata.get("encryption_key", bytes.fromhex(expected)).hex() == expected
