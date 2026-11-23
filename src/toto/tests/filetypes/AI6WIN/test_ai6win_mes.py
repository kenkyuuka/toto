"""Tests for AI6WIN ``.mes`` scripts.

Scripts are assembled from synthetic instructions (Alice in Wonderland text) with symbolic
labels, so tests can check that every jump, choice link and header entry still points at
the same instruction after text changes length.
"""

import re
import struct
from io import BytesIO

import pytest

from toto.filetypes.Ai6WinMes import (
    OP_CHOICE,
    OP_ESCAPE,
    OP_MESSAGE,
    OP_STR,
    Ai6WinMes,
    ScriptError,
    parse_script,
)
from toto.util import TextLine

# --------------------------------------------------------------------------- #
# Assembler
#
# An item is a tuple: ("label", name) marks a position; ("message",) emits MESSAGE with the
# next message number; ("jump"/"jz"/"choice", label) emit an offset instruction; ("str", text),
# ("push", n), ("push_str", text), ("escape", n) and ("op", byte) emit the rest. bytes are
# emitted verbatim.

OFFSET_OPS = {"jz": 0x14, "jump": 0x15, "choice": 0x1A}


def _encode(item, labels, msg_no):
    kind = item[0] if isinstance(item, tuple) else None
    if kind is None:
        return item
    if kind == "label":
        return b""
    if kind == "message":
        return bytes([OP_MESSAGE]) + struct.pack(">I", msg_no)
    if kind in OFFSET_OPS:
        return bytes([OFFSET_OPS[kind]]) + struct.pack(">I", labels.get(item[1], 0))
    if kind == "str":
        return bytes([OP_STR]) + item[1].encode("cp932") + b"\0"
    if kind == "push":
        return b"\x32" + struct.pack(">i", item[1])
    if kind == "push_str":
        return b"\x33" + item[1].encode("cp932") + b"\0"
    if kind == "escape":
        return bytes([OP_ESCAPE, item[1]])
    if kind == "op":
        return bytes([item[1]])
    raise ValueError(item)


def assemble(items):
    """Assemble *items* into a script; return ``(data, labels)`` with code-relative label offsets."""
    labels = {}
    for _ in range(2):  # the second pass sees every label
        code, msg_offsets, pos = [], [], 0
        for item in items:
            if isinstance(item, tuple) and item[0] == "label":
                labels[item[1]] = pos
            if isinstance(item, tuple) and item[0] == "message":
                msg_offsets.append(pos)
            raw = _encode(item, labels, len(msg_offsets) - 1)
            code.append(raw)
            pos += len(raw)
    header = struct.pack(f"<I{len(msg_offsets)}I", len(msg_offsets), *msg_offsets)
    return header + b"".join(code), labels


SYSCALL = ("op", 0x18)
YIELD = ("op", 0x00)
WAIT = [("push", 3), ("push", 23), SYSCALL]  # the click wait that follows each line

ALICE = "アリス"
RABBIT = "白ウサギ"
NARRATION = "アリスはうさぎの穴に落ちていった。"
ALICE_LINE = "〈アリス〉：白ウサギを見なかった？"
RABBIT_LINE = "〈白ウサギ〉：大変だ、遅刻しちゃう！"
ALICE_LINE2 = "〈アリス〉：待ってちょうだい！"
OPTIONS = ["ウサギを追う", "お茶会に行く"]

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
        ("str", NARRATION),
        *WAIT,
        ("message",),
        ("push_str", "alice001.ogg"),
        ("push", 5),
        SYSCALL,
        ("str", ALICE_LINE),
        *WAIT,
        ("push", 0),
        ("jz", "shared"),
        ("message",),
        ("str", RABBIT_LINE),
        *WAIT,
        ("jump", "after"),
        ("label", "shared"),
        ("message",),
        ("str", ALICE_LINE2),  # a jump lands on the text itself
        *WAIT,
        ("label", "after"),
        ("message",),
        ("push", 0),
        ("label", "opt0"),
        ("choice", "opt1"),
        ("str", OPTIONS[0]),
        YIELD,
        ("push", 1),
        ("label", "opt1"),
        ("choice", "end"),
        ("str", OPTIONS[1]),
        YIELD,
        ("label", "end"),
        ("push_str", "next.mes"),
        ("op", 0x17),
    ]


