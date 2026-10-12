"""Calibre-library side of the downloaders: duplicate detection and importing built EPUBs."""
import re
from html import escape
from io import BytesIO

try:
    from .storykit import build_epub, extract_epub_story, sort_by_posting_order
except (ImportError, ValueError):
    try:
        from storykit import build_epub, extract_epub_story, sort_by_posting_order
    except ImportError:
        from common.storykit import build_epub, extract_epub_story, sort_by_posting_order


def existing_ids(gui, identifier):
    """Values of the given identifier type (e.g. 'metabods') already present in the current library."""
    db = gui.current_db.new_api
    found = db.all_field_for('identifiers', db.all_book_ids(), {})
    return {idents[identifier] for idents in found.values() if idents and identifier in idents}


def metadata_for(group, title, identifier, publisher):
    try:
        from calibre.ebooks.metadata.book.base import Metadata
    except ImportError:
        class Metadata:
            def __init__(self, title, authors):
                self.title = title
                self.authors = authors
                self.publisher = None
                self.languages = []
                self.tags = []
                self.comments = None
                self.series = None
                self.series_index = None
                self.pubdate = None
                self.identifiers = {}

            def set_identifier(self, k, v):
                self.identifiers[k] = v

    group = sort_by_posting_order(group)
    combined = len(group) > 1
    authors = list(dict.fromkeys(s['author'] for s in group))
    mi = Metadata(title or (group[0]['title'] if not combined else f"{group[0]['title']} and {len(group) - 1} more"), authors)
    mi.publisher = publisher
    mi.languages = ['eng']
    mi.tags = list(dict.fromkeys(t for s in group for t in s.get('tags', []) + s.get('categories', [])))
    if combined:
        mi.comments = '<ol>' + ''.join(f"<li>{escape(s['title'])} — {escape(s['author'])}</li>" for s in group) + '</ol>'
    else:
        story = group[0]
        if story.get('summary'):
            mi.comments = f"<p>{escape(story['summary'])}</p>"
        mi.set_identifier(identifier, story['id'])
        if story.get('series'):
            mi.series = story['series']
            mi.series_index = story.get('series_index') or 1.0
        if story.get('pubdate'):
            try:
                from calibre.utils.date import parse_date
                mi.pubdate = parse_date(story['pubdate'], assume_utc=True)
            except Exception:
                pass
    return mi


def import_stories(gui, stories, combine, title, build_epub, identifier, publisher):
    """Build one EPUB per story (or one omnibus) and add them to the current library. Returns (added, errors)."""
    if not stories:
        return 0, []
    db = gui.current_db.new_api
    if combine:
        stories = sort_by_posting_order(stories)
    groups = [stories] if combine else [[s] for s in stories]
    added, errors = 0, []
    for group in groups:
        try:
            book_title = (title or None) if combine else None
            data = build_epub(group, title=book_title)
            ids, _ = db.add_books([(metadata_for(group, book_title, identifier, publisher), {'EPUB': BytesIO(data)})])
            added += len(ids)
        except Exception as exc:
            errors.append(f"{', '.join(s['id'] for s in group)}: {exc}")
    if added:
        gui.library_view.model().refresh()
        gui.tags_view.recount()
    return added, errors


def add_prebuilt(gui, items):
    """Add already-finished EPUBs. items: [(Metadata, epub bytes)]. Returns (added, errors); covers come from the EPUB."""
    if not items:
        return 0, []
    db = gui.current_db.new_api
    added, errors = 0, []
    for mi, data in items:
        try:
            ids, _ = db.add_books([(mi, {'EPUB': BytesIO(data)})])
            added += len(ids)
            try:  # FanFicFare can embed a cover; Calibre does not pick it up from add_books
                from calibre.ebooks.metadata.meta import get_metadata
                cover = getattr(get_metadata(BytesIO(data), stream_type='epub'), 'cover_data', None)
                if ids and cover and cover[1]:
                    db.set_cover({ids[0]: cover[1]})
            except Exception:
                pass
        except Exception as exc:
            errors.append(f'{mi.title}: {exc}')
    if added:
        gui.library_view.model().refresh()
        gui.tags_view.recount()
    return added, errors


def find_book(gui, identifier, value):
    """Id of the first library book carrying identifier:value, or None."""
    db = gui.current_db.new_api
    for book_id, idents in db.all_field_for('identifiers', db.all_book_ids(), {}).items():
        if idents and idents.get(identifier) == value:
            return book_id
    return None


def upsert_epub(gui, mi, data, identifier, value):
    """Create the book, or replace the EPUB of the one already carrying identifier:value (and refresh its description).
    Returns (book_id, created)."""
    db = gui.current_db.new_api
    book_id = find_book(gui, identifier, value)
    if book_id is None:
        ids, _ = db.add_books([(mi, {'EPUB': BytesIO(data)})])
        if not ids:
            raise ValueError('Calibre did not add the book')
        created = True
        book_id = ids[0]
    else:
        if not db.add_format(book_id, 'EPUB', BytesIO(data), replace=True):
            raise ValueError('Calibre did not save the updated EPUB')
        if mi.comments:
            db.set_field('comments', {book_id: mi.comments})
        created = False
    gui.library_view.model().refresh_ids([book_id])
    gui.tags_view.recount()
    return book_id, created


