"""Tests for BLACKRAINBOW ADVDX ``.spt`` scripts.

Scripts are assembled from synthetic records (Alice in Wonderland text) so every
field can be checked: masked message strings, record lengths and the header's
body size.
"""

import re
import struct
from io import BytesIO

import pytest

from toto.filetypes.BlackRainbowSpt import KNOWN_KEYS, BlackRainbowSpt, detect_key, mask, parse_records

OLD_KEY = bytes.fromhex("01000000")
NEW_KEY = bytes.fromhex("2bc52a3d")

ALICE = "アリス"
RABBIT = "白ウサギ"
SPEECH = "「白ウサギを見なかった？」"
LONG_SPEECH = ["「ねえ、白ウサギさん、そんなに急いでどこへ", "　行くの？　待ってちょうだい！」"]
NARRATION = ["アリスはうさぎの穴に落ちていった。", "どこまでも、どこまでも。"]


# --------------------------------------------------------------------------- #
# Builders


def s(text, nul=False):
    raw = text.encode("cp932") + (b"\0" if nul else b"")
    return struct.pack("<I", len(raw)) + raw


def message(msg_id, text, name="", key=OLD_KEY, voice=0xFFFFFFFF, speaker=0xFFFFFFFF):
    n, t = name.encode("cp932"), text.encode("cp932")
    return 0x08, struct.pack("<5I", msg_id, voice, speaker, len(n), len(t)) + mask(n, key) + mask(t, key)


def speech(msg_id, name, lines, display=None, key=OLD_KEY):
    return message(
        msg_id, f"【{display or name}】\r\n" + "\r\n".join(lines), name=name, key=key, voice=msg_id, speaker=1
    )


def choice(*options, flag=0x10):
    raw = "".join(o + "\\0" for o in options).encode("cp932")
    return 0x0E, struct.pack("<III", flag, 0, len(raw)) + raw


def build(records, name="alice.spt"):
    body = b"".join(struct.pack("<II", op, len(pl)) + pl for op, pl in records)
    header = name.encode("ascii").ljust(0x40, b"\0") + struct.pack("<III", 0, len(records), len(body))
    return header + body


BG = (0x13, struct.pack("<II", 0x10, 1) + b"sound\\\\bgm01.ogg")
PADDED = (0x1B, struct.pack("<II", 0, 0) + b"\xcc" * 12)  # MSVC debug fill must survive
GOTO = (0x09, s("rabbit_hole.spt", nul=True))


def sample(key=OLD_KEY):
    return build(
        [
            BG,
            (0x1D, s("お茶会")),
            (0x1E, s("アリス\\n不思議の国へ")),
            message(0, "\r\n".join(NARRATION), key=key),
            speech(1, ALICE, [SPEECH], key=key),
            PADDED,
            speech(2, "その他", LONG_SPEECH, display=RABBIT, key=key),
            choice("ウサギを追う", "お茶会に行く"),
            GOTO,
        ]
    )


def extract(data, **kwargs):
    return BlackRainbowSpt.extract_lines(BytesIO(data), **kwargs)


def roundtrip(data, edit=None, width=None, **kwargs):
    intermediate, lines, metadata = extract(data, **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit.get(t.text, t.text)) for k, t in trans.items()}
    return BlackRainbowSpt.insert_lines(intermediate, trans, width=width, **metadata).read(), lines


def messages(data, key):
    out = []
    for op, pl in parse_records(data):
        if op == 0x08:
            msg_id, voice, speaker, nl, tl = struct.unpack_from("<5I", pl)
            assert len(pl) == 20 + nl + tl
            name = mask(pl[20 : 20 + nl], key).decode("cp932")
            out.append((msg_id, voice, speaker, name, mask(pl[20 + nl :], key).decode("cp932")))
    return out


# --------------------------------------------------------------------------- #
# Cipher and key detection


@pytest.mark.unit
def test_mask_matches_worked_example():
    """Each string is XORed with the 4-byte key from its own offset 0."""
    plain = "【アリス】\r\n「白".encode("cp932")
    assert mask(plain, OLD_KEY).hex(" ") == "80 79 83 41 82 8a 83 58 80 7a 0d 0a 80 75 94 92"
    assert mask(plain, NEW_KEY).hex(" ") == "aa bc a9 7c a8 4f a9 65 aa bf 27 37 aa b0 be af"
    assert mask(mask(plain, NEW_KEY), NEW_KEY) == plain