def sample():
    return assemble(sample_items())[0]


def extract(data, **kwargs):
    return Ai6WinMes.extract_lines(BytesIO(data), **kwargs)


def insert(intermediate, trans, **kwargs):
    kwargs.setdefault("width", None)
    return Ai6WinMes.insert_lines(intermediate, trans, **kwargs).read()


def roundtrip(data, edit=None, extract_kwargs=None, **kwargs):
    intermediate, lines, metadata = extract(data, **(extract_kwargs or {}))
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(t.text)) for k, t in trans.items()}
    return insert(intermediate, trans, **metadata, **kwargs)


def texts(data):
    """Every STR_PRIMARY string in the script, in order."""
    return [ins.arg.decode("cp932") for ins in parse_script(data).instructions if ins.op == OP_STR]


def check_consistent(data):
    """The invariants every valid script satisfies (parse_script checks them; this re-asserts)."""
    script = parse_script(data)
    starts = {ins.offset for ins in script.instructions}
    msgs = [ins for ins in script.instructions if ins.op == OP_MESSAGE]
    assert [m.offset for m in msgs] == list(script.message_offsets)
    assert [m.arg for m in msgs] == list(range(len(msgs)))
    for ins in script.instructions:
        if ins.op in (0x14, 0x15, 0x16, 0x1A):
            assert ins.arg in starts
    return script


def target_of(script, op, n=0):
    """The instruction that the *n*-th instruction with opcode *op* points at."""
    src = [ins for ins in script.instructions if ins.op == op][n]
    return next(i for i in script.instructions if i.offset == src.arg)


# --------------------------------------------------------------------------- #
# Parsing


@pytest.mark.unit
def test_parse_sample():
    script = check_consistent(sample())
    assert len(script.message_offsets) == 5
    assert texts(sample()) == [NARRATION, ALICE_LINE, RABBIT_LINE, ALICE_LINE2, *OPTIONS]


@pytest.mark.unit
def test_parse_script_without_messages():
    data, _ = assemble([("push", 1), ("push", 2), ("op", 0x34), ("op", 0x0C)])
    script = parse_script(data)
    assert script.message_offsets == ()
    assert script.code_base == 4


@pytest.mark.unit
@pytest.mark.parametrize(
    "data",
    [
        pytest.param(b"", id="empty"),
        pytest.param(struct.pack("<I", 5), id="header-past-eof"),
        pytest.param(struct.pack("<I", 0) + b"\x50", id="unknown-opcode"),
        pytest.param(struct.pack("<I", 0) + b"\x32\x00\x00", id="truncated-operand"),
        pytest.param(struct.pack("<I", 0) + b"\x0a\x82\xa0", id="unterminated-string"),
        pytest.param(struct.pack("<II", 1, 0) + b"\x00\x19\x00\x00\x00\x00", id="header-mismatch"),
        pytest.param(struct.pack("<II", 1, 0) + b"\x19\x00\x00\x00\x01", id="message-number"),
        pytest.param(struct.pack("<I", 0) + b"\x15\x00\x00\x00\x02\x00", id="jump-into-instruction"),
        pytest.param(struct.pack("<I", 0) + b"\x15\x00\x00\x01\x00", id="jump-past-eof"),
    ],
)
def test_parse_rejects_invalid(data):
    with pytest.raises(ScriptError):
        parse_script(data)


@pytest.mark.unit
def test_parse_picks_table_with_operand():
    """A script that only parses with byte operands for ``10``/``36`` is accepted."""
    data, _ = assemble([("push", 1), b"\x10\x50", ("message",), ("str", NARRATION)])
    assert texts(data) == [NARRATION]


@pytest.mark.unit
def test_parse_picks_table_without_operand():
    """``36`` (MUL) without an operand, as in the elf games."""
    data, _ = assemble([("push", 2), ("op", 0x36), ("jump", "x"), ("label", "x"), ("message",), ("str", NARRATION)])
    assert texts(data) == [NARRATION]


@pytest.mark.unit
def test_parse_rejects_ambiguous_tables():
    """When both opcode tables validate but disagree about where the text is, refuse to guess."""
    data = struct.pack("<I", 0) + b"\x36\x0a\x41\x00"  # MUL 0x0a; GE; YIELD  vs  MUL; STR "A"
    with pytest.raises(ScriptError, match="ambiguous"):
        parse_script(data)


