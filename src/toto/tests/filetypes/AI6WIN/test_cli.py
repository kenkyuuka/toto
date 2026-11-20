"""CLI extract/insert tests for AI6WIN ``.mes`` scripts."""

import pytest
from click.testing import CliRunner

from toto.filetypes.Ai6WinMes import OP_ESCAPE, OP_STR, parse_script
from toto.toto import cli

from .test_ai6win_mes import ALICE_LINE, ENGLISH, NARRATION, WAIT, assemble


def _run(runner, *args):
    result = runner.invoke(cli, list(args))
    assert result.exit_code == 0, result.output
    return result


@pytest.mark.integration
def test_extract_translate_insert(tmp_path):
    """Translations come back with the name tag and wrapped at the default 60 characters."""
    src, workpath, transdir, outpath = (tmp_path / d for d in ("src", "working", "trans", "output"))
    (src / "stage").mkdir(parents=True)
    data, _ = assemble([("message",), ("str", NARRATION), *WAIT, ("message",), ("str", ALICE_LINE), *WAIT])
    (src / "stage" / "stage00.mes").write_bytes(data)

    common = ["--workpath", str(workpath), "--filetype", "ai6win"]
    runner = CliRunner()
    _run(runner, "extract", str(src), "--outpath", str(transdir), *common)

    (trans_file,) = transdir.rglob("*.trans*.txt")
    assert trans_file.read_text(encoding="utf_8").splitlines() == [NARRATION, "アリス", "白ウサギを見なかった？"]
    trans_file.write_text(f"Alice fell down the rabbit hole.\nAlice\n{ENGLISH}\n", encoding="utf_8")

    _run(runner, "insert", str(transdir), "--outpath", str(outpath), *common)

    script = parse_script((outpath / "stage" / "stage00.mes").read_bytes())
    runs, current = [], None
    for ins in script.instructions:
        if ins.op == OP_STR:
            if current is None:
                current = []
                runs.append(current)
            current.append(ins.arg.decode("cp932"))
        elif ins.op != OP_ESCAPE:
            current = None
    assert runs[0] == ["Alice fell down the rabbit hole."]
    assert runs[1][0].startswith("〈Alice〉：Alice was beginning")
    assert len(runs[1]) > 1
    assert all(len(line) <= 60 for line in runs[1])
