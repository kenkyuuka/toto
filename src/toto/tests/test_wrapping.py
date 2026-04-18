"""Tests for the text wrapping support in TranslatableFile."""

import pytest

from toto.filetypes.Anim import Anim
from toto.filetypes.DxLib import DxLib
from toto.filetypes.KiriKiriScriptV2 import KiriKiriScript
from toto.filetypes.Mgos import Mgos
from toto.filetypes.TranslatableFile import TranslatableFile


class TestWrapText:
    """Tests for TranslatableFile.wrap_text static method."""

    @pytest.mark.unit
    def test_basic_wrapping(self):
        result = TranslatableFile.wrap_text(
            "Alice was beginning to get very tired of sitting by her sister on the bank",
            width=30,
            wrap="[r]",
            newline="\r\n",
        )
        segments = result.split("[r]\r\n")
        assert len(segments) > 1
        assert all(len(s) <= 30 for s in segments)

    @pytest.mark.unit
    def test_short_text_unchanged(self):
        result = TranslatableFile.wrap_text("Short text.", width=60, wrap="[r]", newline="\n")
        assert result == "Short text."

    @pytest.mark.unit
    def test_custom_wrap_separator(self):
        result = TranslatableFile.wrap_text(
            "Alice was beginning to get very tired of sitting by her sister",
            width=30,
            wrap="<br>",
            newline="\n",
        )
        assert "<br>\n" in result

    @pytest.mark.unit
    def test_strips_whitespace(self):
        result = TranslatableFile.wrap_text("  hello world  ", width=60, wrap="[r]", newline="\n")
        assert result == "hello world"


class TestDefaultWrap:
    """Tests for the default_wrap class attribute.

    A non-None default_wrap signals that the handler supports wrapping.
    """

    @pytest.mark.unit
    def test_kirikiri_supports_wrapping(self):
        assert KiriKiriScript.default_wrap == "[r]"

    @pytest.mark.unit
    def test_dxlib_does_not_support_wrapping(self):
        assert DxLib.default_wrap is None

    @pytest.mark.unit
    def test_anim_does_not_support_wrapping(self):
        assert Anim.default_wrap is None

    @pytest.mark.unit
    def test_mgos_does_not_support_wrapping(self):
        assert Mgos.default_wrap is None

    @pytest.mark.unit
    def test_base_default_wrap_is_none(self):
        assert TranslatableFile.default_wrap is None


class TestShouldWrapLine:
    """Tests for the should_wrap_line predicate."""

    @pytest.mark.unit
    def test_base_returns_true_when_text_exceeds_width(self):
        assert TranslatableFile.should_wrap_line("a long line of text", width=10) is True

    @pytest.mark.unit
    def test_base_returns_false_when_text_fits_in_width(self):
        assert TranslatableFile.should_wrap_line("short", width=10) is False

    @pytest.mark.unit
    def test_base_returns_false_when_text_equals_width(self):
        assert TranslatableFile.should_wrap_line("exactly 10", width=10) is False

    @pytest.mark.unit
    def test_base_returns_false_when_width_is_none(self):
        assert TranslatableFile.should_wrap_line("any text at all", width=None) is False

    @pytest.mark.unit
    def test_kirikiri_skips_lines_with_brackets(self):
        assert KiriKiriScript.should_wrap_line("plain text that is long enough to wrap", width=10) is True
        assert KiriKiriScript.should_wrap_line("text with [ruby text=cmd] inside that is long", width=10) is False

    @pytest.mark.unit
    def test_kirikiri_skips_short_lines(self):
        assert KiriKiriScript.should_wrap_line("short", width=60) is False
