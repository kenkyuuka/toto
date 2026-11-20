"""Tests for System-NNN ``.xtx`` lists."""

import re
from io import BytesIO

import pytest

from toto.filetypes.SystemNnnXtx import SystemNnnXtx, tokens

NAMECOL = "アリス 255 255 255\r\n白ウサギ 255 255 255\r\n帽子屋 255 255 255\r\n".encode("cp932")
SELIST = "muon,無音\r\nse001,扉の音\r\nse002,時計の音".encode("cp932")  # no final newline
SCENEVOICE = "[シーン登録]\r\n@sc001,aa030029\r\n#コメント\r\n!無効".encode("cp932")
CUTIN = "キャラ名,fileTag,X,Y\r\nアリス,alice_cutin,300,100\r\n".encode("cp932")


def extract(data, **kwargs):
    return SystemNnnXtx.extract_lines(BytesIO(data), **kwargs)


def roundtrip(data, edit=None):
    intermediate, lines, metadata = extract(data)
    trans = {t.key: t for t in lines}
    if edit:
        trans = {k: t._replace(text=edit(t.text)) for k, t in trans.items()}
    return SystemNnnXtx.insert_lines(intermediate, trans, **metadata).read()


def token_texts(data):
    return [data[s:e].decode("cp932") for s, e in tokens(data)]


@pytest.mark.unit
def test_tokenizer_follows_the_engine():
    """One comma, then any spaces, tabs and line breaks separate tokens; ',,' gives an empty token."""
    assert token_texts(b"a,b c\t d\r\ne,,f , g") == ["a", "b", "c", "d", "e", "", "f", "", "g"]


@pytest.mark.unit
@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (NAMECOL, ["アリス", "白ウサギ", "帽子屋"]),
        (SELIST, ["無音", "扉の音", "時計の音"]),
        (SCENEVOICE, []),
        (CUTIN, ["キャラ名", "アリス"]),
    ],
    ids=["namecol", "selist", "markers", "cutin"],
)
def test_extracts_japanese_tokens_only(data, expected):
    _, lines, _ = extract(data)
    assert [t.text for t in lines] == expected


@pytest.mark.unit
def test_ignore_patterns():
    _, lines, _ = extract(SELIST, ignore_patterns=[re.compile("音$")])
    assert lines == []


@pytest.mark.unit
@pytest.mark.parametrize("data", [NAMECOL, SELIST, SCENEVOICE, CUTIN], ids=["namecol", "selist", "markers", "cutin"])
def test_identity_roundtrip(data):
    assert roundtrip(data) == data
    assert roundtrip(data, edit=lambda t: f" {t} ") == data


@pytest.mark.unit
def test_translation_keeps_the_token_structure():
    names = {"アリス": "Alice", "白ウサギ": "White Rabbit", "帽子屋": "Hatter, Mad"}
    out = roundtrip(NAMECOL, edit=names.get)
    assert out.decode("cp932") == ("Alice 255 255 255\r\nWhite　Rabbit 255 255 255\r\nHatter，　Mad 255 255 255\r\n")
    assert len(list(tokens(out))) == len(list(tokens(NAMECOL)))


@pytest.mark.unit
def test_empty_translation_keeps_the_original():
    assert roundtrip(SELIST, edit=lambda _: "") == SELIST


@pytest.mark.unit
def test_token_too_long_for_the_engine():
    with pytest.raises(ValueError, match="128"):
        roundtrip(SELIST, edit=lambda _: "x" * 128)
    assert b"x" * 127 in roundtrip(SELIST, edit=lambda _: "x" * 127)