def merge_library_books(gui, book_ids, title=None, author=None, publisher=None):
    """Merge selected library books into a single omnibus EPUB.
    Returns (added_book_id, errors).
    """
    if not book_ids:
        return None, ['No books selected']
    try:
        from .storykit import build_epub, extract_epub_story
    except (ImportError, ValueError):
        try:
            from storykit import build_epub, extract_epub_story
        except ImportError:
            from common.storykit import build_epub, extract_epub_story

    db = gui.current_db.new_api
    stories = []
    errors = []
    for book_id in book_ids:
        try:
            epub_bytes = db.format(book_id, 'EPUB')
            if not epub_bytes:
                epub_bytes = db.format(book_id, 'ORIGINAL_EPUB')
            if not epub_bytes:
                errors.append(f'Book ID {book_id} has no EPUB format')
                continue
            mi = db.get_metadata(book_id)
            story = extract_epub_story(epub_bytes)
            if mi and mi.title:
                story['title'] = mi.title
            if mi and mi.authors:
                story['author'] = ' & '.join(mi.authors)
            stories.append(story)
        except Exception as exc:
            errors.append(f'Book ID {book_id}: {exc}')

    if not stories:
        return None, errors or ['No valid EPUB formats found among selected books']

    stories = sort_by_posting_order(stories)

    try:
        data = build_epub(stories, title=title, author=author, publisher=publisher or 'Merged Omnibus')
        book_title = title or (f"{stories[0]['title']} and {len(stories) - 1} more" if len(stories) > 1 else stories[0]['title'])
        authors = [a.strip() for a in (author or ' & '.join(dict.fromkeys(s['author'] for s in stories))).split('&')]

        try:
            from calibre.ebooks.metadata.book.base import Metadata
        except ImportError:
            class Metadata:
                def __init__(self, title, authors):
                    self.title = title
                    self.authors = authors
                    self.publisher = None
                    self.languages = []
                    self.tags = []
                    self.comments = None

        mi = Metadata(book_title, authors)
        mi.publisher = publisher or 'Merged Omnibus'
        mi.languages = ['eng']
        mi.tags = list(dict.fromkeys(t for s in stories for t in s.get('tags', [])))
        mi.comments = '<ol>' + ''.join(f"<li>{escape(s['title'])} — {escape(s['author'])}</li>" for s in stories) + '</ol>'

        ids, _ = db.add_books([(mi, {'EPUB': BytesIO(data)})])
        if ids:
            gui.library_view.model().refresh()
            gui.tags_view.recount()
            return ids[0], errors
        else:
            errors.append('Calibre did not add the merged book')
            return None, errors
    except Exception as exc:
        errors.append(f'Failed to build merged EPUB: {exc}')
        return None, errors


def find_nifty_books(gui, book_ids=None):
    """Find library books that carry a nifty: identifier and have EPUB format.
    Returns list of dicts: [{'book_id': ..., 'mi': ..., 'nifty_id': ..., 'story': ...}].
    """
    db = gui.current_db.new_api
    all_idents = db.all_field_for('identifiers', db.all_book_ids(), {})
    if book_ids:
        target_ids = [b_id for b_id in book_ids if all_idents.get(b_id) and 'nifty' in all_idents[b_id]]
    else:
        target_ids = [b_id for b_id, idents in all_idents.items() if idents and 'nifty' in idents]

    books = []
    for b_id in target_ids:
        nifty_id = all_idents[b_id]['nifty']
        epub_bytes = db.format(b_id, 'EPUB')
        if not epub_bytes:
            epub_bytes = db.format(b_id, 'ORIGINAL_EPUB')
        if not epub_bytes:
            continue
        try:
            mi = db.get_metadata(b_id)
            story = extract_epub_story(epub_bytes)
            if mi and getattr(mi, 'title', None):
                story['title'] = mi.title
            if mi and getattr(mi, 'authors', None):
                story['author'] = ' & '.join(mi.authors)
            if mi and getattr(mi, 'series', None):
                story['series'] = mi.series
            if mi and getattr(mi, 'series_index', None):
                story['series_index'] = mi.series_index
            if mi and getattr(mi, 'pubdate', None):
                try:
                    story['pubdate'] = mi.pubdate.date().isoformat()
                except Exception:
                    pass
            story['book_id'] = b_id
            story['nifty_id'] = nifty_id
            if 'id' not in story or not story['id']:
                story['id'] = nifty_id
            books.append({'book_id': b_id, 'mi': mi, 'nifty_id': nifty_id, 'story': story})
        except Exception:
            continue
    return books