@pytest.mark.unit
def test_known_keys():
    assert OLD_KEY in KNOWN_KEYS and NEW_KEY in KNOWN_KEYS


@pytest.mark.unit
@pytest.mark.parametrize("key", [OLD_KEY, NEW_KEY], ids=["old", "fullvoice"])
def test_detect_key(key):
    assert detect_key(parse_records(sample(key))) == key


@pytest.mark.unit
@pytest.mark.parametrize("key", [OLD_KEY, NEW_KEY], ids=["old", "fullvoice"])
def test_detect_key_single_short_message(key):
    """One short message is enough to tell the known keys apart."""
    data = build([message(0, "はい。", key=key)])
    assert detect_key(parse_records(data)) == key


@pytest.mark.unit
def test_detect_key_without_messages():
    """A script with no messages has nothing masked and needs no key."""
    data = build([BG, choice("はい", "いいえ")])
    assert detect_key(parse_records(data)) is None


@pytest.mark.unit
def test_empty_messages_roundtrip_without_key():
    """Messages with empty strings need no key; the script still roundtrips and translates."""
    data = build([(0x08, struct.pack("<5I", 0, 0xFFFFFFFF, 0xFFFFFFFF, 0, 0)), (0x1D, s("お茶会"))])
    out, lines = roundtrip(data)
    assert out == data
    out, _ = roundtrip(data, edit={"お茶会": "Tea Party"})
    assert parse_records(out)[1] == (0x1D, s("Tea Party"))


@pytest.mark.unit
def test_unknown_key_is_an_error():
    data = build([message(0, "\r\n".join(NARRATION), key=bytes.fromhex("5aa55aa5"))])
    with pytest.raises(ValueError, match="--key"):
        extract(data)


@pytest.mark.unit
def test_explicit_key():
    key = bytes.fromhex("5aa55aa5")
    data = build([message(0, "\r\n".join(NARRATION), key=key)])
    _, lines, metadata = extract(data, key="5aa55aa5")
    assert [t.text for t in lines] == ["".join(NARRATION)]
    assert metadata["encryption_key"] == key


@pytest.mark.unit
def test_wrong_explicit_key_is_an_error():
    with pytest.raises(ValueError, match="CP932"):
        extract(sample(NEW_KEY), key=OLD_KEY.hex())


# --------------------------------------------------------------------------- #
# Parsing


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\0" * 0x4C + b"\x08\0\0\0\xff\0\0\0",  # record runs past EOF
        sample()[:-1],
        sample()[:0x44] + struct.pack("<I", 99) + sample()[0x48:],  # wrong record count
        sample()[:0x48] + struct.pack("<I", 5) + sample()[0x4C:],  # wrong body size
    ],
    ids=["empty", "overrun", "truncated", "count", "body_size"],
)
def test_rejects_malformed(data):
    with pytest.raises(ValueError):
        parse_records(data)


@pytest.mark.unit
def test_rejects_malformed_message():
    op, pl = message(0, "アリス")
    bad = build([(op, pl[:16] + struct.pack("<I", 99) + pl[20:])])
    with pytest.raises(ValueError, match="message"):
        extract(bad)


# --------------------------------------------------------------------------- #
# Extraction


@pytest.mark.unit
def test_extract_unwrapped():
    """Speech is split into display name and body; bodies are joined without their indent."""
    _, lines, metadata = extract(sample())
    assert [t.text for t in lines] == [
        "お茶会",
        "アリス\n不思議の国へ",
        "".join(NARRATION),
        ALICE,
        SPEECH,
        RABBIT,
        "「ねえ、白ウサギさん、そんなに急いでどこへ行くの？　待ってちょうだい！」",
        "ウサギを追う",
        "お茶会に行く",
    ]
    assert [t.key for t in lines] == [f"<<<TRANS:{i}>>>" for i in range(len(lines))]
    assert metadata["encryption_key"] == OLD_KEY


