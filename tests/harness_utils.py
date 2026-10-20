"""Shared test database and GUI harness helpers."""

from types import SimpleNamespace


class BaseMockDb:
    """Base mock database for Calibre library API test doubles."""

    def __init__(self, books=None):
        self.books = books if books is not None else {}
        self.writes = []
        self.covers = {}
        self.identifiers = {}
        self.paths = {}
        self.added = []

    def all_book_ids(self):
        return set(self.books)

    def has_id(self, book_id):
        return book_id in self.books

    def get_metadata(self, book_id, get_cover=False):
        if book_id not in self.books:
            return None
        b = self.books[book_id]
        if hasattr(b, 'title') and not isinstance(b, dict):
            return b
        identifiers = dict(b.get('identifiers', self.identifiers.get(book_id, {})))
        publisher = b.get('publisher', '')
        pubdate = b.get('pubdate', None)
        fields = {k: v for k, v in b.items() if k not in ('formats', 'path', 'has_cover')}
        fields['publisher'] = publisher
        fields['identifiers'] = identifiers
        if 'pubdate' not in fields:
            fields['pubdate'] = pubdate
        return SimpleNamespace(**fields)

    def field_for(self, name, book_id):
        if book_id in self.books and isinstance(self.books[book_id], dict):
            if name in self.books[book_id]:
                return self.books[book_id][name]
            if name == 'has_cover':
                return self.books[book_id].get('has_cover', False)
        return dict(self.identifiers.get(book_id, {}))

    def all_field_for(self, name, ids, default=None):
        res = {}
        for i in ids:
            if i in self.books:
                b = self.books[i]
                if name == 'formats' and isinstance(b, dict) and 'formats' in b:
                    res[i] = tuple(b['formats'])
                elif isinstance(b, dict) and 'mi' in b:
                    res[i] = dict(b['mi'].ids)
                elif isinstance(b, dict):
                    field_key = {'authors': 'authors', 'title': 'title', 'series': 'series'}.get(name, name)
                    if field_key in b:
                        res[i] = b[field_key]
        return res

    def formats(self, book_id):
        if book_id in self.books and isinstance(self.books[book_id], dict) and 'formats' in self.books[book_id]:
            return list(self.books[book_id]['formats'])
        return ['EPUB']

    def format_abspath(self, book_id, fmt):
        if book_id in self.books and isinstance(self.books[book_id], dict) and 'path' in self.books[book_id]:
            return self.books[book_id].get('path') or None
        return self.paths.get(book_id, f'/lib/{book_id}.epub')

    def cover(self, book_id):
        if book_id in self.covers:
            return self.covers.get(book_id)
        cover_bytes = getattr(self, 'cover_bytes', None)
        return cover_bytes if book_id == 1 else None

    def remove_cover(self, book_ids):
        for book_id in book_ids:
            self.covers.pop(book_id, None)

    def set_cover(self, mapping):
        for book_id, data in mapping.items():
            if data is None:
                self.covers.pop(book_id, None)
            else:
                self.covers[book_id] = data

    def set_field(self, name, mapping):
        self.writes.append((name, dict(mapping)))

    def add_books(self, items, **kwargs):
        ids = []
        for mi, formats in items:
            new_id = max(self.books, default=0) + 1
            self.books[new_id] = dict(
                title=getattr(mi, 'title', ''),
                authors=list(getattr(mi, 'authors', [])),
                series=getattr(mi, 'series', ''),
                series_index=getattr(mi, 'series_index', 1.0),
                tags=list(getattr(mi, 'tags', [])),
                identifiers=dict(getattr(mi, 'ids', {})),
                formats=[f for f in formats],
            )
            self.added.append((mi, formats))
            ids.append(new_id)
        return ids, []


def create_test_gui(db):
    """Creates a mock Calibre GUI object wrapping a database for integration test harnesses."""
    return SimpleNamespace(
        current_db=db,
        library_view=SimpleNamespace(model=lambda: SimpleNamespace(refresh=lambda: None)),
        tags_view=SimpleNamespace(recount=lambda: None),
    )
