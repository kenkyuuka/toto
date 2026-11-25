"""CLI extract/insert tests for Silky Engine ``.mes`` scripts."""

import pytest
from click.testing import CliRunner

from toto.filetypes.SilkyMes import OP_NEWLINE, OP_PUSH_STR, OP_STR_PLAIN, parse_script
from toto.toto import cli

from .test_silky_mes import ALICE, ALICE_LINE, ENGLISH, NARRATION, WAIT, assemble


def _run(runner, *args):
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_extract_translate_insert(tmp_path):
    """Upper-case .MES files are found, and translations come back as STR_UNCRYPT lines wrapped
    at the default width, with the speaker name translated."""
    src, workpath, transdir, outpath = (tmp_path / d for d in ("src", "working", "trans", "output"))
    (src / "script").mkdir(parents=True)
    data, _ = assemble(
        [("message",), ("crypt", NARRATION[0]), *WAIT, ("message",), ("name", ALICE), ("crypt", ALICE_LINE), *WAIT]
    )
    (src / "script" / "OT_ALICE01.MES").write_bytes(data)

    common = ["--workpath", str(workpath), "--filetype", "silky"]
    runner = CliRunner()
    _run(runner, "extract", str(src), "--outpath", str(transdir), *common)

    (trans_file,) = transdir.rglob("*.trans*.txt")
    assert trans_file.read_text(encoding="utf_8").splitlines() == [NARRATION[0], ALICE, ALICE_LINE]
    trans_file.write_text(f"Alice fell down the rabbit hole.\nAlice\n{ENGLISH}\n", encoding="utf_8")

    _run(runner, "insert", str(transdir), "--outpath", str(outpath), *common)

    script = parse_script((outpath / "script" / "OT_ALICE01.MES").read_bytes())
    runs, current = [], None
    for ins in script.instructions:
        if ins.op == OP_STR_PLAIN:
            if current is None:
                current = []
                runs.append(current)
            current.append(ins.arg.decode("cp932"))
        elif ins.op != OP_NEWLINE:
            current = None
    assert runs[0] == ["Alice fell down the rabbit hole."]
    assert " ".join(runs[1]) == ENGLISH
    assert len(runs[1]) > 1
    assert all(len(line) <= 56 for line in runs[1])
    assert b"Alice" in [i.arg for i in script.instructions if i.op == OP_PUSH_STR]
