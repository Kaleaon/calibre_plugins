"""Content detection and recovery. Original files are never modified."""
import hashlib
import io
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import zipfile

MAX_MEMBER = 512 * 1024 * 1024
MAX_TOTAL = 10 * 1024 * 1024 * 1024
BOOK_SUFFIXES = {'.pdf', '.epub', '.mobi', '.azw', '.azw3', '.cbz', '.cbr', '.fb2', '.djvu'}


def detect(data):
    if data[:5] == b'%PDF-':
        return 'pdf'
    if data.startswith((b'Rar!\x1a\x07\x00', b'Rar!\x1a\x07\x01\x00')):
        return 'cbr'  # RAR containers are handled conservatively by recover().
    if data.startswith(b'AT&TFORM') and data[12:16] in (b'DJVU', b'DJVM'):
        return 'djvu'
    if len(data) >= 68 and data[60:68] == b'BOOKMOBI':
        return 'mobi'
    if zipfile.is_zipfile(io.BytesIO(data)):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names = z.namelist()
                if 'mimetype' in names and z.getinfo('mimetype').file_size < 100 and z.read('mimetype') == b'application/epub+zip' and 'META-INF/container.xml' in names:
                    return 'epub'
                files = [n for n in names if not n.endswith('/') and not n.startswith('__MACOSX/')]
                images = [n for n in files if Path(n).suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}]
                if images and all(n in images or Path(n).suffix.lower() in {'.xml', '.txt', '.nfo'} for n in files):
                    return 'cbz'
        except (zipfile.BadZipFile, KeyError, OSError, RuntimeError):
            pass
    return None


def image_pdf_to_cbz(data):
    """Require no substantive text and render every page, preserving page order."""
    with tempfile.TemporaryDirectory(prefix='bookfixer-') as tmp:
        source = Path(tmp) / 'source.pdf'
        source.write_bytes(data)
        try:
            import pymupdf as fitz
        except ImportError:
            fitz = None
        if fitz is not None:
            with fitz.open(source) as doc:
                if doc.needs_pass:
                    raise ValueError('Encrypted PDF cannot be converted')
                if not len(doc) or len(doc) > 2000:
                    raise ValueError('PDF page count outside supported range (1–2000)')
                if any(re.search(r'\w', page.get_text()) for page in doc):
                    return None
                output = io.BytesIO()
                with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as z:
                    for index, page in enumerate(doc):
                        if page.rect.width * page.rect.height > 20_000_000:
                            raise ValueError('PDF page dimensions exceed safety limit')
                        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)
                        z.writestr(f'{index + 1:05d}.png', pix.tobytes('png'))
                        if output.tell() > MAX_MEMBER:
                            raise ValueError('Comic output exceeds 512 MiB safety limit')
                return output.getvalue()
        for tool in ('pdftotext', 'pdftoppm', 'pdfinfo'):
            if not shutil.which(tool):
                raise ValueError('PDF conversion requires PyMuPDF or Poppler (pdftotext, pdftoppm, pdfinfo)')
        info = subprocess.run(['pdfinfo', str(source)], capture_output=True, check=True, timeout=60).stdout.decode(errors='replace')
        match = re.search(r'^Pages:\s+(\d+)', info, re.M)
        if not match or not 1 <= int(match.group(1)) <= 2000:
            raise ValueError('Unsupported PDF page count')
        text = subprocess.run(['pdftotext', str(source), '-'], capture_output=True, check=True, timeout=120).stdout.decode(errors='replace')
        if re.search(r'\w', text):
            return None
        subprocess.run(['pdftoppm', '-scale-to', '2400', '-png', str(source), str(Path(tmp) / 'page')], capture_output=True, check=True, timeout=600)
        pages = sorted(Path(tmp).glob('page-*.png'), key=lambda p: int(p.stem.rsplit('-', 1)[1]))
        if len(pages) != int(match.group(1)) or sum(p.stat().st_size for p in pages) > MAX_MEMBER:
            raise ValueError('Incomplete rendering or comic exceeds size limit')
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w', zipfile.ZIP_STORED) as z:
            for index, page in enumerate(pages):
                z.write(page, f'{index + 1:05d}.png')
        return output.getvalue()


