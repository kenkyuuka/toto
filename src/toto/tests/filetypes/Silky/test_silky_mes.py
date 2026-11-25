"""Tests for Silky Engine ``.mes`` scripts.

Scripts are assembled from synthetic instructions (Alice in Wonderland text) with symbolic
labels, so tests can check that every jump, choice link and header entry still points at the
same instruction after text changes length.
"""

import struct
from io import BytesIO

import pytest

from toto.filetypes.SilkyMes import (
    OP_LINK,
    OP_MESSAGE,
    OP_NEWLINE,
    OP_PUSH_STR,
    OP_STR_CRYPT,
    OP_STR_PLAIN,
    ScriptError,
    SilkyMes,
    decrypt,
    encrypt,
    parse_script,
)
from toto.util import TextLine

# --------------------------------------------------------------------------- #
# Assembler
#
# An item is a tuple: ("label", name) marks a position; ("message",) emits MESSAGE with the next
# message number, and ("choice_message",) does the same and also lists it in the header's choice
# table; ("jump"/"jz"/"link", label) emit an offset instruction; ("crypt", text) and ("plain", text)
# emit STR_CRYPT/STR_UNCRYPT; ("name", text) emits the speaker-name sequence; ("newline",),
# ("menu", n), ("push", n), ("push_str", text) and ("op", byte) emit the rest. bytes are verbatim.

OFFSET_OPS = {"jz": 0x14, "jump": 0x15, "link": OP_LINK}
SYSCALL = ("op", 0x18)
ADD = ("op", 0x34)
RETURN = ("op", 0x00)
WAIT = [("push", 3), ("push", 22), SYSCALL]  # the click wait that follows each message
NAME_CALL = [("push", 10), ("push", 5), ADD, ("push", 29), SYSCALL]


def _encode(item, labels, msg_no):
    kind = item[0] if isinstance(item, tuple) else None
    if kind is None:
        return item
    if kind == "label":
        return b""
    if kind in ("message", "choice_message"):
        return bytes([OP_MESSAGE]) + struct.pack(">I", msg_no)
    if kind in OFFSET_OPS:
        return bytes([OFFSET_OPS[kind]]) + struct.pack(">I", labels.get(item[1], 0))
    if kind == "crypt":
        return bytes([OP_STR_CRYPT]) + encrypt(item[1].encode("cp932")) + b"\0"
    if kind == "plain":
        return bytes([OP_STR_PLAIN]) + item[1].encode("cp932") + b"\0"
    if kind == "newline":
        return bytes([OP_NEWLINE, 0])
    if kind == "menu":
        return b"\x1a" + struct.pack(">I", item[1])
    if kind == "push":
        return b"\x32" + struct.pack(">i", item[1])
    if kind == "push_str":
        return bytes([OP_PUSH_STR]) + item[1].encode("cp932") + b"\0"
    if kind == "name":
        return _encode(("push_str", item[1]), labels, msg_no) + b"".join(_encode(i, labels, msg_no) for i in NAME_CALL)
    if kind == "op":
        return bytes([item[1]])
    raise ValueError(item)


def assemble(items):
    """Assemble *items* into a script; return ``(data, labels)`` with code-relative label offsets."""
    labels = {}
    for _ in range(2):  # the second pass sees every label
        code, msg_offsets, choice_offsets, pos = [], [], [], 0
        for item in items:
            if isinstance(item, tuple) and item[0] == "label":
                labels[item[1]] = pos
            if isinstance(item, tuple) and item[0] in ("message", "choice_message"):
                msg_offsets.append(pos)
                if item[0] == "choice_message":
                    choice_offsets.append(pos)
            raw = _encode(item, labels, len(msg_offsets) - 1)
            code.append(raw)
            pos += len(raw)
    header = struct.pack(
        f"<II{len(msg_offsets)}I{len(choice_offsets)}I",
        len(msg_offsets),
        len(choice_offsets),
        *msg_offsets,
        *choice_offsets,
    )
    return header + b"".join(code), labels


