"""Tests for the skip_identical flag in insert."""

from pathlib import Path

from click.testing import CliRunner

from toto.toto import cli

TEST_DATA = Path(__file__).parent / "filetypes" / "KiriKiriScriptV2" / "samples"


def test_skip_identical_no_translatable_text(tmp_path):
    """A file with no translatable text should still be output (copied through)
    so the archive is complete when a patch is built."""
    workpath = tmp_path / "working"
    transdir = tmp_path / "trans"
    outpath = tmp_path / "output"
    for d in (workpath, transdir, outpath):
        d.mkdir()

    runner = CliRunner()

    # Extract phase: creates intermediate file and shelf in workpath,
    # and translation files in transdir.
    result = runner.invoke(
        cli,
        [
            'extract',
            str(TEST_DATA / 'no_text.ks'),
            '--outpath',
            str(transdir),
            '--workpath',
            str(workpath),
            '--filetype',
            'kirikiri',
            '--codec',
            'ascii',
        ],
    )
    assert result.exit_code == 0, result.output

    # Sanity: no trans file should have been created (no translatable text).
    trans_files = list(transdir.glob('*.trans*.txt'))
    assert trans_files == [], f"Unexpected trans files: {trans_files}"

    # Insert phase with skip_identical (the default).
    result = runner.invoke(
        cli,
        [
            'insert',
            str(transdir),
            '--outpath',
            str(outpath),
            '--workpath',
            str(workpath),
            '--filetype',
            'kirikiri',
            '--codec',
            'ascii',
        ],
    )
    assert result.exit_code == 0, result.output

    # The output directory should contain the file — files with no translatable
    # text are always copied through so the archive is complete.
    output_files = list(outpath.rglob('*'))
    assert any(f.is_file() for f in output_files), "File with no translatable text should still be output"


def test_skip_identical_identity_translation(tmp_path):
    """A file with translatable text, where the translation is identical to
    the original, should be skipped when skip_identical is enabled."""
    workpath = tmp_path / "working"
    transdir = tmp_path / "trans"
    outpath = tmp_path / "output"
    for d in (workpath, transdir, outpath):
        d.mkdir()

    runner = CliRunner()

    # Extract phase.
    result = runner.invoke(
        cli,
        [
            'extract',
            str(TEST_DATA / 'has_text.ks'),
            '--outpath',
            str(transdir),
            '--workpath',
            str(workpath),
            '--filetype',
            'kirikiri',
            '--codec',
            'ascii',
        ],
    )
    assert result.exit_code == 0, result.output

    # Sanity: trans file should exist (file has translatable text).
    trans_files = list(transdir.glob('*.trans*.txt'))
    assert len(trans_files) > 0, "Expected at least one trans file"

    # Do NOT modify the trans files — identity translation.

    # Insert phase with --skip-identical explicitly enabled.
    result = runner.invoke(
        cli,
        [
            'insert',
            str(transdir),
            '--outpath',
            str(outpath),
            '--workpath',
            str(workpath),
            '--filetype',
            'kirikiri',
            '--codec',
            'ascii',
            '--skip-identical',
        ],
    )
    assert result.exit_code == 0, result.output

    # The output directory should have NO files, because all translations
    # are identical to the originals.
    output_files = list(outpath.glob('*'))
    assert output_files == [], f"skip_identical should have prevented output, but got: {output_files}"
