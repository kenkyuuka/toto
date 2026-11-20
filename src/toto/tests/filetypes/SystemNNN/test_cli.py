"""CLI extract/insert tests for System-NNN ``.spt`` scripts and ``.xtx`` lists."""

import pytest
from click.testing import CliRunner

from toto.toto import cli

from .test_systemnnn_spt import build, crypt, texts


def _dirs(tmp_path):
    dirs = [tmp_path / d for d in ("src", "working", "trans", "output")]
    for d in dirs:
        d.mkdir()
    return dirs


def _run(runner, *args):
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_spt_and_xtx(tmp_path):
    """A translated speaker name ends up identical in the script and in namecol.xtx."""
    src, workpath, transdir, outpath = _dirs(tmp_path)
    (src / "spt").mkdir()
    (src / "spt" / "alice.spt").write_bytes(build(["白ウサギ\r\n「遅刻だ！」"], ["★トップ"]))
    (src / "spt" / "namecol.xtx").write_bytes("白ウサギ 255 255 255\r\n".encode("cp932"))
    runner = CliRunner()

    for filetype, trans in (("systemnnn", "White Rabbit\n\"I'm late!\"\n"), ("systemnnn-xtx", "White Rabbit\n")):
        common = ["--workpath", str(workpath / filetype), "--filetype", filetype]
        _run(runner, "extract", str(src), "--outpath", str(transdir / filetype / "source"), *common)
        (trans_file,) = (transdir / filetype / "source").rglob("*.trans*.txt")
        target = transdir / filetype / "target" / trans_file.relative_to(transdir / filetype / "source")
        target.parent.mkdir(parents=True)
        target.write_text(trans, encoding="utf_8")
        _run(runner, "insert", str(transdir / filetype / "target"), "--outpath", str(outpath), *common)

    script = (outpath / "spt" / "alice.spt").read_bytes()
    assert crypt(script)[0x30:0x3A] == b"SPTHEADER0"
    (message,) = texts(script, "messages")
    name = message.split("\r\n")[0]
    namecol = (outpath / "spt" / "namecol.xtx").read_bytes().decode("cp932")
    assert namecol.split(" ")[0] == name == "White　Rabbit"
    assert message == "White　Rabbit\r\n\"I'm late!\""
