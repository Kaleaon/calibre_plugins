"""Remove third-party promotional marks (watermarks, banner pages, links) from EPUB files.

Pure Python plus lxml (bundled with Calibre). Nothing here touches DRM: documents that
cannot be parsed as well-formed XML, including encrypted ones, are left byte-for-byte unchanged.
"""
import io
import json
import os
import posixpath
import re
import zipfile
from pathlib import Path
from urllib.parse import unquote, urldefrag

from lxml import etree

try:
    from calibre_plugins.epub_promo_scrubber.profiles import (
        extract_domains_from_metadata,
        extract_domains_from_opf,
        extract_domains_from_text,
        resolve_profile,
    )
except ImportError:
    try:
        from calibre_plugins.scrubber_plugin.profiles import (
            extract_domains_from_metadata,
            extract_domains_from_opf,
            extract_domains_from_text,
            resolve_profile,
        )
    except ImportError:
        try:
            from .profiles import (
                extract_domains_from_metadata,
                extract_domains_from_opf,
                extract_domains_from_text,
                resolve_profile,
            )
        except ImportError:
            import importlib.util
            from pathlib import Path

            prof_path = Path(__file__).parent / 'profiles.py'
            if prof_path.exists():
                spec = importlib.util.spec_from_file_location('profiles', prof_path)
                prof_mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(prof_mod)
                extract_domains_from_metadata = prof_mod.extract_domains_from_metadata
                extract_domains_from_opf = prof_mod.extract_domains_from_opf
                extract_domains_from_text = prof_mod.extract_domains_from_text
                resolve_profile = prof_mod.resolve_profile
            else:
                from profiles import (
                    extract_domains_from_opf,
                    resolve_profile,
                )

DEFAULT_PATTERNS = [r'oceanofpdf', r'pdfdrive', r'z-?library', r'1lib', r'libgen', r'library\s+genesis']
MAX_BLOCK_CHARS = 250
MAX_TOTAL_BYTES = 1 << 30
BLOCK_TAGS = {'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'li', 'ul', 'ol', 'blockquote', 'td', 'th', 'tr',
              'table', 'dt', 'dd', 'dl', 'section', 'article', 'aside', 'figure', 'figcaption', 'pre', 'center'}
LEAF_BLOCKS = BLOCK_TAGS - {'ul', 'ol', 'tr', 'table', 'dl'}
MEDIA_TAGS = {'img', 'image', 'svg', 'object', 'video', 'audio', 'math', 'iframe', 'embed', 'canvas'}
URLISH = re.compile(r'https?://|www\.|\b[a-z0-9-]+\.(?:com|net|org|info|io|me|cc|to|ru|biz)\b', re.I)
PROMO = re.compile(r'downloaded\s+from|free\s+(?:e-?)?books?|(?:e-?)?books?\s+for\s+free|download\s+(?:free\s+)?(?:e-?)?books?', re.I)
DOC_TYPES = {'application/xhtml+xml', 'text/html'}
NCX_TYPE = 'application/x-dtbncx+xml'
DC_SINGLE = {'publisher', 'source', 'rights', 'contributor'}


class Matcher:
    """Decides what counts as promotional material."""

    def __init__(self, extra=(), heuristic=True):
        parts = list(DEFAULT_PATTERNS)
        for item in extra:
            item = item.strip()
            if item:
                parts.append(item[3:] if item.startswith('re:') else re.escape(item))
        core = '|'.join(f'(?:{p})' for p in parts)
        self.name = re.compile(core, re.I)
        self.span = re.compile(r'(?:https?://)?(?:www\.)?(?:[a-z0-9-]+\.)*(?:%s)(?:\.[a-z]{2,6})?(?:[/?#][^\s<>"\']*)?' % core, re.I)
        self.heuristic = heuristic

    def hit(self, text):
        return bool(self.name.search(text))

    def promo_block(self, text):
        if not text or len(text) > MAX_BLOCK_CHARS:
            return False
        if self.hit(text):
            return True
        return self.heuristic and bool(URLISH.search(text) and PROMO.search(text))

    def clean_text(self, text):
        return self.span.sub('', text)


def tidy(text):
    """Tidy leftovers after removing a mark from a metadata string."""
    text = re.sub(r'\(\s*\)|\[\s*\]|\{\s*\}', '', text)
    text = re.sub(r'\s{2,}', ' ', text)
    return re.sub(r'^[\s\-–—|:_,;]+|[\s\-–—|:_,;]+$', '', text)


def _local(el):
    tag = el.tag
    return etree.QName(tag).localname.lower() if isinstance(tag, str) else None


def _norm(text):
    return ' '.join(text.split())


