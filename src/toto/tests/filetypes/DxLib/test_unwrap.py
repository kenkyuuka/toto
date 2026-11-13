"""Tests for unwrapping and rewrapping DxLib (lunetb) message text.

Scripts are assembled from synthetic records so the tests can check that code
offsets (labels, block jumps, menu tables) survive records being added or removed.
"""

import struct
from io import BytesIO

import pytest

from toto.filetypes.DxLib import DxLib
from toto.filetypes.TranslatableFile import TranslatableFile

# Synthetic Alice in Wonderland text.
ALICE = "【アリス】"
LINE1 = "「白うさぎさん、そんなに急いで"
LINE2 = "　どこへ行くの？」"
JOINED = "「白うさぎさん、そんなに急いでどこへ行くの？」"
NARR = "アリスはうさぎの穴に落ちていった。"
SHARED = "「……」"
CAPTION = "不思議の国"

EOL = b"\x00"
CLICK = b"\x04"


def text(s):
    return ("text", s)


class Ref:
    """A u16 code offset to a named mark, resolved after layout."""

    def __init__(self, name):
        self.name = name


def assemble(items, strings, labels=(), nlines=99):
    """Build a script from records.

    *items* is a list of ``("mark", name)`` or ``(op, *operands)`` where operands are
    ``bytes``, ``Ref`` (u16 code offset of a mark) or ``str`` (u16 index into *strings*).
    *labels* lists mark names that become entries of the label table.
    """

    def payload(item):
        out = []
        for part in item:
            if isinstance(part, int):
                out.append(bytes([part]))
            elif isinstance(part, bytes):
                out.append(part)
            elif isinstance(part, Ref):
                out.append(part)
            else:
                out.append(struct.pack("<H", strings.index(part)))
        return out

    marks, layout, pos = {}, [], 0
    for line, item in enumerate(items):
        if item[0] == "mark":
            marks[item[1]] = pos
            continue
        parts = payload((0x01, item[1]) if item[0] == "text" else item)
        size = sum(2 if isinstance(p, Ref) else len(p) for p in parts)
        layout.append((line, parts, size))
        pos += 3 + size
    marks.setdefault("end", pos)
    code = b""
    for line, parts, size in layout:
        body = b"".join(struct.pack("<H", marks[p.name]) if isinstance(p, Ref) else p for p in parts)
        code += struct.pack("<HB", line, size) + body
    code += b"\x00"
    pool = struct.pack(f"<{len(labels)}H", *(marks[n] for n in labels))
    pool += b"".join(s.encode("cp932") + b"\0" for s in strings)
    pool += b"".join(n.encode("ascii") + b"\0" for n in labels)
    body = code + pool
    header = struct.pack("<IIHHI", len(body), len(code), len(strings), len(labels), nlines)
    return header + body


def parse(data):
    """Minimal independent parser: (records [(offset, line, payload)], strings, label offsets)."""
    size, code_len, ns, nt, _ = struct.unpack_from("<IIHHI", data)
    assert size == len(data) - 0x10
    code = data[0x10 : 0x10 + code_len]
    pool = data[0x10 + code_len :]
    offs = list(struct.unpack_from(f"<{nt}H", pool))
    parts = pool[2 * nt :].split(b"\0")
    assert parts[-1] == b"" and len(parts) - 1 == ns + nt
    strings = [p.decode("cp932") for p in parts[:ns]]
    recs, i = [], 0
    while i + 3 <= len(code):
        line, n = struct.unpack_from("<HB", code, i)
        recs.append((i, line, code[i + 3 : i + 3 + n]))
        i += 3 + n
    assert code[i:] == b"\x00"
    return recs, strings, offs


def messages(data):
    """The text of each message in *data*: a list of lists of lines."""
    recs, strings, _ = parse(data)
    out, cur = [], []
    for _, _, p in recs:
        if p[:1] == b"\x01":
            cur.append(strings[struct.unpack_from("<H", p, 1)[0]])
        elif p[:1] != b"\x00" and cur:
            out.append(cur)
            cur = []
    return out


def ops(data):
    return [p[0] for _, _, p in parse(data)[0]]


