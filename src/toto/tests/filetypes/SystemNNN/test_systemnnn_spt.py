"""Tests for System-NNN ``.spt`` scripts.

Scripts are assembled from synthetic records (Alice in Wonderland text) with the same
layout as the engine's compiler output: a 32-word header, code records, then the label,
message, string and trailing data tables.
"""

import logging
import re
import struct
from io import BytesIO

import pytest

from toto.filetypes.SystemNnnSpt import SystemNnnSpt, crypt, iter_records, read_table

IDENTIFY_DATA = 0x66660001
IDENTIFY_SYSTEMCOMMAND = 0x66660006
IDENTIFY_SYSTEMFUNCTION = 0x66660007
CODE_HEADER0 = 0x55550001
CODE_TABLE = 0x55550002
PRINT = 0x22220001
SELECT = 0x22220006
STORYNAME = 0x33330051
LOADDWQ = 0x33330015
TYPE_LABEL, TYPE_MES, TYPE_STR = 0x44440003, 0x44440002, 0x44440001
TRAILING_TYPES = (0x44440004, 0x44440005, 0x44440006, 0x44440007, 0x44440008)

NARRATION = "アリスはうさぎの穴に落ちていった。\r\nどこまでも、どこまでも。"
SPEECH = "アリス\r\n「ねえ、白ウサギさん、そんなに急いでどこへ\r\n　行くの？　待ってちょうだい！」"
SHORT = "白ウサギ\r\n「遅刻だ、遅刻だ！」"
AGAIN = "アリス\r\n「まあ！」"
DUET = "アリス\r\n白ウサギ\r\n「お茶会へ！」"

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, "
    "and of having nothing to do: once or twice she had peeped into the book her sister was reading."
)


# --------------------------------------------------------------------------- #
# Builders


def table(type_, strings, start, junk=b""):
    """A DATA_TABLE record starting at word *start*; *junk* fills slot padding after the NUL."""
    ptrs, slots = [], b""
    pos = start + 4 + len(strings)
    for s in strings:
        raw = s.encode("cp932") if isinstance(s, str) else s
        pad = -(len(raw) + 1) % 4
        slot = raw + b"\0" + (junk * 4)[:pad].ljust(pad, b"\0")
        ptrs.append(pos)
        slots += slot
        pos += len(slot) // 4
    words = 4 + len(strings) + len(slots) // 4
    return struct.pack(f"<4I{len(strings)}I", words, IDENTIFY_DATA, CODE_TABLE, type_, *ptrs) + slots


def rec(*words):
    return [len(words) + 1, *words]


def select(options, prompts=()):
    """A SELECT record in the newest layout (prompt count and the four optional words)."""
    ids = [*prompts, *options]
    return rec(IDENTIFY_SYSTEMCOMMAND, SELECT, len(options), *ids, 7, 0, 0, 2, len(prompts))


def build(messages, strings, code=None, junk=b"", encrypt=True):
    """Assemble a script; *code* defaults to a story name plus one PRINT per message."""
    if code is None:
        code = [rec(IDENTIFY_SYSTEMFUNCTION, STORYNAME, 1, 0)]
        code += [rec(IDENTIFY_SYSTEMCOMMAND, PRINT, i, i) for i in range(len(messages))]
    body = b"".join(struct.pack(f"<{len(r)}I", *r) for r in code)
    pos = 32 + len(body) // 4
    label_at = pos
    body += struct.pack("<5I", 5, IDENTIFY_DATA, CODE_TABLE, TYPE_LABEL, 32)
    mes_at = pos = pos + 5
    mes = table(TYPE_MES, messages, pos, junk)
    body += mes
    str_at = pos = pos + len(mes) // 4
    st = table(TYPE_STR, strings, pos, junk)
    body += st
    pos += len(st) // 4
    trailing = []
    for type_ in TRAILING_TYPES:
        extra = [0x011500C4] if type_ == 0x44440006 else [32]
        trailing.append(pos)
        body += struct.pack(f"<{4 + len(extra)}I", 4 + len(extra), IDENTIFY_DATA, CODE_TABLE, type_, *extra)
        pos += 4 + len(extra)

    header = [0] * 32
    header[0:3] = [32, IDENTIFY_DATA, CODE_HEADER0]
    header[4:8] = [len(messages), mes_at + 4, len(strings), str_at + 4]
    header[8:11] = [1, label_at + 4, 1]
    header[12:16] = struct.unpack("<4I", b"SPTHEADER0     \0")
    header[16:19] = [label_at + 4, label_at + 4, trailing[0] + 4]
    header[20:24] = [t + 4 for t in trailing[1:]]
    data = struct.pack("<32I", *header) + body
    return crypt(data) if encrypt else data