@pytest.mark.unit
def test_extract_names_once():
    """A display name is exported once per file and shared by every message that shows it."""
    data = build(
        [
            speech(0, ALICE, [SPEECH]),
            speech(1, RABBIT, ["「遅刻だ、遅刻だ！」"]),
            speech(2, ALICE, ["「待って！」"]),
            # Same display name under a different internal speaker tag.
            speech(3, "その他", ["「どうしよう」"], display=ALICE),
        ]
    )
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == [ALICE, SPEECH, RABBIT, "「遅刻だ、遅刻だ！」", "「待って！」", "「どうしよう」"]

    edit = {ALICE: "Alice", RABBIT: "White Rabbit"}
    out, _ = roundtrip(data, edit=edit, width=44)
    displays = [text.split("\r\n")[0] for *_, text in messages(out, OLD_KEY)]
    assert displays == ["【Alice】", "【White Rabbit】", "【Alice】", "【Alice】"]


@pytest.mark.unit
def test_extract_without_unwrap_keeps_breaks():
    _, lines, _ = extract(sample(), unwrap=False)
    texts = [t.text for t in lines]
    assert "\n".join(NARRATION) in texts
    assert "\n".join(LONG_SPEECH) in texts


@pytest.mark.unit
def test_ignore_patterns():
    _, lines, _ = extract(sample(), ignore_patterns=[re.compile("^お茶会")])
    texts = [t.text for t in lines]
    assert "お茶会" not in texts and "お茶会に行く" not in texts
    assert "ウサギを追う" in texts


# --------------------------------------------------------------------------- #
# Insertion


@pytest.mark.unit
@pytest.mark.parametrize("key", [OLD_KEY, NEW_KEY], ids=["old", "fullvoice"])
@pytest.mark.parametrize("unwrap", [True, False], ids=["unwrap", "no-unwrap"])
@pytest.mark.parametrize("width", [None, 44], ids=["nowidth", "width44"])
def test_identity_roundtrip(key, unwrap, width):
    data = sample(key)
    out, _ = roundtrip(data, unwrap=unwrap, width=width)
    assert out == data


@pytest.mark.unit
@pytest.mark.parametrize("key", [OLD_KEY, NEW_KEY], ids=["old", "fullvoice"])
def test_translated_message(key):
    """Translations are re-masked; lengths, body size and other fields are rewritten consistently."""
    data = sample(key)
    edit = {
        ALICE: "Alice",
        SPEECH: '"Have you seen the White Rabbit?"',
        RABBIT: "White Rabbit",
        "お茶会": "Tea Party",
        "アリス\n不思議の国へ": "Alice\nInto Wonderland",
        "ウサギを追う": "Follow the Rabbit",
    }
    out, _ = roundtrip(data, edit=edit, width=44)

    assert struct.unpack_from("<III", out, 0x40) == (0, 9, len(out) - 0x4C)
    msgs = messages(out, key)
    assert msgs[1] == (1, 1, 1, ALICE, '【Alice】\r\n"Have you seen the White Rabbit?"')
    # The internal speaker tag is never translated; the display name is.
    assert msgs[2][3] == "その他" and msgs[2][4].startswith("【White Rabbit】\r\n")

    recs = parse_records(out)
    assert recs[1] == (0x1D, s("Tea Party"))
    assert recs[2] == (0x1E, s("Alice\\nInto Wonderland"))
    assert recs[7][1][12:] == "Follow the Rabbit\\0お茶会に行く\\0".encode("cp932")
    assert struct.unpack_from("<I", recs[7][1], 8)[0] == len(recs[7][1]) - 12
    # Non-text records, including uninitialised padding, pass through untouched.
    assert recs[0] == BG and recs[5] == PADDED and recs[8] == GOTO


@pytest.mark.unit
def test_narration_is_wrapped():
    """The engine doesn't wrap, so translated text gets line breaks at the wrap width."""
    english = "Alice was beginning to get very tired of sitting by her sister on the bank."
    out, _ = roundtrip(sample(), edit={"".join(NARRATION): english}, width=44)
    body = messages(out, OLD_KEY)[0][4].split("\r\n")
    assert len(body) == 2 and all(len(line) <= 44 for line in body)
    assert not body[1].startswith("　")
    assert " ".join(body) == english
    assert len(parse_records(out)) == 9