def roundtrip(data, translate=None, width=45, **kwargs):
    intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(data), unwrap=True)
    trans = {t.key: (t._replace(text=translate[t.text]) if translate and t.text in translate else t) for t in textlines}
    return DxLib.insert_lines(intermediate, trans, width=width, **metadata, **kwargs).read()


def dialogue_script():
    """Named dialogue, narration, an if-block, a switch, a menu and a label."""
    strings = [ALICE, "VOICE01", LINE1, LINE2, NARR, SHARED, CAPTION]
    items = [
        ("mark", "start"),
        (0x07, 0),
        (0x0D, ALICE),
        (0x14, "VOICE01"),
        text(LINE1),
        (EOL[0],),
        text(LINE2),
        (EOL[0],),
        (CLICK[0],),
        ("mark", "narr"),
        text(NARR),
        (EOL[0],),
        (CLICK[0],),
        (0x1D, b"\x01\x00\xf5\xeb\x01\xff"),
        (0x08, 1, Ref("endif")),
        ("mark", "body"),
        text(SHARED),
        (EOL[0],),
        (CLICK[0],),
        (0x09, 1, Ref("body")),
        ("mark", "endif"),
        (0x2A, 1, Ref("opt0"), Ref("end")),
        ("mark", "opt0"),
        (0x0A, b"\xff\xff", b"\x00\x00\xf5\x00\x00\xff"),
        (0x21, CAPTION, 1, Ref("opt1"), Ref("end")),
        ("mark", "opt1"),
        (0x0A, CAPTION, b"\x00\x00\xf5\x00\x00\xff"),
        (0x1F,),
    ]
    return assemble(items, strings, labels=("start",)), items


class TestExtract:
    @pytest.mark.unit
    def test_message_lines_are_joined(self):
        data, _ = dialogue_script()
        _, textlines, _ = DxLib.extract_lines(BytesIO(data), unwrap=True)
        texts = [t.text for t in textlines]
        assert JOINED in texts
        assert LINE1 not in texts and LINE2 not in texts

    @pytest.mark.unit
    def test_names_and_options_extracted_once(self):
        data, _ = dialogue_script()
        _, textlines, _ = DxLib.extract_lines(BytesIO(data), unwrap=True)
        texts = [t.text for t in textlines]
        assert texts.count(ALICE) == 1
        assert texts.count(CAPTION) == 1
        assert "VOICE01" not in texts

    @pytest.mark.unit
    def test_script_order(self):
        """Speaker names come right before the first message that uses them."""
        data, _ = dialogue_script()
        _, textlines, _ = DxLib.extract_lines(BytesIO(data), unwrap=True)
        assert [t.text for t in textlines] == [ALICE, JOINED, NARR, SHARED, CAPTION]

    @pytest.mark.unit
    def test_label_table_not_glued_to_first_string(self):
        """The u16 label offsets before the strings must not leak into extracted text."""
        data, _ = dialogue_script()
        _, textlines, _ = DxLib.extract_lines(BytesIO(data), unwrap=True)
        assert textlines[0].text == ALICE

    @pytest.mark.unit
    def test_shared_line_string_gives_one_unit_per_message(self):
        strings = [SHARED, NARR]
        items = [text(SHARED), (0,), (4,), text(SHARED), (0,), text(NARR), (0,), (4,)]
        data = assemble(items, strings)
        _, textlines, _ = DxLib.extract_lines(BytesIO(data), unwrap=True)
        assert [t.text for t in textlines] == [SHARED, SHARED + NARR]

    @pytest.mark.unit
    def test_ascii_lines_joined_with_space(self):
        strings = ["Alice fell down", "the rabbit hole."]
        items = [text(strings[0]), (0,), text(strings[1]), (0,), (4,)]
        data = assemble(items, strings)
        _, textlines, _ = DxLib.extract_lines(BytesIO(data), unwrap=True)
        # Not Japanese, so not extracted by default -- but the join rule applies when it is.
        assert textlines == []
        line = DxLib._join_lines(strings)
        assert line == "Alice fell down the rabbit hole."

    @pytest.mark.unit
    def test_definition_table_not_joined(self):
        """Runs of ``name<TAB>value`` lines (the _DEF table) are extracted line by line."""
        strings = ["#ハートの女王\tQUEEN", "#チェシャ猫\tCAT"]
        data = assemble([text(strings[0]), (0,), text(strings[1]), (0,), (4,)], strings)
        intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(data), unwrap=True)
        assert textlines == []  # '#'-prefixed, so not translatable either way
        strings = ["女王\tQUEEN", "猫\tCAT"]
        data = assemble([text(strings[0]), (0,), text(strings[1]), (0,), (4,)], strings)
        intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(data), unwrap=True)
        assert [t.text for t in textlines] == strings
        trans = {t.key: t._replace(text=t.text.replace("猫", "Cat")) for t in textlines}
        out = DxLib.insert_lines(intermediate, trans, width=45, **metadata).read()
        assert messages(out) == [["女王\tQUEEN", "Cat\tCAT"]]

    @pytest.mark.unit
    def test_unparseable_file_falls_back(self):
        """A file without a valid record layout is extracted the old way."""
        body = b"\x01\x02"
        txt = "アリスは不思議の国にいた".encode("cp932") + b"\0"
        data = struct.pack("<IIII", len(body) + len(txt), len(body), 0, 0) + body + txt
        intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(data), unwrap=True)
        trans = {t.key: t for t in textlines}
        assert DxLib.insert_lines(intermediate, trans, width=45, **metadata).read() == data