def _remove(el):
    """Remove an element but keep its tail text."""
    parent = el.getparent()
    if parent is None:
        return
    tail = el.tail
    prev = el.getprevious()
    if tail:
        if prev is not None:
            prev.tail = (prev.tail or '') + tail
        else:
            parent.text = (parent.text or '') + tail
    parent.remove(el)


def _unwrap(el):
    """Replace an element with its own content."""
    parent = el.getparent()
    index = parent.index(el)
    prev = el.getprevious()
    lead = el.text or ''
    if lead:
        if prev is not None:
            prev.tail = (prev.tail or '') + lead
        else:
            parent.text = (parent.text or '') + lead
    children = list(el)
    for offset, child in enumerate(children):
        parent.insert(index + offset, child)
    last = children[-1] if children else prev
    tail = el.tail or ''
    if tail:
        if last is not None:
            last.tail = (last.tail or '') + tail
        else:
            parent.text = (parent.text or '') + tail
    parent.remove(el)


def _has_media(el):
    return any(_local(d) in MEDIA_TAGS for d in el.iter() if d is not el) or _local(el) in MEDIA_TAGS


def _is_empty(el):
    return not ''.join(el.itertext()).strip() and not _has_media(el)


def _snippet(text):
    text = _norm(text)
    return text if len(text) <= 120 else text[:117] + '...'


def scrub_document(root, matcher, file_name="", allowed_change_ids=None, dry_run=False):
    """Scrub one parsed XHTML tree in place or collect proposed changes.

    Returns a list of change item dicts.
    """
    changes = []
    candidates = []

    # 1. comments and processing instructions
    for node in list(root.iter()):
        if not isinstance(node.tag, str) and node.getparent() is not None and matcher.hit(node.text or ''):
            text = node.text or ''
            candidates.append({
                'type': 'comment',
                'node': node,
                'action': 'comment removed',
                'text': _snippet(text),
                'detail': f'Comment containing promotional text: "{text}"',
                'category': 'HTML Comments'
            })

    # 2. short blocks that are promotion from top to bottom, innermost first
    body = next((e for e in root.iter() if _local(e) == 'body'), root)
    for el in [e for e in body.iter() if _local(e) in LEAF_BLOCKS]:
        if el.getparent() is None or any(_local(d) in BLOCK_TAGS for d in el.iterdescendants()):
            continue
        text = _norm(''.join(el.itertext()))
        hrefs = ' '.join(unquote(d.get('href') or '') for d in el.iter() if _local(d) == 'a')
        if (matcher.promo_block(text) or matcher.promo_block(hrefs) and len(text) <= MAX_BLOCK_CHARS) and not _has_media(el):
            candidates.append({
                'type': 'block',
                'node': el,
                'action': 'block removed',
                'text': _snippet(text),
                'detail': f'Promotional block element (<{_local(el)}>):\n"{text}"',
                'category': 'Content Blocks'
            })

    # 3. hyperlinks pointing at a promotional site
    for a in [e for e in root.iter() if _local(e) == 'a']:
        href = a.get('href') or ''
        if not matcher.hit(unquote(href)) or a.getparent() is None:
            continue
        text = _norm(''.join(a.itertext()))
        if not text or matcher.hit(text) or URLISH.fullmatch(text) or len(text) <= 3:
            action = 'link removed'
            detail = f'Hyperlink removed:\nText: "{text}"\nTarget: {href}'
        else:
            action = 'link unwrapped'
            detail = f'Hyperlink unwrapped (link removed, text preserved):\nText: "{text}"\nTarget: {href}'
        candidates.append({
            'type': 'link',
            'node': a,
            'action': action,
            'text': f'{_snippet(text)} -> {href[:100]}',
            'original_text': text,
            'href': href,
            'detail': detail,
            'category': 'Hyperlinks'
        })

    # 4. leftover mentions inside longer text
    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        for attr in ('text', 'tail'):
            value = getattr(el, attr)
            if value and matcher.hit(value):
                new = matcher.clean_text(value)
                if new != value:
                    candidates.append({
                        'type': 'text',
                        'node': el,
                        'attr': attr,
                        'action': 'text trimmed',
                        'text': _snippet(value),
                        'detail': f'Promotional text trimmed:\nBefore: {value}\nAfter: {new}',
                        'category': 'Text Mentions'
                    })

    for idx, cand in enumerate(candidates):
        change_id = f"{file_name}:{cand['action']}:{idx}"
        item = {
            'file': file_name,
            'action': cand['action'],
            'text': cand['text'],
            'id': change_id,
            'detail': cand['detail'],
            'category': cand['category']
        }

        if dry_run:
            changes.append(item)
            continue

        if allowed_change_ids is not None and change_id not in allowed_change_ids:
            continue

        ctype = cand['type']
        if ctype == 'comment':
            node = cand['node']
            if node.getparent() is not None:
                _remove(node)
                changes.append(item)
        elif ctype == 'block':
            el = cand['node']
            if el.getparent() is not None:
                parent = el.getparent()
                _remove(el)
                while parent is not None and parent is not body and parent.getparent() is not None and _is_empty(parent) \
                        and _local(parent) in BLOCK_TAGS | {'span'}:
                    grand = parent.getparent()
                    _remove(parent)
                    parent = grand
                changes.append(item)
        elif ctype == 'link':
            a = cand['node']
            if a.getparent() is not None:
                text = cand['original_text']
                if not text or matcher.hit(text) or URLISH.fullmatch(text) or len(text) <= 3:
                    if _has_media(a):
                        del a.attrib['href']
                    else:
                        _remove(a)
                else:
                    _unwrap(a)
                changes.append(item)
        elif ctype == 'text':
            el = cand['node']
            attr = cand['attr']
            curr = getattr(el, attr)
            if curr and matcher.hit(curr):
                new = matcher.clean_text(curr)
                if new != curr:
                    setattr(el, attr, new)
                    changes.append(item)

    return changes