def sample(**kwargs):
    strings = ["★トップ", "不思議の国", "bg_alice_001", "ウサギを追う", "お茶会に行く", "%どうしよう――"]
    code = [
        rec(IDENTIFY_SYSTEMFUNCTION, STORYNAME, 1, 1),
        rec(IDENTIFY_SYSTEMFUNCTION, LOADDWQ, 1, 2),
        *(rec(IDENTIFY_SYSTEMCOMMAND, PRINT, i, i) for i in range(4)),
        select([3, 4], prompts=[5]),
    ]
    return build([NARRATION, SPEECH, SHORT, AGAIN], strings, code=code, **kwargs)


def extract(data, **kwargs):
    return SystemNnnSpt.extract_lines(BytesIO(data), **kwargs)


def roundtrip(data, edit=None, width=SystemNnnSpt.default_width, **kwargs):
    intermediate, lines, metadata = extract(data, **kwargs)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(t.text)) for k, t in trans.items()}
    return SystemNnnSpt.insert_lines(intermediate, trans, width=width, **metadata).read()


def texts(data, which):
    plain = crypt(data)
    return [slot[: slot.index(b"\0")].decode("cp932") for slot in read_table(plain, which)]


# --------------------------------------------------------------------------- #
# Extraction


@pytest.mark.unit
def test_extracts_dialogue_names_once_and_choices():
    _, lines, _ = extract(sample())
    assert [t.text for t in lines] == [
        "アリスはうさぎの穴に落ちていった。どこまでも、どこまでも。",
        "アリス",
        "「ねえ、白ウサギさん、そんなに急いでどこへ行くの？　待ってちょうだい！」",
        "白ウサギ",
        "「遅刻だ、遅刻だ！」",
        "「まあ！」",
        "どうしよう――",
        "ウサギを追う",
        "お茶会に行く",
    ]
    assert [t.key for t in lines] == [f"<<<TRANS:{i}>>>" for i in range(len(lines))]


@pytest.mark.unit
def test_no_unwrap_keeps_line_breaks():
    _, lines, _ = extract(sample(), unwrap=False)
    assert lines[0].text == "アリスはうさぎの穴に落ちていった。\nどこまでも、どこまでも。"
    assert lines[2].text == "「ねえ、白ウサギさん、そんなに急いでどこへ\n　行くの？　待ってちょうだい！」"


@pytest.mark.unit
def test_labels_story_names_and_file_names_are_not_extracted():
    _, lines, _ = extract(sample())
    extracted = {t.text for t in lines}
    assert not extracted & {"★トップ", "不思議の国", "bg_alice_001"}


@pytest.mark.unit
def test_known_speaker_on_first_line_is_split_even_without_a_quote_after_it():
    _, lines, _ = extract(build([AGAIN, DUET], ["★トップ"]))
    assert [t.text for t in lines] == ["アリス", "「まあ！」", "白ウサギ「お茶会へ！」"]


@pytest.mark.unit
def test_ignore_patterns():
    import re

    _, lines, _ = extract(sample(), ignore_patterns=[re.compile("まあ")])
    assert "「まあ！」" not in [t.text for t in lines]


@pytest.mark.unit
def test_plaintext_script_is_accepted():
    _, lines, metadata = extract(sample(encrypt=False))
    assert lines[1].text == "アリス"


@pytest.mark.unit
def test_rejects_other_formats():
    with pytest.raises(ValueError, match="System-NNN"):
        extract(b"\0" * 0x40 + b"not a script at all")


# --------------------------------------------------------------------------- #
# Insertion


@pytest.mark.unit
@pytest.mark.parametrize("unwrap", [True, False])
@pytest.mark.parametrize("encrypt", [True, False])
def test_identity_roundtrip_is_byte_exact(unwrap, encrypt):
    """Unchanged text keeps its slot, including the compiler's junk padding."""
    data = sample(junk=b"\x52\x83\x93", encrypt=encrypt)
    assert roundtrip(data, unwrap=unwrap) == data


@pytest.mark.unit
def test_omegat_padding_does_not_count_as_a_change():
    data = sample(junk=b"\x52\x83\x93")
    assert roundtrip(data, edit=lambda t: f" {t} ") == data


