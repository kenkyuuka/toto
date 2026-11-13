"""CLI extract/insert tests for DxLib scripts."""

import struct

import pytest
from click.testing import CliRunner

from toto.toto import cli

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