def _parse(data):
    parser = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False)
    return etree.fromstring(data, parser)


def _serialize(root, original):
    declaration = original.lstrip().startswith(b'<?xml')
    doctype = etree.ElementTree(_parse(original)).docinfo.doctype
    body = etree.tostring(root, encoding='utf-8', xml_declaration=declaration, doctype=doctype or None)
    return body


def _opf_path(zf):
    try:
        container = _parse(zf.read('META-INF/container.xml'))
        for el in container.iter():
            if _local(el) == 'rootfile' and el.get('full-path'):
                return el.get('full-path')
    except Exception:
        pass
    return next((n for n in zf.namelist() if n.lower().endswith('.opf')), None)


def _resolve(base, href):
    href = unquote(urldefrag(href)[0])
    return posixpath.normpath(posixpath.join(posixpath.dirname(base), href)) if href else base


def _scrub_metadata(opf_root, matcher, changes, opf_name="", allowed_change_ids=None, dry_run=False):
    metadata = next((e for e in opf_root.iter() if _local(e) == 'metadata'), None)
    if metadata is None:
        return
    candidates = []
    for el in list(metadata):
        name = _local(el)
        if name is None:
            continue
        text = el.text or ''
        content = el.get('content') or ''
        if not matcher.hit(text) and not matcher.hit(content):
            continue
        if name in DC_SINGLE or (name == 'meta' and not el.get('refines') and not content.strip()):
            action = 'metadata removed'
            text_val = text or content
            snippet = f'{name}: {_snippet(text_val)}'
            detail = f'Metadata tag <{name}> removed:\n"{text_val}"'
        elif name == 'meta':
            action = 'metadata trimmed'
            snippet = f'meta: {_snippet(content)}'
            detail = f'Metadata <meta> tag content trimmed:\nBefore: {content}\nAfter: {tidy(matcher.clean_text(content))}'
        else:
            action = 'metadata trimmed'
            snippet = f'{name}: {_snippet(text)}'
            detail = f'Metadata tag <{name}> text trimmed:\nBefore: {text}\nAfter: {tidy(matcher.clean_text(text))}'

        candidates.append({
            'el': el,
            'name': name,
            'text': text,
            'content': content,
            'action': action,
            'snippet': snippet,
            'detail': detail
        })

    for idx, cand in enumerate(candidates):
        change_id = f"{opf_name}:{cand['action']}:{idx}"
        item = {
            'file': opf_name,
            'action': cand['action'],
            'text': cand['snippet'],
            'id': change_id,
            'detail': cand['detail'],
            'category': 'Package Metadata'
        }

        if dry_run:
            changes.append(item)
            continue

        if allowed_change_ids is not None and change_id not in allowed_change_ids:
            continue

        el = cand['el']
        name = cand['name']
        if cand['action'] == 'metadata removed':
            changes.append(item)
            _remove(el)
        elif name == 'meta':
            before = cand['content']
            el.set('content', tidy(matcher.clean_text(before)))
            changes.append(item)
        else:
            new = tidy(matcher.clean_text(cand['text']))
            if new:
                changes.append(item)
                el.text = new


