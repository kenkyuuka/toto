"""CLI extract/insert tests for DxLib scripts."""

import struct

import pytest
from click.testing import CliRunner

from toto.toto import cli

from .test_unwrap import messages

ALICE = "アリスは不思議の国にいた"  # Alice was in Wonderland


def _make_script(strings):
    body = b"\x01\x00\x02\x00"
    text = b"".join(s.encode("cp932") + b"\0" for s in strings)
    header = struct.pack("<IIII", len(body) + len(text), len(body), 0, 0)
    return header + body + text


@pytest.mark.integration
def test_translated_script_has_updated_body_size(tmp_path):
    """A length-changing translation through the CLI updates the header's body size."""
    src, workpath, transdir, outpath = (tmp_path / d for d in ("src", "working", "trans", "output"))
    for d in (src, workpath, transdir, outpath):
        d.mkdir()
    (src / "RABBIT").write_bytes(_make_script([ALICE]))

    runner = CliRunner()
    args = ["--workpath", str(workpath), "--filetype", "dxlib"]
    result = runner.invoke(cli, ["extract", str(src / "RABBIT"), "--outpath", str(transdir), *args])
    assert result.exit_code == 0, result.output

    (trans_file,) = transdir.glob("*.trans*.txt")
    trans_file.write_text("Alice fell down the rabbit hole, down and down.\n", encoding="utf_8")

    result = runner.invoke(cli, ["insert", str(transdir), "--outpath", str(outpath), *args])
    assert result.exit_code == 0, result.output

    out = (outpath / "RABBIT").read_bytes()
    assert b"Alice fell down the rabbit hole" in out
    assert struct.unpack_from("<I", out, 0)[0] == len(out) - 0x10


def _make_message_script(line):
    """A one-message script: text, end of line, click."""
    code = struct.pack("<HB", 1, 3) + b"\x01\x00\x00" + struct.pack("<HB", 2, 1) + b"\x00"
    code += struct.pack("<HB", 2, 1) + b"\x04" + b"\x00"
    pool = line.encode("cp932") + b"\0"
    header = struct.pack("<IIHHI", len(code) + len(pool), len(code), 1, 0, 2)
    return header + code + pool


@pytest.mark.integration
@pytest.mark.parametrize(("width_args", "width"), [([], 45), (["--width", "20"], 20)], ids=["default", "explicit"])
def test_unwrapped_message_rewrapped(tmp_path, width_args, width):
    """Extraction joins a message by default; insert rewraps it to the DxLib default width or --width."""
    src, workpath, transdir, outpath = (tmp_path / d for d in ("src", "working", "trans", "output"))
    for d in (src, workpath, transdir, outpath):
        d.mkdir()
    (src / "RABBIT").write_bytes(_make_message_script(ALICE))

    runner = CliRunner()
    args = ["--workpath", str(workpath), "--filetype", "dxlib"]
    result = runner.invoke(cli, ["extract", str(src / "RABBIT"), "--outpath", str(transdir), *args])
    assert result.exit_code == 0, result.output

    english = "Alice was beginning to get very tired of sitting by her sister on the bank."
    (trans_file,) = transdir.glob("*.trans*.txt")
    trans_file.write_text(english + "\n", encoding="utf_8")

    result = runner.invoke(cli, ["insert", str(transdir), "--outpath", str(outpath), *width_args, *args])
    assert result.exit_code == 0, result.output

    # At width 20 the text needs more than three lines, so it spans two pages.
    lines = [line for page in messages((outpath / "RABBIT").read_bytes()) for line in page]
    assert " ".join(lines) == english
    assert all(len(s) <= width for s in lines)
    assert len(lines) > 1


@pytest.mark.integration
def test_no_unwrap_extracts_lines_separately(tmp_path):
    src, workpath, transdir = (tmp_path / d for d in ("src", "working", "trans"))
    for d in (src, workpath, transdir):
        d.mkdir()
    lines = ["「白うさぎさん、どこへ", "\u3000行くの？」"]
    code = b"".join(
        struct.pack("<HB", 1, 3) + b"\x01" + struct.pack("<H", i) + struct.pack("<HB", 1, 1) + b"\x00" for i in range(2)
    )
    code += struct.pack("<HB", 2, 1) + b"\x04" + b"\x00"
    pool = b"".join(s.encode("cp932") + b"\0" for s in lines)
    (src / "RABBIT").write_bytes(struct.pack("<IIHHI", len(code) + len(pool), len(code), 2, 0, 2) + code + pool)

    args = ["--outpath", str(transdir), "--workpath", str(workpath), "--filetype", "dxlib", "--no-unwrap"]
    result = CliRunner().invoke(cli, ["extract", str(src / "RABBIT"), *args])
    assert result.exit_code == 0, result.output
    (trans_file,) = transdir.glob("*.trans*.txt")
    assert trans_file.read_text(encoding="utf_8").splitlines() == lines
