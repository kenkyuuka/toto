import codecs
import textwrap
from abc import ABC, abstractmethod

import chardet
import chardet.enums


class TranslatableFile(ABC):
    default_wrap: str | None = None
    """The default wrap separator for this format (e.g. ``'[r]'`` for KiriKiri).

    Setting this to a non-None value signals that the handler supports text
    wrapping during insertion.  Handlers may also override
    ``should_wrap_line`` to provide a format-specific predicate for skipping
    wrapping on certain lines.
    """

    @classmethod
    def should_wrap_line(cls, text: str, width: int | None) -> bool:
        """Return True if this line of text should be wrapped.

        Returns False when *width* is None (wrapping disabled) or the line
        already fits within *width*.  Subclasses may add format-specific
        conditions (e.g. skipping lines that contain inline commands) by
        overriding and calling ``super().should_wrap_line(text, width)``.
        """
        return width is not None and len(text) > width

    @staticmethod
    def wrap_text(text: str, width: int, wrap: str, newline: str) -> str:
        """Wrap *text* to *width* columns, joining wrapped lines with *wrap* + *newline*.

        This uses :func:`textwrap.wrap` to break the text into lines of at most
        *width* characters.  The *wrap* separator (a format-specific line-break
        macro such as ``[r]``) and the file's *newline* sequence are inserted
        between wrapped segments.

        Returns the wrapped text (without a trailing newline or eol macro —
        the caller is responsible for appending those).
        """
        return (wrap + newline).join(textwrap.wrap(text.strip(), width=width))

    @staticmethod
    def detect_encoding(data: bytes) -> str:
        """Detect the encoding of data using chardet with a Japanese language hint.

        Returns the detected encoding name (normalized for Python's codec system).
        """
        detector = chardet.UniversalDetector(lang_filter=chardet.enums.LanguageFilter.JAPANESE)
        detector.feed(data)
        detector.close()
        result = detector.result
        encoding = result['encoding'] or 'shift_jis'
        return codecs.lookup(encoding).name

    @staticmethod
    @abstractmethod
    def get_paths(workpath):
        """Return a list of supported files in workpath.

        This might just glob for supported file extensions, or it might do more complex analysis on
        the files in workpath.
        """
        ...

    @staticmethod
    def _should_ignore(text, ignore_patterns):
        """Return True if text matches any of the ignore patterns."""
        return any(p.search(text) for p in ignore_patterns)

    @classmethod
    @abstractmethod
    def extract_lines(cls, input_file, ignore_patterns=(), **kwargs):
        """Extract translatable lines from input_file.

        Return a tuple (output_file, textlines, metadata). output_file is a bytestream that can be
        written out as an intermediate file (to be passed back to insert_lines later). textlines is
        a sequence of TextLines to be translated. metadata is a dict of handler-specific data to be
        stored between extract and insert phases (e.g. {'codec': 'shift_jis'}).

        If ignore_patterns is provided, lines whose text matches any of the compiled regex patterns
        will be treated as non-translatable and left verbatim in the intermediate file.

        Subclasses may accept additional keyword arguments for handler-specific options.
        """
        ...

    @staticmethod
    @abstractmethod
    def insert_lines(intermediate_file, textlines, **kwargs):
        """Insert translated lines into intermediate_file.

        Return a bytestream ready to be written out to disk.

        Subclasses may accept additional keyword arguments (width, wrap, codec, etc.).
        """
        ...