# --------------------------------------------------------------------------- #
# Extraction


@pytest.mark.unit
def test_extract_lines():
    """Narration, each speaker once per file, bodies without the name tag, and choices."""
    _, lines, _ = extract(sample())
    assert [t.text for t in lines] == [
        NARRATION,
        ALICE,
        "白ウサギを見なかった？",
        RABBIT,
        "大変だ、遅刻しちゃう！",
        "待ってちょうだい！",
        *OPTIONS,
    ]
    assert [t.key for t in lines] == [f"<<<TRANS:{i}>>>" for i in range(len(lines))]


@pytest.mark.unit
def test_extract_skips_code_strings():
    """File names pushed with PUSH_STR are never text, even when they aren't ASCII."""
    data, _ = assemble([("push_str", "アリス.ogg"), ("message",), ("str", NARRATION)])
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == [NARRATION]


@pytest.mark.unit
def test_extract_skips_ascii_and_ignored():
    data, _ = assemble(
        [("message",), ("str", "Alice"), ("message",), ("str", "※テスト"), ("message",), ("str", ALICE_LINE)]
    )
    _, lines, _ = extract(data, ignore_patterns=[__import__("re").compile("^※")])
    assert [t.text for t in lines] == [ALICE, "白ウサギを見なかった？"]


@pytest.mark.unit
def test_extract_name_only_message():
    """A message that is just a name tag still exports the name."""
    data, _ = assemble([("message",), ("str", "〈アリス〉：")])
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == [ALICE]


@pytest.mark.unit
def test_extract_joins_consecutive_strings():
    """Strings back to back with no line break are shown run together, so they're one line."""
    data, _ = assemble([("message",), ("str", "ぽちゃん。"), ("str", "アリスは涙の池に落ちた。"), *WAIT])
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ["ぽちゃん。アリスは涙の池に落ちた。"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("unwrap", "expected"),
    [
        (True, ["Down, down, down. Would the fall never come to an end?"]),
        (False, ["Down, down, down.\nWould the fall never come to an end?"]),
    ],
)
def test_extract_line_breaks(unwrap, expected):
    """ESCAPE 0 line breaks are joined with a space (unwrap) or kept as newlines.

    The trailing full-width bracket keeps the run from being skipped as ASCII-only.
    """
    data, _ = assemble(
        [
            ("message",),
            ("str", "Down, down, down."),
            ("escape", 0),
            ("str", "Would the fall never come to an end?"),
            ("str", "（"),
        ]
    )
    _, lines, _ = extract(data, unwrap=unwrap)
    assert [t.text for t in lines] == [expected[0] + "（"]


@pytest.mark.unit
def test_extract_does_not_join_across_jump_target():
    """A string that a jump lands on starts its own run."""
    data, _ = assemble(
        [("push", 0), ("jz", "second"), ("message",), ("str", "ぽちゃん。"), ("label", "second"), ("str", "涙の池。")]
    )
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == ["ぽちゃん。", "涙の池。"]


@pytest.mark.unit
def test_extract_leaves_other_escapes():
    """Only ESCAPE 0 is a known line break; other escapes split runs and stay as they are."""
    data, _ = assemble([("message",), ("str", "ぽちゃん。"), ("escape", 1), ("str", "涙の池。")])
    out = roundtrip(data, edit=lambda _: "Sigh.")
    script = parse_script(out)
    assert [(i.op, i.arg) for i in script.instructions[1:]] == [
        (OP_STR, b"Sigh."),
        (OP_ESCAPE, 1),
        (OP_STR, b"Sigh."),
    ]


# --------------------------------------------------------------------------- #
# Insertion


@pytest.mark.unit
@pytest.mark.parametrize("unwrap", [True, False])
def test_identity_roundtrip(unwrap):
    assert roundtrip(sample(), extract_kwargs={"unwrap": unwrap}, width=60) == sample()


@pytest.mark.unit
def test_untranslated_lines_with_spaces_stay_identical():
    """OmegaT's leading space on an untranslated segment doesn't count as a change."""
    assert roundtrip(sample(), edit=lambda t: f" {t} ", width=10) == sample()


