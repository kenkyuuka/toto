from collections import namedtuple

TextLine = namedtuple('TextLine', ['key', 'text', 'eol'])


# --------------------------------------------------------------------------- #
# CP932 duplicate-encoding fixup table
#
# Python's cp932 codec maps several IBM extended characters (0xFA-0xFC range)
# to their NEC equivalents (0x81, 0x87, 0xED-0xEE range) on re-encode.  Both
# byte sequences decode to the same Unicode codepoint, but the engine may treat
# them differently.  We build a static table so we can restore whichever
# variant the original file used.
#
# _CP932_REENC_TO_ORIGINALS: python-preferred bytes -> [original variant(s)]
# For 396 of 398 pairs there is exactly one alternative; 2 are ambiguous.
# --------------------------------------------------------------------------- #
def _build_cp932_fixup_table():
    from collections import defaultdict

    reenc_to_orig = defaultdict(list)
    for hi in range(0x81, 0xFF):
        for lo in range(0x40, 0xFF):
            if lo == 0x7F:
                continue
            orig = bytes([hi, lo])
            try:
                ch = orig.decode('cp932')
                reenc = ch.encode('cp932')
                if reenc != orig:
                    reenc_to_orig[reenc].append(orig)
            except (UnicodeDecodeError, UnicodeEncodeError):
                pass
    return dict(reenc_to_orig)


_CP932_REENC_TO_ORIGINALS = _build_cp932_fixup_table()

# Inverted: for each non-roundtripping original, what does Python encode it as?
_CP932_ORIG_TO_REENC = {}
for _reenc, _origs in _CP932_REENC_TO_ORIGINALS.items():
    for _orig in _origs:
        _CP932_ORIG_TO_REENC[_orig] = _reenc


def build_file_cp932_fixup(raw_byte_strings):
    """Scan raw CP932 byte strings to build a per-file fixup table.

    Returns a dict mapping python-preferred bytes -> original bytes for each
    non-roundtripping character found in the file.  For the 2 ambiguous
    characters (where multiple originals map to the same python encoding),
    we use whichever variant actually appears in the file.
    """
    fixup = {}  # python-preferred -> file's original
    for raw in raw_byte_strings:
        pos = 0
        while pos < len(raw):
            b = raw[pos]
            if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFF) and pos + 1 < len(raw):
                pair = raw[pos : pos + 2]
                if pair in _CP932_ORIG_TO_REENC:
                    reenc = _CP932_ORIG_TO_REENC[pair]
                    fixup[reenc] = pair
                pos += 2
            else:
                pos += 1
    return fixup


def apply_cp932_fixup(encoded, fixup):
    """Apply the fixup table to CP932-encoded bytes, respecting char boundaries."""
    if not fixup:
        return encoded
    result = bytearray()
    pos = 0
    while pos < len(encoded):
        b = encoded[pos]
        if (0x81 <= b <= 0x9F or 0xE0 <= b <= 0xFF) and pos + 1 < len(encoded):
            pair = encoded[pos : pos + 2]
            replacement = fixup.get(bytes(pair))
            if replacement is not None:
                result.extend(replacement)
            else:
                result.extend(pair)
            pos += 2
        else:
            result.append(b)
            pos += 1
    return bytes(result)