@pytest.mark.unit
def test_translation_is_wrapped_and_tables_are_relocated():
    data = sample()
    plain = crypt(data)
    replacements = {
        "アリス": "Alice",
        "白ウサギ": "White Rabbit",
        "「まあ！」": '"Oh my!"',
        "ウサギを追う": "Follow the Rabbit",
        "どうしよう――": "What now?",
    }
    out = roundtrip(data, edit=lambda t: replacements.get(t, ENGLISH if t.startswith("「ねえ") else t))
    out_plain = crypt(out)

    messages = texts(out, "messages")
    assert messages[0] == NARRATION
    assert messages[1] == (
        "Alice\r\nAlice was beginning to get very tired of sitting\r\n"
        "　by her sister on the bank, and of having\r\n"
        "　nothing to do: once or twice she had peeped\r\n"
        "　into the book her sister was reading."
    )
    # A space would split the name in namecol.xtx, so it becomes a full-width space
    assert messages[2] == "White　Rabbit\r\n「遅刻だ、遅刻だ！」"
    assert messages[3] == 'Alice\r\n"Oh my!"'
    assert texts(out, "strings") == [
        "★トップ",
        "不思議の国",
        "bg_alice_001",
        "Follow the Rabbit",
        "お茶会に行く",
        "%What now?",
    ]

    # The record stream still ends exactly at end of file, with the same records in order;
    # everything but the header (whose pointers move) and the two text tables is unchanged
    old = [w for _, w in iter_records(plain)]
    new = [w for _, w in iter_records(out_plain)]
    assert [w[1:3] for w in new] == [w[1:3] for w in old]
    assert sum(len(w) for w in new) * 4 == len(out_plain)
    text_tables = [i for i, w in enumerate(old) if w[2] == CODE_TABLE and w[3] in (TYPE_MES, TYPE_STR)]
    assert len(text_tables) == 2
    for i, (a, b) in enumerate(zip(old, new, strict=True)):
        if i != 0 and i not in text_tables:
            assert a == b
    # Header words other than pointers are untouched
    hdr_old, hdr_new = struct.unpack_from("<32I", plain), struct.unpack_from("<32I", out_plain)
    for i in (0, 1, 2, 3, 4, 6, 8, 10, 11, 12, 13, 14, 15, 24):
        assert hdr_old[i] == hdr_new[i]
    # Trailing tables are found where the header says
    for slot, type_ in zip((18, 20, 21, 22, 23), TRAILING_TYPES, strict=True):
        assert struct.unpack_from("<I", out_plain, (hdr_new[slot] - 1) * 4)[0] == type_


@pytest.mark.unit
def test_hard_line_break_in_translation():
    out = roundtrip(build([NARRATION], ["★トップ"]), edit=lambda _: "Down,\\ndown, down.")
    assert texts(out, "messages") == ["Down,\r\ndown, down."]


@pytest.mark.unit
def test_no_width_keeps_translation_on_one_line():
    out = roundtrip(build([NARRATION], ["★トップ"]), edit=lambda _: ENGLISH, width=None)
    assert texts(out, "messages") == [ENGLISH]


@pytest.mark.unit
def test_too_many_lines_is_logged(caplog):
    with caplog.at_level(logging.WARNING):
        roundtrip(build([NARRATION], ["★トップ"]), edit=lambda _: ENGLISH * 2)
    assert "lines" in caplog.text


@pytest.mark.unit
def test_cp932_variant_is_restored():
    """An IBM extended kanji keeps its original encoding when the message is re-encoded."""
    ibm = "髙".encode("cp932")  # Python prefers the NEC form; force the IBM one
    ibm_raw = b"\xfb\xfc"
    assert ibm_raw.decode("cp932") == "髙" and ibm != ibm_raw
    name = ibm_raw + "橋".encode("cp932")
    msg = name + "\r\n「白ウサギ！」".encode("cp932")
    out = roundtrip(build([msg], ["★トップ"]), edit=lambda t: '"Rabbit!"' if t.startswith("「") else t)
    plain = crypt(out)
    assert read_table(plain, "messages")[0].startswith(name + b'\r\n"Rabbit!"\0')


@pytest.mark.unit
def test_speakers():
    """Bodies get their speaker; extra name lines over one line of speech are listed too."""
    _, lines, _ = extract(build([NARRATION, AGAIN, SHORT, DUET], []))
    assert [(t.text, t.speaker) for t in lines] == [
        ("アリスはうさぎの穴に落ちていった。どこまでも、どこまでも。", None),
        ("アリス", None),
        ("「まあ！」", "アリス"),
        ("白ウサギ", None),
        ("「遅刻だ、遅刻だ！」", "白ウサギ"),
        ("白ウサギ「お茶会へ！」", "アリス / 白ウサギ"),
    ]


@pytest.mark.unit
def test_speaker_kept_when_name_ignored():
    _, lines, _ = extract(build([SHORT], []), ignore_patterns=[re.compile("ウサギ$")])
    assert [(t.text, t.speaker) for t in lines] == [("「遅刻だ、遅刻だ！」", "白ウサギ")]