@pytest.mark.unit
def test_missing_translations_keep_original():
    intermediate, _, metadata = extract(sample())
    assert insert(intermediate, {}, **metadata) == sample()


@pytest.mark.unit
def test_insert_translation():
    intermediate, lines, metadata = extract(sample())
    english = [
        "Alice fell down the rabbit hole.",
        "Alice",
        "Have you seen the White Rabbit?",
        "White Rabbit",
        "Oh dear! Oh dear! I shall be too late!",
        "Wait for me!",
        "Follow the Rabbit",
        "Go to the tea party",
    ]
    trans = {t.key: t._replace(text=e) for t, e in zip(lines, english, strict=True)}
    out = insert(intermediate, trans, **metadata)
    assert texts(out) == [
        "Alice fell down the rabbit hole.",
        "〈Alice〉：Have you seen the White Rabbit?",
        "〈White Rabbit〉：Oh dear! Oh dear! I shall be too late!",
        "〈Alice〉：Wait for me!",
        "Follow the Rabbit",
        "Go to the tea party",
    ]


@pytest.mark.unit
def test_offsets_follow_longer_text():
    """Jumps, choice links and the header still point at the same instructions."""
    before = check_consistent(sample())
    after = check_consistent(roundtrip(sample(), edit=lambda _: ENGLISH[:70]))

    def strip(ins):
        return ins.op, None if ins.op in (OP_STR, 0x14, 0x15, OP_CHOICE) else ins.arg

    def shape(script):
        return [strip(ins) for ins in script.instructions]

    assert shape(after) == shape(before)
    assert len(after.message_offsets) == len(before.message_offsets)
    # jz lands on the shared message, jump on the instruction after it, the choice chain on its options
    for op, n in [(0x14, 0), (0x15, 0), (OP_CHOICE, 0), (OP_CHOICE, 1)]:
        old, new = target_of(before, op, n), target_of(after, op, n)
        assert strip(old) == strip(new)
        assert before.instructions.index(old) == after.instructions.index(new)


@pytest.mark.unit
def test_jump_to_text_lands_on_replacement():
    """A jump that targets a string lands on the first line of its wrapped replacement."""
    data, _ = assemble(
        [("push", 0), ("jz", "t"), ("message",), ("str", NARRATION), ("label", "t"), ("str", ALICE_LINE), *WAIT]
    )
    out = roundtrip(data, edit=lambda t: ENGLISH if t != ALICE else "Alice", width=30)
    script = check_consistent(out)
    landed = target_of(script, 0x14)
    assert landed.op == OP_STR
    assert landed.arg.startswith("〈Alice〉：".encode("cp932"))


