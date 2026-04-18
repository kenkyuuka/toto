import os
import pathlib
import re
import shelve
from importlib.metadata import entry_points

import click

from .util import TextLine

filetype_plugins = entry_points(group='toto.filetypes')


@click.group()
def cli():
    pass


@cli.command()
@click.argument('paths', type=click.Path(exists=True, resolve_path=True, path_type=pathlib.Path), nargs=-1)
@click.option(
    '--outpath',
    type=click.Path(file_okay=False, resolve_path=True, path_type=pathlib.Path),
    default=pathlib.Path('./project/source/'),
)
@click.option(
    '--workpath',
    type=click.Path(file_okay=False, resolve_path=True, path_type=pathlib.Path),
    default=pathlib.Path('./working/'),
)
@click.option('--codec', type=str, default=None)
@click.option('--filetype', type=str, required=True)
@click.option(
    '--ignore-line-regex', multiple=True, help='Regex pattern to ignore matching extracted lines. Can be repeated.'
)
@click.option(
    '--unwrap/--no-unwrap',
    default=False,
    help='Remove inline line breaks from extracted text so it can be re-wrapped on insertion.',
)
def extract(paths, outpath, workpath, codec, filetype=None, ignore_line_regex=(), unwrap=False):
    """Extract lines from script files into a single file.

    The files given in paths will be scanned for lines that look like
    translatable text. These lines will be extracted from the source
    files, and new intermediate files will be saved to workpath with
    tokens replacing the translatable lines. The lines to be translated
    will be saved in batches of 10000 to files in outpath.
    """

    ignore_patterns = []
    for pattern in ignore_line_regex:
        try:
            ignore_patterns.append(re.compile(pattern))
        except re.error as e:
            raise click.BadParameter(
                f'Invalid regex {pattern!r}: {e}',
                param_hint="'--ignore-line-regex'",
            ) from None

    workpath.mkdir(parents=True, exist_ok=True)
    outpath.parent.mkdir(parents=True, exist_ok=True)

    if filetype.lower() is None:
        raise RuntimeError("File type must be specified.")
    elif filetype.lower() not in filetype_plugins.names:
        raise RuntimeError("Unsupported file type specified.")
    else:
        fmt = filetype_plugins[filetype.lower()].load()

    # Compute common root of all input paths so that relative paths
    # preserve directory structure.
    # e.g. passing orig/foo orig/bar → common root is orig/
    #      → relative paths are foo/... and bar/...
    common_root = pathlib.Path(os.path.commonpath(paths)) if paths else pathlib.Path('.')
    if not common_root.is_dir():
        common_root = common_root.parent

    all_paths = []  # list of (absolute_path, relative_path) tuples
    for path in paths:
        if os.path.isdir(path):
            for p in fmt.get_paths(path):
                all_paths.append((p, p.relative_to(common_root)))
        else:
            all_paths.append((path, path.relative_to(common_root)))

    for path, rel_path in all_paths:
        with open(path, 'rb') as f:
            kwargs = {}
            if codec is not None:
                kwargs['codec'] = codec
            if ignore_patterns:
                kwargs['ignore_patterns'] = ignore_patterns
            if unwrap:
                kwargs['unwrap'] = True
            intermediate_file, textlines, metadata = fmt.extract_lines(f, **kwargs)
        (workpath / rel_path).parent.mkdir(parents=True, exist_ok=True)
        with open(workpath / rel_path, 'wb') as f:
            f.write(intermediate_file.read())

        with shelve.open(str(workpath / rel_path) + '.shelf') as shelf:
            shelf['lines'] = {t.key: t for t in textlines}
            shelf['metadata'] = metadata

        (outpath / rel_path).parent.mkdir(parents=True, exist_ok=True)
        for i in range(10**3):
            if 10000 * i >= len(textlines):
                break
            trans_name = str(rel_path) + f'.trans{i:03d}.txt'
            with open(outpath / trans_name, 'w', encoding='utf_8', errors='namereplace') as f:
                for t in textlines[i * 10000 : (i + 1) * 10000]:
                    f.write(t.text.replace('\n', '\\n') + '\n')


@cli.command()
@click.argument(
    'inpath',
    type=click.Path(exists=True, file_okay=False, resolve_path=True, path_type=pathlib.Path),
    default=pathlib.Path('./project/target/'),
)
@click.option(
    '--outpath',
    type=click.Path(file_okay=False, resolve_path=True, path_type=pathlib.Path),
    default=pathlib.Path('./patch/'),
)
@click.option(
    '--workpath',
    type=click.Path(file_okay=False, resolve_path=True, path_type=pathlib.Path),
    default=pathlib.Path('./working/'),
)
@click.option('--width', type=int, default=60)
@click.option('--codec', type=str, default=None)
@click.option('--wrap', type=str)
@click.option('--filetype', type=str)
@click.option(
    '--skip-identical/--no-skip-identical',
    default=False,
    help='Skip producing output for files where all translations match the original text.',
)
def insert(inpath, outpath, workpath, width, wrap, codec, filetype, skip_identical):
    """Reinsert translated lines into scripts.

    A list of translated lines given by inpath will be inserted into the
    correct location in the files in workpath, generating a translated
    version of the original scripts in outpath.
    """
    if filetype.lower() is None:
        raise RuntimeError("File type must be specified.")
    elif filetype.lower() not in filetype_plugins.names:
        raise RuntimeError("Unsupported file type specified.")
    else:
        fmt = filetype_plugins[filetype.lower()].load()

    paths = [p for p in fmt.get_paths(workpath) if not str(p).endswith('.shelf')]

    for path in paths:
        rel_path = path.relative_to(workpath)

        with shelve.open(str(path) + '.shelf') as shelf:
            intrans = shelf['lines']
            metadata = shelf.get('metadata', {})

        tlines = {}
        for i in range(10**3):
            trans_name = str(rel_path) + f'.trans{i:03d}.txt'
            if not os.path.exists(inpath / trans_name):
                break
            with open(inpath / trans_name, encoding='utf_8') as vs:
                tlines.update(dict(zip(list(intrans.keys())[i * 10000 : (i + 1) * 10000], list(vs), strict=True)))

        if skip_identical and tlines:
            all_identical = all(
                v.rstrip('\n') == intrans[k.strip()].text.replace('\n', '\\n') for k, v in tlines.items()
            )
            if all_identical:
                continue

        trans = {}
        for k, v in tlines.items():
            try:
                trans[k] = TextLine(k, v, intrans[k.strip()].eol)
            except Exception:
                print("keys: {!r}", list(intrans.keys())[:5])
                raise

        insert_codec = codec if codec is not None else metadata.get('codec')
        insert_kwargs = {}
        if insert_codec is not None:
            insert_kwargs['codec'] = insert_codec
        if metadata.get('bom'):
            insert_kwargs['bom'] = metadata['bom']
        if metadata.get('encryption_key'):
            insert_kwargs['encryption_key'] = metadata['encryption_key']
        if metadata.get('cp932_fixup'):
            insert_kwargs['cp932_fixup'] = metadata['cp932_fixup']

        if fmt.default_wrap is not None:
            insert_kwargs['width'] = width
            insert_kwargs['wrap'] = wrap
        elif width:
            click.echo(f"Warning: {filetype} handler does not support text wrapping; --width ignored.")

        with open(path, 'rb') as f:
            output_file = fmt.insert_lines(f, trans, **insert_kwargs)
        (outpath / rel_path).parent.mkdir(parents=True, exist_ok=True)
        with open(outpath / rel_path, 'wb') as f:
            f.write(output_file.read())