ALICE = "アリス"
RABBIT = "白ウサギ"
NARRATION = ["アリスはうさぎのあなにおちていった。", "どこまでもどこまでもおちていく。"]
ALICE_LINE = "しろいうさぎをみなかった？"
RABBIT_LINE = ["たいへんだ、", "ちこくしちゃう！"]
ALICE_LINE2 = "まってちょうだい！"
OPTIONS = ["ウサギをおいかける", "おちゃかいにいく"]

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, "
    "and of having nothing to do: once or twice she had peeped into the book her sister was reading."
)


def sample_items():
    return [
        ("push_str", "bg_garden.akb"),
        ("push", 1),
        SYSCALL,
        ("message",),
        ("crypt", NARRATION[0]),
        ("newline",),
        ("crypt", NARRATION[1]),
        *WAIT,
        ("message",),
        ("push", 0),
        ("push_str", "alice001.ogg"),
        ("push", 5),
        ("push", 18),
        SYSCALL,
        ("name", ALICE),
        ("crypt", ALICE_LINE),
        *WAIT,
        ("push", 0),
        ("jz", "shared"),
        ("message",),
        ("name", RABBIT),
        ("crypt", RABBIT_LINE[0]),
        ("newline",),
        ("crypt", RABBIT_LINE[1]),
        ("newline",),  # a trailing break, as some of the game's messages have
        *WAIT,
        ("jump", "after"),
        ("label", "shared"),
        ("message",),
        ("name", ALICE),
        ("crypt", ALICE_LINE2),
        *WAIT,
        ("label", "after"),
        ("menu", 0),
        ("choice_message",),
        ("push", -1),
        SYSCALL,
        ("push", 0),
        ("link", "opt1"),
        ("crypt", OPTIONS[0]),
        RETURN,
        ("label", "opt1"),
        ("push", 1),
        ("link", "end"),
        ("crypt", OPTIONS[1]),
        RETURN,
        ("label", "end"),
        ("push_str", "next.mes"),
        ("op", 0x17),
    ]


def sample():
    return assemble(sample_items())[0]


def extract(data, **kwargs):
    return SilkyMes.extract_lines(BytesIO(data), **kwargs)


def insert(intermediate, trans, **kwargs):
    intermediate.seek(0)
    return SilkyMes.insert_lines(intermediate, trans, **kwargs).read()


def roundtrip(data, edit=None, extract_kwargs=None, **kwargs):
    intermediate, lines, metadata = extract(data, **(extract_kwargs or {}))
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(t.text)) for k, t in trans.items()}
    return insert(intermediate, trans, **metadata, **kwargs)


def strings(data):
    """Each text instruction as ``(op, text)``, plus ``"\\n"`` for each line break."""
    out = []
    for ins in parse_script(data).instructions:
        if ins.op == OP_STR_CRYPT:
            out.append((ins.op, decrypt(ins.arg).decode("cp932")))
        elif ins.op == OP_STR_PLAIN:
            out.append((ins.op, ins.arg.decode("cp932")))
        elif ins.op == OP_NEWLINE:
            out.append("\n")
    return out


def code_shape(data):
    """Non-text instructions, with offsets replaced by the index of the instruction they target,
    and the header tables likewise."""
    script = parse_script(data)
    kept = [i for i in script.instructions if i.op not in (OP_STR_CRYPT, OP_STR_PLAIN, OP_NEWLINE)]
    index = {ins.offset: n for n, ins in enumerate(kept)}
    body = [(i.op, index[i.arg] if i.op in (0x14, 0x15, OP_LINK) else i.arg) for i in kept if i.op != OP_PUSH_STR]
    return body, [index[o] for o in script.message_offsets], [index[o] for o in script.choice_offsets]


# --------------------------------------------------------------------------- #
# Text compression


@pytest.mark.unit
def test_decrypt_worked_example():
    raw = bytes.fromhex("1d4d1d4d8b4e0d268e789378528e6e412a04288141")
    assert decrypt(raw).decode("cp932") == "そろそろ起きて支度を始めないと、"


