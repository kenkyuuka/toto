"""CLI extract/insert tests for BLACKRAINBOW ``.spt`` scripts."""

import struct

import pytest
from click.testing import CliRunner

from toto.filetypes.BlackRainbowSpt import mask, parse_records
from toto.toto import cli

from .test_blackrainbow_spt import NEW_KEY, OLD_KEY, build, message, speech

ENGLISH = (
    "Alice was beginning to get very tired of sitting by her sister on the bank, "
    "and of having nothing to do: once or twice she had peeped into the book her sister was reading."
)


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
@pytest.mark.parametrize(("key", "key_args"), [(NEW_KEY, []), (bytes.fromhex("5aa55aa5"), ["--key", "5aa55aa5"])])
def test_extract_translate_insert(tmp_path, key, key_args):
    """A translated script comes back masked with the right key and paged at the default width."""
    src, workpath, transdir, outpath = _dirs(tmp_path)
    (src / "rabbit.spt").write_bytes(
        build([message(0, "アリスはうさぎの穴に落ちていった。", key=key), speech(1, "アリス", ["「まあ！」"], key=key)])
    )
    common = ["--workpath", str(workpath), "--filetype", "blackrainbow"]
    runner = CliRunner()
    _run(runner, "extract", str(src), "--outpath", str(transdir), *common, *key_args)

    (trans_file,) = transdir.glob("*.trans*.txt")
    assert trans_file.read_text(encoding="utf_8").splitlines() == [
        "アリスはうさぎの穴に落ちていった。",
        "アリス",
        "「まあ！」",
    ]
    trans_file.write_text(f'{ENGLISH}\nAlice\n"Oh my!"\n', encoding="utf_8")

    _run(runner, "insert", str(transdir), "--outpath", str(outpath), *common)

    out = (outpath / "rabbit.spt").read_bytes()
    first, cont, second = (pl for _, pl in parse_records(out))
    pages = []
    for pl in (first, cont):
        nl = struct.unpack_from("<I", pl, 12)[0]
        pages.append(mask(pl[20 + nl :], key).decode("cp932"))
    # Wrapped at the default 55 columns; text over two lines continues on a second page.
    lines = [line for p in pages for line in p.split("\r\n")]
    assert all(len(p.split("\r\n")) <= 2 for p in pages) and all(len(line) <= 55 for line in lines)
    assert " ".join(lines) == ENGLISH
    assert struct.unpack_from("<I", cont, 0)[0] == 0
    nl = struct.unpack_from("<I", second, 12)[0]
    assert mask(second[20 : 20 + nl], key).decode("cp932") == "アリス"
    assert mask(second[20 + nl :], key).decode("cp932") == '【Alice】\r\n"Oh my!"'


@pytest.mark.integration
def test_bad_key_is_a_usage_error(tmp_path):
    src, workpath, transdir, _ = _dirs(tmp_path)
    (src / "rabbit.spt").write_bytes(build([message(0, "アリス", key=OLD_KEY)]))
    result = CliRunner().invoke(
        cli,
        [
            "extract",
            str(src),
            "--outpath",
            str(transdir),
            "--workpath",
            str(workpath),
            "--filetype",
            "blackrainbow",
            "--key",
            "xyz",
        ],
    )
    assert result.exit_code == 2
    assert "--key" in result.output