def group_related_nifty_stories(nifty_books):
    """Group list of nifty book entries into related story sets for omnibus consolidation."""
    groups = []
    used_bids = set()

    # Step 1: Explicit series name
    by_series = {}
    for b in nifty_books:
        series = b['story'].get('series') or (getattr(b['mi'], 'series', None) if b.get('mi') else None)
        if series:
            author = b['story'].get('author', '')
            by_series.setdefault((author.casefold(), series.casefold()), (series, []))[1].append(b)

    for (author_cf, series_cf), (series_name, members) in by_series.items():
        if len(members) >= 2:
            groups.append({
                'title': series_name,
                'series': series_name,
                'books': members,
                'stories': [m['story'] for m in members]
            })
            for m in members:
                used_bids.add(m['book_id'])

    # Step 2: Nifty path prefix (e.g. gay/college/saga from gay/college/saga-1 and gay/college/saga-2)
    by_prefix = {}
    for b in nifty_books:
        if b['book_id'] in used_bids:
            continue
        nid = b['nifty_id']
        parts = nid.strip('/').split('/')
        if len(parts) >= 3:
            folder = '/'.join(parts[:-1])
            filename = parts[-1]
            m = re.match(r'^(.*?)[-_]?\d+$', filename)
            prefix = m.group(1) if m and m.group(1) else filename
            prefix_path = f"{folder}/{prefix}"
            by_prefix.setdefault(prefix_path, []).append(b)
        else:
            by_prefix.setdefault(nid, []).append(b)

    for prefix_path, members in by_prefix.items():
        if len(members) >= 2:
            folder_parts = prefix_path.split('/')
            series_name = re.sub(r'[-_]+', ' ', folder_parts[-1]).strip().title()
            groups.append({
                'title': series_name,
                'series': series_name,
                'books': members,
                'stories': [m['story'] for m in members]
            })
            for m in members:
                used_bids.add(m['book_id'])

    # Step 3: Remaining books by author using suggest_series
    remaining = [b for b in nifty_books if b['book_id'] not in used_bids]
    by_author = {}
    for b in remaining:
        by_author.setdefault(b['story'].get('author', 'Unknown').casefold(), []).append(b)

    for author_cf, members in by_author.items():
        if len(members) >= 2:
            st_list = [{'title': b['story']['title'], 'path': b['nifty_id'], 'dir': False, '_b': b} for b in members]
            try:
                try:
                    from nifty_downloader import core as ncore
                except ImportError:
                    from calibre_plugins.nifty_downloader import core as ncore
            except ImportError:
                import nifty.core as ncore
            series_sug, _ = ncore.suggest_series(st_list)
            for sug in series_sug:
                sug_books = [st['_b'] for st in sug['stories'] if '_b' in st]
                if len(sug_books) >= 2:
                    groups.append({
                        'title': sug['name'],
                        'series': sug['name'],
                        'books': sug_books,
                        'stories': [m['story'] for m in sug_books]
                    })
                    for m in sug_books:
                        used_bids.add(m['book_id'])

    return groups


def consolidate_nifty_omnibuses(gui, book_ids=None):
    """Scan Nifty books in library, group related ones, and consolidate into posting-order omnibuses.
    Returns (added_book_ids, errors).
    """
    nifty_books = find_nifty_books(gui, book_ids)
    if not nifty_books:
        return [], ['No Nifty books found in library.']

    groups = group_related_nifty_stories(nifty_books)
    if not groups:
        return [], ['No related Nifty stories found to combine.']

    publisher = 'Nifty'

    db = gui.current_db.new_api
    added_ids = []
    errors = []

    for grp in groups:
        try:
            stories = grp['stories']
            sorted_stories = sort_by_posting_order(stories)
            title = grp['title']

            data = build_epub(sorted_stories, title=title, publisher=publisher)

            mi = metadata_for(sorted_stories, title, 'nifty', publisher)
            if grp.get('series'):
                mi.series = grp['series']
                mi.series_index = 1.0

            common_id = sorted_stories[0].get('nifty_id', sorted_stories[0]['id'])
            parts = common_id.strip('/').split('/')
            if len(parts) >= 3:
                m = re.match(r'^(.*?)[-_]?\d+$', parts[-1])
                prefix = m.group(1) if m and m.group(1) else parts[-1]
                omnibus_id = '/'.join(parts[:-1]) + '/' + prefix
            else:
                omnibus_id = common_id

            mi.set_identifier('nifty', omnibus_id)

            omnibus_b_id, created = upsert_epub(gui, mi, data, 'nifty', omnibus_id)
            added_ids.append(omnibus_b_id)

            old_bids = [m['book_id'] for m in grp['books'] if m['book_id'] != omnibus_b_id]
            if old_bids:
                db.delete_books(old_bids)

        except Exception as exc:
            errors.append(f"Failed to consolidate omnibus '{grp.get('title')}': {exc}")

    if added_ids:
        gui.library_view.model().refresh()
        gui.tags_view.recount()

    return added_ids, errors