@pytest.mark.unit
def test_encrypt_compresses_every_hiragana():
    assert encrypt("ぁん".encode("cp932")) == b"\x01\x53"
    assert encrypt("ア".encode("cp932")) == b"\x83\x41"  # trail byte below 0x81 is kept


@pytest.mark.unit
@pytest.mark.parametrize("text", ["そろそろ起きて支度を始めないと、", "アリスは「どこ？」とさけんだ。", ""])
def test_encrypt_roundtrip(text):
    raw = text.encode("cp932")
    assert decrypt(encrypt(raw)) == raw


@pytest.mark.unit
@pytest.mark.parametrize("text", ["Alice", "アリス Liddell", "ｱﾘｽ"])
def test_encrypt_rejects_single_byte_characters(text):
    with pytest.raises(ValueError):
        encrypt(text.encode("cp932"))


# --------------------------------------------------------------------------- #
# Parsing


@pytest.mark.unit
def test_parse_sample():
    data, labels = assemble(sample_items())
    script = parse_script(data)
    assert len(script.message_offsets) == 5
    assert script.choice_offsets == (script.message_offsets[4],)
    assert script.code_base == 8 + 4 * 6


@pytest.mark.unit
def test_parse_script_without_messages():
    data, _ = assemble([("push", 1), ("push", 2), SYSCALL, RETURN])
    script = parse_script(data)
    assert script.message_offsets == ()
    assert script.code_base == 8


@pytest.mark.unit
def test_menu_operand_is_not_an_offset():
    """1A's operand isn't remapped or checked as an offset (it's always 0 in the game)."""
    data, _ = assemble([("menu", 0x7777), ("message",), ("crypt", ALICE_LINE), RETURN])
    parse_script(data)


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"\x01\x00\x00", id="too-short"),
        pytest.param(struct.pack("<II", 5, 0), id="header-past-end"),
        pytest.param(struct.pack("<II", 0, 0) + b"\x99", id="unknown-opcode"),
        pytest.param(struct.pack("<II", 0, 0) + b"\x15\x00\x00\x00\x03", id="jump-into-operand"),
        pytest.param(struct.pack("<II", 0, 0) + b"\x1b\x00\x00\x00\x03", id="link-into-operand"),
        pytest.param(struct.pack("<III", 1, 0, 0) + b"\x00\x19\x00\x00\x00\x00", id="message-offset-mismatch"),
        pytest.param(struct.pack("<III", 1, 0, 0) + b"\x19\x00\x00\x00\x01", id="message-number"),
        pytest.param(struct.pack("<III", 0, 1, 5) + b"\x00\x00\x00", id="choice-offset-not-instruction"),
        pytest.param(struct.pack("<I", 1) + struct.pack("<I", 0) + b"\x19\x00\x00\x00\x00", id="ai6win-header"),
    ],
)
def test_parse_rejects_invalid(data):
    with pytest.raises(ScriptError):
        parse_script(data)


# --------------------------------------------------------------------------- #
# Extraction


@pytest.mark.unit
def test_extract_lines():
    _, lines, _ = extract(sample())
    assert [(t.text, t.speaker) for t in lines] == [
        ("".join(NARRATION), None),
        (ALICE, None),
        (ALICE_LINE, ALICE),
        (RABBIT, None),
        ("".join(RABBIT_LINE), RABBIT),
        (ALICE_LINE2, ALICE),
        (OPTIONS[0], None),
        (OPTIONS[1], None),
    ]
    assert all(isinstance(t, TextLine) and t.eol == b"" for t in lines)


@pytest.mark.unit
def test_extract_without_unwrap_keeps_line_breaks():
    _, lines, _ = extract(sample(), unwrap=False)
    assert lines[0].text == "\n".join(NARRATION)