def sources(source, output, cancelled=lambda: False):
    source, output = Path(source).resolve(), Path(output).resolve()
    total = 0
    if source.is_file():
        if not zipfile.is_zipfile(source):
            if source.stat().st_size > MAX_MEMBER:
                raise ValueError('Input book exceeds 512 MiB safety limit')
            if cancelled():
                return
            yield source.name, source.read_bytes()
            return
        try:
            with zipfile.ZipFile(source) as z:
                names = z.namelist()
                files = [n for n in names if not n.endswith('/') and not n.startswith('__MACOSX/')]
                images = [n for n in files if Path(n).suffix.lower() in {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp'}]
                is_epub = ('mimetype' in names and z.getinfo('mimetype').file_size < 100 and z.read('mimetype') == b'application/epub+zip' and 'META-INF/container.xml' in names)
                is_comic = images and all(n in images or Path(n).suffix.lower() in {'.xml', '.txt', '.nfo'} for n in files)
                if is_epub or is_comic:
                    if source.stat().st_size > MAX_MEMBER:
                        raise ValueError('Input book exceeds 512 MiB safety limit')
                    if cancelled():
                        return
                    yield source.name, source.read_bytes()
                    return
                for member in z.infolist():
                    if cancelled():
                        return
                    if member.is_dir():
                        continue
                    total += member.file_size
                    if member.file_size > MAX_MEMBER or total > MAX_TOTAL:
                        raise ValueError('Takeout exceeds safety limits: 512 MiB per file, 10 GiB total')
                    if member.flag_bits & 1:
                        raise ValueError('Encrypted Takeout ZIP is unsupported')
                    yield member.filename, z.read(member)
        except (zipfile.BadZipFile, KeyError, OSError, RuntimeError) as exc:
            raise ValueError(f'Corrupted or invalid ZIP file: {exc}') from exc
    else:
        for path in sorted(source.rglob('*')):
            if cancelled():
                return
            if not path.is_file() or path.is_symlink() or output == path or output in path.parents:
                continue
            size = path.stat().st_size
            total += size
            if size > MAX_MEMBER or total > MAX_TOTAL:
                raise ValueError('Takeout exceeds safety limits: 512 MiB per file, 10 GiB total')
            yield str(path.relative_to(source)), path.read_bytes()


def recover(source, output, comics=False, progress=None, cancelled=lambda: False):
    output = Path(output).resolve()
    source = Path(source).resolve()
    if source == output or (source.is_dir() and source in output.parents):
        raise ValueError('Choose an output folder outside the input folder')
    output.mkdir(parents=True, exist_ok=True)
    report, written, sidecars = [], [], {}
    try:
        for name, data in sources(source, output, cancelled=cancelled):
            if cancelled():
                break
            if progress:
                progress(name)
            if name.lower().endswith('.json') and len(data) <= 1024 * 1024:
                try:
                    meta = json.loads(data)
                    if isinstance(meta, dict):
                        sidecars[name.replace('\\', '/')] = meta.get('volumeInfo', meta)
                except (ValueError, UnicodeDecodeError):
                    pass
            row = {'source': name}
            report.append(row)
            try:
                kind = detect(data)
                if kind == 'cbr' and Path(name).suffix.lower() != '.cbr':
                    kind = None  # RAR signature alone cannot prove this is a comic.
                if kind is None:
                    row.update(status='skipped', reason='Unknown or non-book content; no format guessed')
                    continue
                if comics and kind == 'pdf':
                    converted = image_pdf_to_cbz(data)
                    if converted is not None:
                        data, kind = converted, 'cbz'
                        row['conversion'] = 'Rendered text-free PDF pages to comic archive'
                stem = Path(name.replace('\\', '/')).stem
                stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', stem).strip(' .')[:100] or 'book'
                # Content hash prevents duplicate names and makes repeated recovery idempotent.
                digest = hashlib.sha256(data).hexdigest()[:16]
                target = output / f'book-{stem}-{digest}.{kind}'
                if target.exists():
                    if target.read_bytes() != data:
                        raise ValueError('Output collision; existing file preserved')
                    row['status'] = 'already recovered'
                else:
                    with target.open('xb') as f:
                        f.write(data)
                    row['status'] = 'recovered'
                row.update(format=kind, output=str(target))
                written.append(str(target))
            except Exception as e:
                row.update(status='error', reason=str(e))
    except Exception as e:
        report.append({'source': str(source), 'status': 'error', 'reason': str(e)})
    for row in report:
        if 'output' not in row:
            continue
        name = row['source'].replace('\\', '/')
        candidates = [name + '.json', str(Path(name).with_suffix('.json'))]
        for candidate in candidates:
            if candidate in sidecars:
                row['metadata'] = sidecars[candidate]
                break
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=output, prefix='recovery-report-', suffix='.json', delete=False) as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
        report_path = f.name
    return written, report, report_path