def _drop_references(dropped, opf_name, opf_root, extra_docs, changes):
    """Remove spine/manifest/guide/TOC references to deleted pages."""
    ids = set()
    for item in [e for e in opf_root.iter() if _local(e) == 'item']:
        if _resolve(opf_name, item.get('href', '')) in dropped:
            ids.add(item.get('id'))
            _remove(item)
    for ref in [e for e in opf_root.iter() if _local(e) == 'itemref']:
        if ref.get('idref') in ids:
            _remove(ref)
    for ref in [e for e in opf_root.iter() if _local(e) == 'reference']:
        if _resolve(opf_name, ref.get('href', '')) in dropped:
            _remove(ref)
    for name, root in extra_docs.items():
        for el in [e for e in root.iter() if _local(e) in ('navpoint', 'li', 'a')]:
            if el.getparent() is None:
                continue
            tag = _local(el)
            target = None
            if tag == 'navpoint':
                content = next((c for c in el if _local(c) == 'content'), None)
                target = content.get('src') if content is not None else None
            elif tag == 'li':
                a = next((c for c in el if _local(c) == 'a'), None)
                target = a.get('href') if a is not None else None
            else:
                target = el.get('href')
            if target is None or _resolve(name, target) not in dropped:
                continue
            if tag == 'a':
                _unwrap(el)
                continue
            for child in [c for c in el if _local(c) in ('navpoint', 'ol', 'ul')]:
                if tag == 'li' and _local(child) in ('ol', 'ul'):
                    for sub in list(child):
                        el.addprevious(sub)
                else:
                    el.addprevious(child)
            _remove(el)
            changes.append(('toc entry removed', _resolve(name, target)))