@pytest.mark.unit
def test_wrapping_inserts_line_breaks():
    data, _ = assemble([("message",), ("str", ALICE_LINE), *WAIT])
    out = roundtrip(data, edit=lambda t: "Alice" if t == ALICE else ENGLISH, width=40)
    script = check_consistent(out)
    run = []
    for ins in script.instructions[1:]:
        if ins.op not in (OP_STR, OP_ESCAPE):
            break
        run.append(ins)
    assert [i.op for i in run[1::2]] == [OP_ESCAPE] * (len(run) // 2)
    assert all(i.arg == 0 for i in run[1::2])
    lines = [i.arg.decode("cp932") for i in run[::2]]
    assert lines[0].startswith("〈Alice〉：Alice was")
    assert all(len(line) <= 40 for line in lines)
    assert " ".join(lines).removeprefix("〈Alice〉：") == ENGLISH


@pytest.mark.unit
def test_hard_line_breaks():
    """A newline in a translation (written \\n in the translation file) is a forced break."""
    data, _ = assemble([("message",), ("str", NARRATION)])
    out = roundtrip(data, edit=lambda _: "Down, down, down.\\nWould the fall never come to an end?", width=None)
    assert [(i.op, i.arg) for i in parse_script(out).instructions[1:]] == [
        (OP_STR, b"Down, down, down."),
        (OP_ESCAPE, 0),
        (OP_STR, b"Would the fall never come to an end?"),
    ]


@pytest.mark.unit
def test_choices_are_not_wrapped():
    """A menu option stays one string, however long."""
    out = roundtrip(sample(), edit=lambda t: ENGLISH[:50] if t in OPTIONS else t, width=20)
    check_consistent(out)
    assert not [i for i in parse_script(out).instructions if i.op == OP_ESCAPE]
    assert texts(out)[-2:] == [ENGLISH[:50]] * 2


@pytest.mark.unit
def test_name_translation_applies_everywhere():
    intermediate, lines, metadata = extract(sample())
    trans = {t.key: t._replace(text="Alice") if t.text == ALICE else t for t in lines}
    out = insert(intermediate, trans, **metadata)
    assert [s for s in texts(out) if "Alice" in s] == [
        "〈Alice〉：白ウサギを見なかった？",
        "〈Alice〉：待ってちょうだい！",
    ]


@pytest.mark.unit
def test_name_only_change_keeps_body_lines():
    """Translating just the speaker leaves an untranslated body for the engine to wrap."""
    long_line = "〈アリス〉：" + "ねえ、白ウサギさん、そんなに急いでどこへ行くの？" * 3
    data, _ = assemble([("message",), ("str", long_line), *WAIT])
    out = roundtrip(data, edit=lambda t: "Alice" if t == ALICE else t, width=20)
    assert [(i.op, i.arg.decode("cp932")) for i in parse_script(out).instructions[1:2]] == [
        (OP_STR, long_line.replace("アリス", "Alice", 1))
    ]
    assert not [i for i in parse_script(out).instructions if i.op == OP_ESCAPE]


@pytest.mark.unit
def test_translated_name_stays_on_one_line():
    data, _ = assemble([("message",), ("str", ALICE_LINE)])
    out = roundtrip(data, edit=lambda t: "Alice\\nLiddell" if t == ALICE else t)
    assert texts(out) == ["〈Alice Liddell〉：白ウサギを見なかった？"]


@pytest.mark.unit
def test_cp932_fixup_is_preserved():
    """An IBM-extension character keeps its original byte sequence after insertion."""
    ibm = b"\xfa\x5c"  # 纊 as IBM extension; Python re-encodes it as NEC 0xED40
    data = struct.pack("<II", 1, 0) + b"\x19\x00\x00\x00\x00" + b"\x0a" + "アリス".encode("cp932") + ibm + b"\0"
    out = roundtrip(data, edit=lambda t: t + "！")
    assert ibm + "！".encode("cp932") + b"\0" in out


@pytest.mark.unit
def test_rejects_foreign_intermediate():
    with pytest.raises(ValueError, match="intermediate"):
        insert(BytesIO(sample()), {})


@pytest.mark.unit
def test_textline_eol_is_bytes():
    _, lines, _ = extract(sample())
    assert all(isinstance(t, TextLine) and t.eol == b"" for t in lines)


@pytest.mark.integration
def test_get_paths(tmp_path):
    (tmp_path / "stage").mkdir()
    for name in ("stage/stage00.mes", "main.mes", "liblary.lib", "notes.txt"):
        (tmp_path / name).write_bytes(b"")
    assert [p.relative_to(tmp_path).as_posix() for p in Ai6WinMes.get_paths(tmp_path)] == [
        "main.mes",
        "stage/stage00.mes",
    ]


# --------------------------------------------------------------------------- #
# Speakers
# --------------------------------------------------------------------------- #


@pytest.mark.unit
def test_bodies_get_speaker_from_their_name_tag():
    _, lines, _ = extract(sample())
    assert [(t.text, t.speaker) for t in lines] == [
        (NARRATION, None),
        (ALICE, None),
        ("白ウサギを見なかった？", ALICE),
        (RABBIT, None),
        ("大変だ、遅刻しちゃう！", RABBIT),
        ("待ってちょうだい！", ALICE),
        (OPTIONS[0], None),
        (OPTIONS[1], None),
    ]


@pytest.mark.unit
def test_speaker_without_padding_and_kept_when_name_ignored():
    data, _ = assemble([("message",), ("str", "〈 チェシャ猫 〉：にやにや"), *WAIT])
    _, lines, _ = extract(data, ignore_patterns=[re.compile("猫")])
    assert [(t.text, t.speaker) for t in lines] == [("にやにや", "チェシャ猫")]
