from io import BytesIO
from pathlib import Path

import pytest

from toto.filetypes.KiriKiriScriptV2 import KiriKiriScript

SAMPLES = Path(__file__).parent / "samples"

FOLDERS = [p.name for p in sorted(SAMPLES.iterdir()) if p.is_dir() and p.name != "nonfree"]


@pytest.mark.parametrize("folder", FOLDERS)
def test_roundtrip(folder):
    workpath = SAMPLES / folder
    paths = KiriKiriScript.get_paths(workpath)
    assert paths, f"No compatible files found in {workpath}"

    for path in paths:
        original_data = path.read_bytes()
        input_file = BytesIO(original_data)

        intermediate_file, textlines, metadata = KiriKiriScript.extract_lines(input_file, line_end_macros=None)

        # Build identity translation dict: key -> original TextLine
        translation_dict = {tl.key: tl for tl in textlines}

        output_file = KiriKiriScript.insert_lines(intermediate_file, translation_dict)
        output_data = output_file.read()

        assert output_data == original_data, (
            f"Roundtrip mismatch for {path.name}: " f"original {len(original_data)} bytes, got {len(output_data)} bytes"
        )
