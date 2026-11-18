"""Metadata-source side of the EPUB Promo Scrubber: answers Calibre's
identify requests with a cleaned title.

Books downloaded from promo-stamping sites often carry the site's name in
the title itself ("Some Book - OceanofPDF.com", "Some Book (PDFDrive)").
The scrubber's Matcher knows those marks; as a metadata source it applies
Matcher.clean_text + tidy to the query title and, when something was
actually removed, returns the same book under its cleaned title. It has no
external catalogue to consult, so it never invents authors or other fields,
and returns nothing when the title is already clean.

The companion ZIP (scrubber_source/) wraps this module in a Calibre Source.
"""
try:
    from . import core
except ImportError:  # tests load this file directly with the folder on sys.path
    import core

SOURCE_NAME = 'Promo Scrubber'
CAPABILITIES = frozenset({'identify'})
TOUCHED_FIELDS = frozenset({'title'})


def clean_title(title, extra_patterns=(), heuristic=True):
    """The title with promo-site marks removed, or '' when nothing changed."""
    if not title:
        return ''
    matcher = core.Matcher(extra_patterns, heuristic)
    cleaned = core.tidy(matcher.clean_text(title))
    return cleaned if cleaned and cleaned != title else ''


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, settings=None):
    settings = settings or {}
    extra = settings.get('extra_patterns') or ()
    if isinstance(extra, str):
        extra = [line for line in extra.splitlines() if line.strip()]
    cleaned = clean_title(title or '', extra, settings.get('heuristic', True))
    if not cleaned:
        return []
    return [{
        'title': cleaned,
        'authors': list(authors or []),
        'identifiers': dict(identifiers or {}),
        'score': 1.0,
    }]
