"""Tests for the text wrapping support in TranslatableFile."""

from io import BytesIO

import pytest

from toto.filetypes.Anim import Anim
from toto.filetypes.DxLib import DxLib
from toto.filetypes.KiriKiriScriptV2 import KiriKiriScript
from toto.filetypes.Mgos import Mgos
from toto.filetypes.TranslatableFile import TranslatableFile
from toto.util import TextLine


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
    def test_kirikiri_wraps_lines_with_tags(self):
        assert KiriKiriScript.should_wrap_line("plain text that is long enough to wrap", width=10) is True
        assert KiriKiriScript.should_wrap_line("text with [ruby text=cmd] inside that is long", width=10) is True

    @pytest.mark.unit
    def test_kirikiri_skips_tagged_lines_that_fit(self):
        assert KiriKiriScript.should_wrap_line("[font size=30]Alice said", width=10) is False

    @pytest.mark.unit
    def test_kirikiri_skips_malformed_tags(self):
        assert KiriKiriScript.should_wrap_line("Alice said [unclosed and kept on talking", width=10) is False

    @pytest.mark.unit
    def test_kirikiri_skips_short_lines(self):
        assert KiriKiriScript.should_wrap_line("short", width=60) is False


class TestKiriKiriTagAwareWrapping:
    """Tests for wrapping KiriKiri text that contains inline tags."""

    @staticmethod
    def wrap(text, width):
        return KiriKiriScript.wrap_text(text, width, "[r]", "\n")

    @pytest.mark.unit
    def test_glyph_tags_wrap(self):
        text = (
            "Oh dear, oh dear[☆] I shall be too late for the Duchess... "
            "my ears and whiskers[☆] how late it's getting[☆]"
        )
        result = self.wrap(text, 65)
        assert result == (
            "Oh dear, oh dear[☆] I shall be too late for the Duchess... my ears[r]\n"
            "and whiskers[☆] how late it's getting[☆]"
        )

    @pytest.mark.unit
    def test_glyph_tag_counts_as_one_character(self):
        assert KiriKiriScript.should_wrap_line("Alice[☆] said", width=11) is False
        assert KiriKiriScript.should_wrap_line("Alice[☆] said", width=10) is True
        assert self.wrap("Alice[☆] said", 10) == "Alice[☆][r]\nsaid"

    @pytest.mark.unit
    def test_formatting_tags_are_zero_width(self):
        assert self.wrap("[font size=30]Alice said[resetfont] so", 10) == "[font size=30]Alice said[resetfont][r]\nso"

    @pytest.mark.unit
    def test_tags_are_not_split(self):
        result = self.wrap('[ruby text="White Rabbit"]Usagi ran down the hole', 10)
        assert result == '[ruby text="White Rabbit"]Usagi ran[r]\ndown the[r]\nhole'

    @pytest.mark.unit
    def test_escaped_bracket_counts_as_one_character(self):
        assert KiriKiriScript.should_wrap_line("[[Alice] said", width=12) is False
        assert KiriKiriScript.should_wrap_line("[[Alice] said", width=11) is True
        assert self.wrap("[[Alice] said", 11) == "[[Alice][r]\nsaid"

    @pytest.mark.unit
    def test_explicit_line_break_resets_width(self):
        result = self.wrap("Down, down, down.[r]Would the fall never come to an end", 20)
        assert result == "Down, down, down.[r]Would the fall never[r]\ncome to an end"

    @pytest.mark.unit
    def test_insert_wraps_tagged_lines(self):
        intermediate = BytesIO(b"<<<TRANS:0>>>\r\n")
        translations = {"<<<TRANS:0>>>": TextLine("<<<TRANS:0>>>", "Alice[☆] said nothing", "[p]")}
        result = KiriKiriScript.insert_lines(intermediate, translations, width=10, codec="utf-8")
        assert result.read().decode("utf-8") == "Alice[☆][r]\r\nsaid[r]\r\nnothing[p]\r\n"