@pytest.mark.unit
def test_extract_reads_plain_strings():
    data, _ = assemble([("message",), ("plain", "アリスとウサギ"), RETURN])
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ["アリスとウサギ"]


@pytest.mark.unit
def test_extract_skips_code_strings():
    """ASCII PUSH_STRs and non-ASCII ones outside the name pattern aren't text."""
    data, _ = assemble(
        [
            ("message",),
            ("push_str", "alice001.ogg"),
            ("push_str", "おちゃかい"),
            ("push", 7),
            SYSCALL,
            ("crypt", ALICE_LINE),
            RETURN,
        ]
    )
    _, lines, _ = extract(data)
    assert [(t.text, t.speaker) for t in lines] == [(ALICE_LINE, None)]


@pytest.mark.unit
def test_extract_skips_ignored():
    import re

    _, lines, _ = extract(sample(), ignore_patterns=[re.compile("^まって")])
    assert ALICE_LINE2 not in [t.text for t in lines]


@pytest.mark.unit
def test_speaker_resets_at_each_message():
    data, _ = assemble(
        [("message",), ("name", ALICE), ("crypt", ALICE_LINE), *WAIT, ("message",), ("crypt", NARRATION[0]), *WAIT]
    )
    _, lines, _ = extract(data)
    assert [(t.text, t.speaker) for t in lines] == [(ALICE, None), (ALICE_LINE, ALICE), (NARRATION[0], None)]


@pytest.mark.unit
def test_extract_does_not_join_across_jump_target():
    data, _ = assemble(
        [
            ("message",),
            ("jz", "second"),
            ("crypt", NARRATION[0]),
            ("newline",),
            ("label", "second"),
            ("crypt", NARRATION[1]),
            RETURN,
        ]
    )
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == NARRATION


# --------------------------------------------------------------------------- #
# Insertion


@pytest.mark.unit
@pytest.mark.parametrize("unwrap", [True, False])
def test_identity_roundtrip(unwrap):
    data = sample()
    assert roundtrip(data, extract_kwargs={"unwrap": unwrap}, width=SilkyMes.default_width) == data


@pytest.mark.unit
def test_missing_translations_keep_original():
    data = sample()
    intermediate, _, metadata = extract(data)
    assert insert(intermediate, {}, **metadata) == data


@pytest.mark.unit
def test_insert_english_as_plain_wrapped_lines():
    data = sample()
    out = roundtrip(data, edit=lambda t: ENGLISH if t == ALICE_LINE else t, width=40)
    texts = strings(out)
    english = [t for op, t in (s for s in texts if s != "\n") if op == OP_STR_PLAIN]
    assert " ".join(english) == ENGLISH
    assert len(english) > 1
    assert all(len(line) <= 40 for line in english)
    assert code_shape(out) == code_shape(data)


@pytest.mark.unit
def test_english_lines_are_joined_by_newline_instructions():
    data, _ = assemble([("message",), ("crypt", ALICE_LINE), *WAIT])
    out = roundtrip(data, edit=lambda _: "Have you seen the White Rabbit?", width=12)
    assert strings(out) == [
        (OP_STR_PLAIN, "Have you"),
        "\n",
        (OP_STR_PLAIN, "seen the"),
        "\n",
        (OP_STR_PLAIN, "White"),
        "\n",
        (OP_STR_PLAIN, "Rabbit?"),
    ]


@pytest.mark.unit
def test_full_width_translation_stays_compressed():
    data, _ = assemble([("message",), ("crypt", ALICE_LINE), *WAIT])
    out = roundtrip(data, edit=lambda _: "ウサギはどこ？", width=60)
    assert strings(out) == [(OP_STR_CRYPT, "ウサギはどこ？")]


@pytest.mark.unit
def test_hard_line_breaks():
    data, _ = assemble([("message",), ("crypt", ALICE_LINE), *WAIT])
    out = roundtrip(data, edit=lambda _: "Curiouser\\nand curiouser!", width=60)
    assert strings(out) == [(OP_STR_PLAIN, "Curiouser"), "\n", (OP_STR_PLAIN, "and curiouser!")]