def scrub_epub(data, extra_patterns=(), heuristic=True, drop_empty_pages=True, allowed_change_ids=None, dry_run=False, profiles=None, profile=None):
    """Return (new_bytes_or_None, report). None means nothing needed changing."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ValueError('Not a valid EPUB (ZIP) file') from exc
    with zf:
        if 'mimetype' not in zf.namelist() or zf.read('mimetype').strip() != b'application/epub+zip':
            raise ValueError('Not a valid EPUB: missing mimetype entry')
        if sum(i.file_size for i in zf.infolist()) > MAX_TOTAL_BYTES:
            raise ValueError('EPUB is too large to process safely')
        opf_name = _opf_path(zf)
        opf_root = _parse(zf.read(opf_name)) if opf_name else None

        domains = extract_domains_from_opf(opf_root)
        if profile is not None:
            active_profile = profile
        elif profiles is not None:
            active_profile = resolve_profile(domains, profiles)
        else:
            active_profile = {
                'name': 'Global Default',
                'extra_patterns': extra_patterns,
                'heuristic': heuristic,
                'drop_empty_pages': drop_empty_pages
            }

        p_extra = active_profile.get('extra_patterns', extra_patterns)
        if isinstance(p_extra, str):
            p_extra = [line.strip() for line in p_extra.splitlines() if line.strip()]

        p_heuristic = active_profile.get('heuristic', heuristic)
        p_drop = active_profile.get('drop_empty_pages', drop_empty_pages)

        matcher = Matcher(p_extra, p_heuristic)

        manifest, nav_names, ncx_names = {}, set(), set()
        if opf_root is not None:
            for item in opf_root.iter():
                if _local(item) == 'item' and item.get('href'):
                    path = _resolve(opf_name, item.get('href'))
                    manifest[path] = item.get('media-type', '')
                    if 'nav' in (item.get('properties') or '').split():
                        nav_names.add(path)
                    if item.get('media-type') == NCX_TYPE:
                        ncx_names.add(path)
        report = {
            'changes': [],
            'skipped': [],
            'dropped_pages': [],
            'active_profile': active_profile.get('name', 'Global Default'),
            'detected_domains': sorted(domains)
        }
        replacements, trees, empties = {}, {}, []
        for info in zf.infolist():
            name = info.filename
            is_doc = manifest.get(name) in DOC_TYPES or (not manifest and name.lower().endswith(('.xhtml', '.html', '.htm')))
            if not is_doc:
                continue
            raw = zf.read(name)
            try:
                root = _parse(raw)
            except etree.XMLSyntaxError:
                if matcher.hit(raw.decode('utf-8', 'ignore')):
                    report['skipped'].append(f'{name}: not well-formed XML, left unchanged but mentions a blocked site')
                continue
            found = scrub_document(root, matcher, name, allowed_change_ids=allowed_change_ids, dry_run=dry_run)
            if not found:
                continue
            report['changes'] += found
            trees[name] = (root, raw)
            body = next((e for e in root.iter() if _local(e) == 'body'), None)
            if p_drop and body is not None and name not in nav_names and _is_empty(body):
                change_id = f"{name}:page dropped:0"
                cand_item = {
                    'file': name,
                    'action': 'page dropped',
                    'text': name,
                    'id': change_id,
                    'detail': f'Empty page containing only promotional content will be deleted: {name}',
                    'category': 'Dropped Pages'
                }
                if dry_run:
                    empties.append(name)
                    report['changes'].append(cand_item)
                elif allowed_change_ids is None or change_id in allowed_change_ids:
                    empties.append(name)

        dropped = set(empties) if opf_root is not None else set()
        extra_docs = {}
        if dropped:
            for name in nav_names | ncx_names:
                if name not in dropped:
                    extra_docs[name] = trees[name][0] if name in trees else _parse(zf.read(name))
            for name, (root, raw) in trees.items():
                if name not in dropped and name not in extra_docs:
                    extra_docs[name] = root
        meta_changes = []
        if opf_root is not None:
            _scrub_metadata(opf_root, matcher, meta_changes, opf_name=opf_name or '', allowed_change_ids=allowed_change_ids, dry_run=dry_run)
            if dropped and not dry_run:
                _drop_references(dropped, opf_name, opf_root, extra_docs, meta_changes)
        report['changes'] += meta_changes
        report['dropped_pages'] = sorted(dropped)
        comment_hit = bool(zf.comment and matcher.hit(zf.comment.decode('utf-8', 'ignore')))
        if comment_hit:
            change_id = "(zip):archive comment removed:0"
            cand_item = {
                'file': '(zip)',
                'action': 'archive comment removed',
                'text': '',
                'id': change_id,
                'detail': 'ZIP archive comment contains promotional text and will be removed.',
                'category': 'Archive Comment'
            }
            if dry_run:
                report['changes'].append(cand_item)
            elif allowed_change_ids is None or change_id in allowed_change_ids:
                pass

        if dry_run or not report['changes']:
            return None, report

        for name, (root, raw) in trees.items():
            if name in dropped:
                continue
            replacements[name] = _serialize(root, raw)
        if meta_changes and opf_root is not None:
            replacements[opf_name] = etree.tostring(opf_root, encoding='utf-8', xml_declaration=True)
        for name, root in extra_docs.items():
            if name not in trees and name != opf_name and meta_changes:
                replacements[name] = etree.tostring(root, encoding='utf-8', xml_declaration=True)
        out = io.BytesIO()
        with zipfile.ZipFile(out, 'w') as new:
            for info in zf.infolist():
                if info.filename in dropped:
                    continue
                payload = replacements.get(info.filename)
                if payload is None:
                    payload = zf.read(info.filename)
                clone = zipfile.ZipInfo(info.filename, info.date_time)
                clone.compress_type = zipfile.ZIP_STORED if info.filename == 'mimetype' else zipfile.ZIP_DEFLATED
                clone.external_attr = info.external_attr
                new.writestr(clone, payload)
            if zf.comment and not (comment_hit and (allowed_change_ids is None or "(zip):archive comment removed:0" in allowed_change_ids)):
                new.comment = zf.comment
        return out.getvalue(), report


def scrub_files(source, output=None, extra_patterns=(), heuristic=True, drop_empty_pages=True, in_place=False, allowed_change_ids=None, profiles=None):
    """Scrub every .epub under source (file or folder).

    By default cleaned copies go to a separate output folder and originals are never modified.
    With in_place=True each changed file is replaced by its cleaned version (written to a
    temporary file first, then swapped in); unchanged files are not touched.
    """
    source = Path(source)
    if in_place:
        output = source if source.is_dir() else source.parent
    elif output is None:
        raise ValueError('An output folder is required unless replacing originals in place')
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    paths = [source] if source.is_file() else sorted(source.rglob('*.epub'))
    rows = []
    for path in paths:
        row = {'input': str(path)}
        try:
            data, report = scrub_epub(path.read_bytes(), extra_patterns, heuristic, drop_empty_pages, allowed_change_ids=allowed_change_ids, profiles=profiles)
            row.update(report)
            if data is None:
                row['status'] = 'unchanged'
            elif in_place:
                tmp = path.with_name(path.name + '.scrub-tmp')
                tmp.write_bytes(data)
                os.replace(tmp, path)
                row.update(status='replaced', output=str(path))
            else:
                target = output / path.name
                if target.resolve() == path.resolve():
                    raise ValueError('Output folder must differ from the source folder')
                target.write_bytes(data)
                row.update(status='scrubbed', output=str(target))
        except Exception as exc:
            row.update(status='error', error=str(exc))
        rows.append(row)
    report_path = output / 'scrub-report.json'
    report_path.write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding='utf-8')
    return rows, report_path
