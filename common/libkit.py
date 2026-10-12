"""Calibre-library side of the downloaders: duplicate detection and importing built EPUBs."""
from html import escape
from io import BytesIO


def existing_ids(gui, identifier):
    """Values of the given identifier type (e.g. 'metabods') already present in the current library."""
    db = gui.current_db.new_api
    found = db.all_field_for('identifiers', db.all_book_ids(), {})
    return {idents[identifier] for idents in found.values() if idents and identifier in idents}


def metadata_for(group, title, identifier, publisher):
    from calibre.ebooks.metadata.book.base import Metadata
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