@pytest.mark.unit
def test_trailing_line_break_is_kept():
    data = sample()
    out = roundtrip(data, edit=lambda t: "Oh dear! I shall be late!" if t == "".join(RABBIT_LINE) else t, width=60)
    texts = strings(out)
    i = texts.index((OP_STR_PLAIN, "Oh dear! I shall be late!"))
    assert texts[i + 1] == "\n"


@pytest.mark.unit
def test_offsets_and_header_follow_longer_text():
    data = sample()
    out = roundtrip(data, edit=lambda _: ENGLISH, width=50)
    assert len(out) > len(data)
    assert code_shape(out) == code_shape(data)  # parse_script also validates both header tables


@pytest.mark.unit
def test_offsets_follow_shorter_text():
    data = sample()
    out = roundtrip(data, edit=lambda _: "Ah", width=50)
    assert len(out) < len(data)
    assert code_shape(out) == code_shape(data)


@pytest.mark.unit
def test_choices_are_not_wrapped():
    data = sample()
    out = roundtrip(data, edit=lambda t: ENGLISH if t == OPTIONS[0] else t, width=20)
    assert (OP_STR_PLAIN, ENGLISH) in strings(out)


@pytest.mark.unit
def test_name_translation_applies_everywhere():
    data = sample()
    out = roundtrip(data, edit=lambda t: "Alice" if t == ALICE else t)
    pushes = [i.arg for i in parse_script(out).instructions if i.op == OP_PUSH_STR]
    assert pushes.count(b"Alice") == 2
    assert ALICE.encode("cp932") not in pushes
    # the bodies are untouched
    assert strings(out) == strings(data)


@pytest.mark.unit
def test_cp932_fixup_is_preserved():
    # ∵ (81E6) is the form Python encodes; the NEC duplicate 879A decodes to the same character
    nec = bytes([OP_STR_CRYPT]) + b"\x87\x9a" + encrypt("のひみつ".encode("cp932")) + b"\0"
    data, _ = assemble([("message",), nec, *WAIT])
    out = roundtrip(data, edit=lambda t: t + "ね", width=60)
    (ins,) = [i for i in parse_script(out).instructions if i.op == OP_STR_CRYPT]
    assert ins.arg.startswith(b"\x87\x9a")


@pytest.mark.unit
def test_rejects_foreign_intermediate():
    with pytest.raises(ValueError, match="Silky"):
        SilkyMes.insert_lines(BytesIO(b"TOTOAI61whatever"), {})


@pytest.mark.integration
def test_get_paths(tmp_path):
    (tmp_path / "script").mkdir()
    for name in ["script/OT_ALICE01.MES", "script/rabbit.mes", "script/LIBLARY.LIB", "notes.txt"]:
        (tmp_path / name).write_bytes(b"")
    assert [p.relative_to(tmp_path).as_posix() for p in SilkyMes.get_paths(tmp_path)] == [
        "script/OT_ALICE01.MES",
        "script/rabbit.mes",
    ]


@pytest.mark.unit
def test_warns_when_message_overflows_the_window(caplog):
    """The text box holds four lines; a translation that wraps to more is reported."""
    data, _ = assemble([("message",), ("crypt", ALICE_LINE), *WAIT])
    intermediate, lines, metadata = extract(data)
    (line,) = lines
    with caplog.at_level("WARNING", logger="toto.filetypes.SilkyMes"):
        insert(intermediate, {line.key: line._replace(text="Down\\ndown\\ndown\\nand down")}, **metadata, width=56)
    assert not caplog.records
    with caplog.at_level("WARNING", logger="toto.filetypes.SilkyMes"):
        insert(
            intermediate,
            {line.key: line._replace(text="Down\\ndown\\ndown\\nand down\\nshe fell")},
            **metadata,
            width=56,
        )
    (record,) = caplog.records
    assert line.key in record.getMessage()
    assert "5 lines" in record.getMessage()
