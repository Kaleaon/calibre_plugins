"""Metadata-source side of the Story Collection Tagger: answers Calibre's
identify requests with tags only.

The tagger's whole job is working out which tags a book should gain from
its source site and the user's keyword rules (rules.plan_changes). As a
metadata source it does exactly that for the one book being identified: the
book's identifiers and publisher are run through site detection and the
saved rules, and the result is the same book carrying the tags the tagger
would add. It never invents a title or author — results are only returned
when the query already names the book and at least one tag applies.

The companion ZIP (tagger_source/) wraps this module in a Calibre Source.
"""
try:
    from . import rules as rules_mod
except ImportError:  # tests load this file with the folder on sys.path
    import rules as rules_mod

SOURCE_NAME = 'Story Source Tagger'
CAPABILITIES = frozenset({'identify'})
TOUCHED_FIELDS = frozenset({'tags'})


def search(title=None, authors=None, identifiers=None, timeout=30, abort=None, settings=None):
    if not title:
        return []
    settings = settings or {}
    sites = rules_mod.DEFAULT_SITES
    if settings.get('sites'):
        try:
            extra, _errors = rules_mod.parse_sites(settings['sites'])
            sites = rules_mod.merge_sites(rules_mod.DEFAULT_SITES, extra)
        except Exception:
            sites = rules_mod.DEFAULT_SITES
    parsed_rules = []
    if settings.get('rules'):
        try:
            parsed_rules, _errors = rules_mod.parse_rules(settings['rules'])
        except Exception:
            parsed_rules = []
    book = {'id': 0, 'title': title, 'authors': list(authors or []), 'tags': [],
            'identifiers': dict(identifiers or {}), 'publisher': '', 'comments': '', 'series': ''}
    plan = rules_mod.plan_changes(
        [book],
        source_prefix=settings.get('source_prefix', 'Source.'),
        add_source=settings.get('add_source', True),
        series_prefix=settings.get('series_prefix', ''),
        rules=parsed_rules, sites=sites,
        scan_comments=settings.get('scan_comments', False))
    if not plan:
        return []
    return [{
        'title': title,
        'authors': list(authors or []),
        'tags': plan[0]['add'],
        'identifiers': dict(identifiers or {}),
        'score': 1.0,
    }]