@pytest.mark.unit
def test_speech_is_wrapped_with_hanging_indent():
    english = '"Oh dear! Oh dear! I shall be too late!" cried the White Rabbit.'
    joined = "「ねえ、白ウサギさん、そんなに急いでどこへ行くの？　待ってちょうだい！」"
    out, _ = roundtrip(sample(), edit={joined: english}, width=44)
    head, *body = messages(out, OLD_KEY)[2][4].split("\r\n")
    assert head == "【白ウサギ】"
    assert len(body) == 2 and all(len(line) <= 44 for line in body)
    assert body[1].startswith("　")
    assert " ".join(line.removeprefix("　") for line in body) == english


@pytest.mark.unit
def test_long_speech_is_split_into_pages():
    """Text needing more than two lines continues on extra pages that repeat the speaker."""
    english = (
        '"Oh dear! Oh dear! I shall be too late!" cried the White Rabbit, taking a watch out of '
        "its waistcoat-pocket and hurrying on, while Alice ran across the field after it."
    )
    joined = "「ねえ、白ウサギさん、そんなに急いでどこへ行くの？　待ってちょうだい！」"
    out, _ = roundtrip(sample(), edit={joined: english}, width=44)
    pages = [msg for msg in messages(out, OLD_KEY) if msg[0] == 2]
    assert len(pages) == 2
    # Same message ID and speaker on every page; only the first page plays the voice.
    assert [(p[0], p[1], p[2], p[3]) for p in pages] == [(2, 2, 1, "その他"), (2, 0xFFFFFFFF, 1, "その他")]
    lines = []
    for p in pages:
        head, *body = p[4].split("\r\n")
        assert head == "【白ウサギ】"
        assert 1 <= len(body) <= 2 and all(len(line) <= 44 for line in body)
        # Hanging indent on continuation lines, but not at the top of a new page.
        assert not body[0].startswith("　")
        assert all(line.startswith("　") for line in body[1:])
        lines += body
    assert " ".join(line.removeprefix("　") for line in lines) == english
    assert struct.unpack_from("<I", out, 0x44)[0] == 10
    # Records after the split message are unchanged and in order.
    assert parse_records(out)[-2:] == parse_records(sample())[-2:]


@pytest.mark.unit
def test_long_narration_is_split_into_pages():
    english = ("Curiouser and curiouser! " * 6).strip()
    out, _ = roundtrip(sample(), edit={"".join(NARRATION): english}, width=44)
    pages = [msg[4].split("\r\n") for msg in messages(out, OLD_KEY) if msg[0] == 0]
    assert len(pages) == 2
    assert all(len(p) <= 2 and all(len(line) <= 44 for line in p) for p in pages)
    assert " ".join(line for p in pages for line in p) == english


@pytest.mark.unit
def test_explicit_breaks_kept():
    out, _ = roundtrip(sample(), edit={"".join(NARRATION): "Down,\\ndown, down."}, width=44)
    assert messages(out, OLD_KEY)[0][4] == "Down,\r\ndown, down."


@pytest.mark.unit
def test_explicit_breaks_count_toward_pages():
    out, _ = roundtrip(sample(), edit={"".join(NARRATION): "Down.\\nDown.\\nDown.\\nThump!"}, width=44)
    assert [p[4] for p in messages(out, OLD_KEY) if p[0] == 0] == ["Down.\r\nDown.", "Down.\r\nThump!"]


@pytest.mark.unit
def test_no_width_does_not_wrap():
    english = ("Curiouser and curiouser! " * 6).strip()
    out, _ = roundtrip(sample(), edit={"".join(NARRATION): english}, width=None)
    assert messages(out, OLD_KEY)[0][4] == english
    assert len(parse_records(out)) == 9


@pytest.mark.unit
def test_cp932_fixup_preserved():
    """IBM-extension characters keep their original byte form on reinsertion."""
    ibm = b"\xfa\x40"  # ⅰ (IBM); Python re-encodes it as the NEC form 0xEEEF
    raw = "アリス".encode("cp932") + ibm
    data = build([(0x08, struct.pack("<5I", 0, 0xFFFFFFFF, 0xFFFFFFFF, 0, len(raw)) + mask(raw, OLD_KEY))])
    intermediate, lines, metadata = extract(data)
    trans = {t.key: t._replace(text=t.text + "。") for t in lines}
    out = BlackRainbowSpt.insert_lines(intermediate, trans, width=44, **metadata).read()
    assert ibm in mask(parse_records(out)[0][1][20:], OLD_KEY)
