"""Tests for extract/insert subfolder path preservation."""

import pathlib
import shutil

from click.testing import CliRunner

from toto.tests.filetypes.Mgos.test_mgos import make_mgos_script
from toto.toto import cli


def _setup_orig(tmp_path):
    """Create orig/ with .o files in nested subdirectories.

    Structure:
      orig/foo/bar/scene1.o
      orig/foo/bar/scene2.o
      orig/qux/scene3.o
    """
    orig = tmp_path / 'orig'
    data = make_mgos_script()

    for rel in ('foo/bar/scene1.o', 'foo/bar/scene2.o', 'qux/scene3.o'):
        p = orig / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)

    return orig


class TestExtractPaths:
    def test_root_dir_preserves_structure(self, tmp_path):
        """Passing the root directory preserves full subfolder structure."""
        orig = _setup_orig(tmp_path)
        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                'extract',
                '--filetype=mgos',
                '--workpath',
                str(workpath),
                '--outpath',
                str(outpath),
                str(orig),
            ],
        )
        assert result.exit_code == 0

        work_files = {p.relative_to(workpath) for p in workpath.rglob('*.o')}
        assert work_files == {
            pathlib.Path('foo/bar/scene1.o'),
            pathlib.Path('foo/bar/scene2.o'),
            pathlib.Path('qux/scene3.o'),
        }

    def test_shell_glob_preserves_structure(self, tmp_path):
        """Passing orig/* (shell-expanded subdirectories) preserves structure.

        This simulates `toto extract --filetype mgos orig/*` where the
        shell expands orig/* to orig/foo orig/qux.
        """
        orig = _setup_orig(tmp_path)
        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'

        # Simulate shell expansion of orig/*
        subdirs = sorted(p for p in orig.iterdir() if p.is_dir())

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                'extract',
                '--filetype=mgos',
                '--workpath',
                str(workpath),
                '--outpath',
                str(outpath),
                *[str(d) for d in subdirs],
            ],
        )
        assert result.exit_code == 0

        work_files = {p.relative_to(workpath) for p in workpath.rglob('*.o')}
        assert work_files == {
            pathlib.Path('foo/bar/scene1.o'),
            pathlib.Path('foo/bar/scene2.o'),
            pathlib.Path('qux/scene3.o'),
        }

    def test_single_subdir(self, tmp_path):
        """Passing a single subdirectory uses it as root."""
        orig = _setup_orig(tmp_path)
        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                'extract',
                '--filetype=mgos',
                '--workpath',
                str(workpath),
                '--outpath',
                str(outpath),
                str(orig / 'foo'),
            ],
        )
        assert result.exit_code == 0

        work_files = {p.relative_to(workpath) for p in workpath.rglob('*.o')}
        # Single directory is the root, so paths are relative to orig/foo
        assert work_files == {
            pathlib.Path('bar/scene1.o'),
            pathlib.Path('bar/scene2.o'),
        }

    def test_roundtrip_preserves_structure(self, tmp_path):
        """Full extract+insert roundtrip preserves subfolder structure in patch/."""
        orig = _setup_orig(tmp_path)
        workpath = tmp_path / 'working'
        outpath = tmp_path / 'source'
        patchpath = tmp_path / 'patch'

        runner = CliRunner()

        # Simulate shell expansion of orig/*
        subdirs = sorted(p for p in orig.iterdir() if p.is_dir())
        result = runner.invoke(
            cli,
            [
                'extract',
                '--filetype=mgos',
                '--workpath',
                str(workpath),
                '--outpath',
                str(outpath),
                *[str(d) for d in subdirs],
            ],
        )
        assert result.exit_code == 0

        # Copy source translations to target (identity)
        transdir = tmp_path / 'target'
        shutil.copytree(outpath, transdir)

        result = runner.invoke(
            cli,
            [
                'insert',
                '--filetype=mgos',
                '--no-skip-identical',
                '--workpath',
                str(workpath),
                '--outpath',
                str(patchpath),
                str(transdir),
            ],
        )
        assert result.exit_code == 0, result.output

        patch_files = {p.relative_to(patchpath) for p in patchpath.rglob('*.o')}
        assert patch_files == {
            pathlib.Path('foo/bar/scene1.o'),
            pathlib.Path('foo/bar/scene2.o'),
            pathlib.Path('qux/scene3.o'),
        }