class TestIdentity:
    @pytest.mark.unit
    def test_untranslated_roundtrip_is_byte_identical(self):
        data, _ = dialogue_script()
        assert roundtrip(data) == data

    @pytest.mark.unit
    def test_without_width(self):
        data, _ = dialogue_script()
        assert roundtrip(data, width=None) == data


class TestRewrap:
    LONG = (
        '"Mr. White Rabbit, where are you going in such a hurry? '
        'Wait for me, please, I only want to ask you one small question!"'
    )

    @pytest.mark.unit
    def test_grows_and_relocates(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {NARR: "Alice tumbled down and down the rabbit hole, wondering where it would end."})
        assert messages(out)[1] == ["Alice tumbled down and down the rabbit hole,", "wondering where it would end."]
        _check_structure(data, out)

    @pytest.mark.unit
    def test_shrinks_and_relocates(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {JOINED: '"Where are you going?"'})
        assert messages(out)[0] == ['"Where are you going?"']
        # Old strings stay in the pool (other records may share them); only the code shrinks.
        assert struct.unpack_from("<I", out, 4)[0] < struct.unpack_from("<I", data, 4)[0]
        _check_structure(data, out)

    @pytest.mark.unit
    def test_hanging_indent_kept_for_dialogue(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {JOINED: '"Mr. White Rabbit, where are you going in such a hurry?"'})
        assert messages(out)[0] == ['"Mr. White Rabbit, where are you going in', '\u3000such a hurry?"']

    @pytest.mark.unit
    def test_hanging_indent_inferred_for_single_line_dialogue(self):
        strings = ["「ねえ、待って！」"]
        data = assemble([text(strings[0]), (0,), (4,)], strings)
        out = roundtrip(data, {strings[0]: '"Hey, wait! Mr. White Rabbit, please wait for me!"'})
        assert messages(out)[0] == ['"Hey, wait! Mr. White Rabbit, please wait for', '　me!"']

    @pytest.mark.unit
    def test_no_indent_for_narration(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {NARR: "Alice tumbled down and down the rabbit hole, wondering where it would end."})
        assert not any(line.startswith("　") for line in messages(out)[1])

    @pytest.mark.unit
    def test_explicit_line_break(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {NARR: "Down, down, down.\\nWould the fall never come to an end?"})
        assert messages(out)[1] == ["Down, down, down.", "Would the fall never come to an end?"]

    @pytest.mark.unit
    def test_long_message_split_into_pages(self):
        data, _ = dialogue_script()
        long = " ".join([self.LONG] * 3)
        out = roundtrip(data, {JOINED: long})
        pages = messages(out)
        assert len(pages[0]) == 3 and len(pages[1]) == 3 and pages[2][0].startswith("　")
        recs, strings, _ = parse(out)
        names = [strings[struct.unpack_from("<H", p, 1)[0]] for _, _, p in recs if p[0] == 0x0D]
        voices = [p for _, _, p in recs if p[0] == 0x14]
        assert names == [ALICE] * 3
        assert len(voices) == 1
        _check_structure(data, out)

    @pytest.mark.unit
    def test_pages_without_speaker(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {NARR: " ".join([self.LONG] * 2)})
        recs, strings, _ = parse(out)
        names = [p for _, _, p in recs if p[0] == 0x0D]
        assert len(names) == 1  # only the first message's
        _check_structure(data, out)

    @pytest.mark.unit
    def test_no_trailing_eol_convention_kept(self):
        """Messages whose last line has no 00 record before the click keep that shape."""
        strings = [NARR]
        data = assemble([text(NARR), (4,)], strings)
        out = roundtrip(data, {NARR: "Alice tumbled down and down the rabbit hole, wondering where it would end."})
        assert ops(out) == [0x01, 0x00, 0x01, 0x04]

    @pytest.mark.unit
    def test_run_not_ending_in_click_is_not_paged(self):
        strings = [NARR, CAPTION]
        data = assemble(
            [text(NARR), (0,), (0x21, CAPTION, 0, Ref("end"))],
            strings,
        )
        out = roundtrip(data, {NARR: " ".join([self.LONG] * 2)})
        assert 0x04 not in ops(out)
        assert len(messages(out)[0]) > 3
        _check_structure(data, out)

    @pytest.mark.unit
    def test_shared_string_translated_per_message(self):
        """Two messages sharing a line string can be translated differently."""
        strings = [SHARED]
        data = assemble([text(SHARED), (0,), (4,), text(SHARED), (0,), (4,)], strings)
        intermediate, textlines, metadata = DxLib.extract_lines(BytesIO(data), unwrap=True)
        a, b = textlines
        trans = {a.key: a._replace(text="..."), b.key: b._replace(text="Hmm.")}
        out = DxLib.insert_lines(intermediate, trans, width=45, **metadata).read()
        assert messages(out) == [["..."], ["Hmm."]]

    @pytest.mark.unit
    def test_unchanged_message_untouched_among_changed(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {NARR: "Down, down, down."})
        assert messages(out)[0] == [LINE1, LINE2]

    @pytest.mark.unit
    def test_header_fields_updated(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {NARR: "Alice tumbled down and down the rabbit hole, wondering where it would end."})
        size, code_len, ns, nt, nlines = struct.unpack_from("<IIHHI", out)
        assert size == len(out) - 0x10
        assert nt == 1 and nlines == 99
        assert ns == len(parse(out)[1])

    @pytest.mark.unit
    def test_translated_names(self):
        data, _ = dialogue_script()
        out = roundtrip(data, {ALICE: "Alice", CAPTION: "Wonderland"})
        _, strings, _ = parse(out)
        assert "Alice" in strings and "Wonderland" in strings and ALICE not in strings


def _check_structure(before, after):
    """Every code offset in *after* points at the same kind of record as in *before*."""

    def targets(data):
        recs, _, labels = parse(data)
        by_off = {o: p for o, _, p in recs}
        end = recs[-1][0] + 3 + len(recs[-1][2])

        def kind(off):
            return "END" if off == end else by_off[off][0]

        found = [("label", kind(o)) for o in labels]
        for _, _, p in recs:
            if p[0] in (0x08, 0x09):
                found.append((p[0], kind(struct.unpack_from("<H", p, 2)[0])))
            elif p[0] in (0x21, 0x22, 0x2A):
                base = 2 if p[0] == 0x2A else 4
                n = p[base - 1]
                found += [(p[0], kind(v)) for v in struct.unpack_from(f"<{n + 1}H", p, base)]
        return found

    assert targets(after) == targets(before)


class TestDefaults:
    @pytest.mark.unit
    def test_dxlib_default_width(self):
        assert DxLib.default_width == 45

    @pytest.mark.unit
    def test_base_default_width(self):
        assert TranslatableFile.default_width == 60
