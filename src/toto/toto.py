import inspect
import os
import pathlib
import re
import shelve
from importlib.metadata import entry_points

import click

from .po import PoError, read_po, source_text, write_po
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
    default=None,
    help='Remove inline line breaks from extracted text so it can be re-wrapped on insertion. '
    'The default depends on the file type (on for DxLib, off for AGSD).',
)
@click.option(
    '--key',
    type=str,
    default=None,
    help='Hex encryption key for formats that need one (BLACKRAINBOW message key). '
    'By default the handler tries the keys it knows.',
)
@click.option(
    '--output-format',
    type=click.Choice(['txt', 'po'], case_sensitive=False),
    default='txt',
    help='Format of the text to translate: txt (one line per string, in batches of 10000) or po '
    '(one gettext PO file per script, with metadata such as the speaker as comments).',
)
@click.option(
    '--group-start',
    multiple=True,
    help='Prefix of a text line that starts a message spanning several lines. The lines are joined into one '
    'string up to a line-end macro other than [r], or a blank, command or tag-only line. Can be repeated. '
    "Default: '「' and '　' (kirikiri).",
)
@click.option(
    '--group-all-lines',
    is_flag=True,
    default=False,
    help='Let every text line start a multi-line message, instead of only those given by --group-start.',
)
@click.option(
    '--line-end-macros',
    type=str,
    default=None,
    metavar='REGEX',
    help=r"Regex for the tags that end a line of text, such as '(?:\[L_NEXT\]|\[T_NEXT\])\\?'. [r] continues "
    r"a message, any other match ends it. An empty value turns off macro handling and grouping. "
    r"Default: '(?:\[[^\]]+\])+\\?' (kirikiri).",
)
@click.option(
    '--strip-continuation-indent',
    is_flag=True,
    default=False,
    help='Remove full-width spaces from the start of the second and later lines of a multi-line message.',
)
def extract(
    paths,
    outpath,
    workpath,
    codec,
    filetype=None,
    ignore_line_regex=(),
    unwrap=None,
    key=None,
    output_format='txt',
    group_start=(),
    group_all_lines=False,
    line_end_macros=None,
    strip_continuation_indent=False,
):
    """Extract lines from script files into a single file.

    The files given in paths will be scanned for lines that look like
    translatable text. These lines will be extracted from the source
    files, and new intermediate files will be saved to workpath with
    tokens replacing the translatable lines. The lines to be translated
    are saved to outpath: with --output-format txt, in batches of 10000
    lines to <script>.transNNN.txt; with --output-format po, to one
    <script>.po file per script.
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

    if key is not None:
        try:
            if not bytes.fromhex(key):
                raise ValueError('empty key')
        except ValueError as e:
            raise click.BadParameter(f'{key!r} is not a hex key: {e}', param_hint="'--key'") from None

    if group_all_lines and group_start:
        raise click.UsageError('--group-all-lines and --group-start cannot be used together.')
    if '' in group_start:
        raise click.BadParameter(
            'an empty prefix is not allowed; use --group-all-lines to group every text line.',
            param_hint="'--group-start'",
        )
    if line_end_macros:
        try:
            re.compile(line_end_macros)
        except re.error as e:
            raise click.BadParameter(
                f'Invalid regex {line_end_macros!r}: {e}', param_hint="'--line-end-macros'"
            ) from None

    workpath.mkdir(parents=True, exist_ok=True)
    outpath.parent.mkdir(parents=True, exist_ok=True)

    if filetype.lower() is None:
        raise RuntimeError("File type must be specified.")
    elif filetype.lower() not in filetype_plugins.names:
        raise RuntimeError("Unsupported file type specified.")
    else:
        fmt = filetype_plugins[filetype.lower()].load()

    # Grouping options, passed only to handlers that take them.
    grouping_kwargs = {}
    if group_all_lines:
        grouping_kwargs['group_starts'] = ('--group-all-lines', ('',))
    elif group_start:
        grouping_kwargs['group_starts'] = ('--group-start', tuple(group_start))
    if line_end_macros is not None:
        grouping_kwargs['line_end_macros'] = ('--line-end-macros', line_end_macros)
    if strip_continuation_indent:
        grouping_kwargs['strip_continuation_indent'] = ('--strip-continuation-indent', True)
    handler_params = inspect.signature(fmt.extract_lines).parameters
    for name, (flag, _) in list(grouping_kwargs.items()):
        if name not in handler_params:
            click.echo(f'Warning: {filetype} handler does not support {flag}; ignored.', err=True)
            del grouping_kwargs[name]

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
            if unwrap is not None:
                kwargs['unwrap'] = unwrap
            if key is not None:
                kwargs['key'] = key
            kwargs.update({name: value for name, (_, value) in grouping_kwargs.items()})
            intermediate_file, textlines, metadata = fmt.extract_lines(f, **kwargs)
        (workpath / rel_path).parent.mkdir(parents=True, exist_ok=True)
        with open(workpath / rel_path, 'wb') as f:
            f.write(intermediate_file.read())

        with shelve.open(str(workpath / rel_path) + '.shelf') as shelf:
            shelf['lines'] = {t.key: t for t in textlines}
            shelf['metadata'] = metadata

        (outpath / rel_path).parent.mkdir(parents=True, exist_ok=True)
        if output_format.lower() == 'po':
            # Like the txt output, a script with nothing to translate gets no file.
            if textlines:
                po_name = str(rel_path) + '.po'
                with open(outpath / po_name, 'w', encoding='utf_8', errors='namereplace', newline='\n') as f:
                    write_po(f, textlines)
            continue
        for i in range(10**3):
            if 10000 * i >= len(textlines):
                break
            trans_name = str(rel_path) + f'.trans{i:03d}.txt'
            with open(outpath / trans_name, 'w', encoding='utf_8', errors='namereplace') as f:
                for t in textlines[i * 10000 : (i + 1) * 10000]:
                    f.write(source_text(t) + '\n')


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
@click.option(
    '--width', type=int, default=None, help="Wrap width in characters. Defaults to the handler's default (60 for most)."
)
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

    Translations are read from <script>.po if it exists (as written by
    extract --output-format po), and from <script>.transNNN.txt otherwise.
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

        po_path = inpath / (str(rel_path) + '.po')
        txt_path = inpath / (str(rel_path) + '.trans000.txt')
        if po_path.exists():
            if txt_path.exists():
                raise click.ClickException(
                    f'Found both {po_path} and {txt_path}; remove the translations in the format no longer used.'
                )
            tlines = _read_po_translations(po_path, intrans)
        else:
            tlines = {}
            for i in range(10**3):
                trans_name = str(rel_path) + f'.trans{i:03d}.txt'
                if not os.path.exists(inpath / trans_name):
                    break
                with open(inpath / trans_name, encoding='utf_8') as vs:
                    lines = [v.rstrip('\n') for v in vs]
                    tlines.update(dict(zip(list(intrans.keys())[i * 10000 : (i + 1) * 10000], lines, strict=True)))
            # Lines without a translation (no files, or a missing batch) keep their source text,
            # as in PO mode. Handlers would otherwise fail, or leave placeholder keys in the output.
            missing = len(intrans) - len(tlines)
            if missing:
                if tlines:
                    click.echo(
                        f'Warning: {missing} lines of {rel_path} have no translation; keeping source text.', err=True
                    )
                else:
                    click.echo(f'Warning: no translations found for {rel_path}; keeping source text.', err=True)
                tlines = {k: tlines[k] if k in tlines else source_text(t) for k, t in intrans.items()}

        if skip_identical and tlines:
            all_identical = all(v == intrans[k.strip()].text.replace('\n', '\\n') for k, v in tlines.items())
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
        if metadata.get('update_body_size'):
            insert_kwargs['update_body_size'] = metadata['update_body_size']

        if fmt.default_wrap is not None:
            insert_kwargs['width'] = width if width is not None else fmt.default_width
            insert_kwargs['wrap'] = wrap
        elif width:
            click.echo(f"Warning: {filetype} handler does not support text wrapping; --width ignored.")

        with open(path, 'rb') as f:
            output_file = fmt.insert_lines(f, trans, **insert_kwargs)
        (outpath / rel_path).parent.mkdir(parents=True, exist_ok=True)
        with open(outpath / rel_path, 'wb') as f:
            f.write(output_file.read())


def _read_po_translations(po_path, intrans):
    """Translations from the PO file *po_path*, as the txt path builds them: key -> escaped text.

    Entries are matched to the shelf's lines by ``msgctxt``. A line with no entry, or an entry
    with an empty ``msgstr``, keeps its source text.
    """
    try:
        with open(po_path, encoding='utf_8') as f:
            entries = read_po(f)
    except PoError as e:
        raise click.ClickException(f'{po_path}: {e}') from None

    found = {}
    for entry in entries:
        if entry.msgctxt not in intrans:
            click.echo(f'Warning: {po_path}:{entry.line}: unknown key {entry.msgctxt!r}; entry ignored.', err=True)
            continue
        if entry.msgctxt in found:
            raise click.ClickException(f'{po_path}:{entry.line}: duplicate key {entry.msgctxt!r}')
        found[entry.msgctxt] = entry.msgstr or entry.msgid

    # Build in shelf order, as the txt path does.
    return {k: found[k] if k in found else source_text(t) for k, t in intrans.items()}
