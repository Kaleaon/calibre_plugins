"""Automated library scanner for EPUB Promo Scrubber.

Scans books across the Calibre library (or external folders) to discover:
- Promotional text, pirate watermarks, and sponsor notes
- Promotional links and redirects
- Image ads, banners, and watermarks (differentiating publisher logos, ornaments, and covers)
- Misplaced pages (e.g. injected pirate ad pages before chapter 1 or after the epilogue)
- Promotional traces in Calibre metadata fields
"""

import json
import logging
import os
import time
from io import BytesIO
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

try:
    from calibre_plugins.epub_promo_scrubber.core import (
        Matcher,
        scrub_epub,
        tidy,
    )
    from calibre_plugins.epub_promo_scrubber.profiles import (
        extract_domains_from_metadata,
        get_profiles_from_prefs,
        resolve_profile,
    )
except ImportError:
    try:
        from .core import Matcher, scrub_epub, tidy
        from .profiles import extract_domains_from_metadata, get_profiles_from_prefs, resolve_profile
    except ImportError:
        from core import Matcher, scrub_epub, tidy
        from profiles import extract_domains_from_metadata, get_profiles_from_prefs, resolve_profile

logger = logging.getLogger('scrubber.scanner')


class LibraryScanner:
    """Manages automated scanning of books across the Calibre library."""

    def __init__(self, db, prefs=None, profiles=None):
        self.db = db
        self.prefs = prefs or {}
        self.profiles = profiles or get_profiles_from_prefs(self.prefs)

    def scan_book(self, book_id: int) -> Optional[Dict[str, Any]]:
        """Scans a single book by ID in Calibre and returns structured findings, or None if clean."""
        mi = self.db.get_metadata(book_id)
        if not mi:
            return None

        title = mi.title or f"Book {book_id}"
        authors = list(mi.authors or [])
        doms = extract_domains_from_metadata(mi)
        active_prof = resolve_profile(doms, self.profiles)
        p_extra = active_prof.get('extra_patterns', '')
        if isinstance(p_extra, str):
            p_extra = [line.strip() for line in p_extra.splitlines() if line.strip()]
        p_heuristic = active_prof.get('heuristic', True)
        p_drop = active_prof.get('drop_empty_pages', True)

        findings = []
        raw_epub = self.db.format(book_id, 'EPUB')

        image_stats = {'covers': 0, 'logos': 0, 'ornaments': 0, 'illustrations': 0, 'ads': 0}
        epub_report = None

        if raw_epub:
            try:
                _, epub_report = scrub_epub(
                    raw_epub,
                    extra_patterns=p_extra,
                    heuristic=p_heuristic,
                    drop_empty_pages=p_drop,
                    dry_run=True,
                    profiles=self.profiles
                )
                findings.extend(epub_report.get('changes', []))
                image_stats = epub_report.get('image_stats', image_stats)
            except Exception as exc:
                logger.warning("Error scanning EPUB for book %s: %s", book_id, exc)

        # Calibre metadata fields check
        field_matcher = Matcher(p_extra, p_heuristic)
        meta_changes = self._scan_metadata_fields(book_id, mi, field_matcher)
        findings.extend(meta_changes)

        if not findings:
            return None

        # Categorize findings
        ad_images = [f for f in findings if f.get('category') == 'Promotional Images & Ads']
        promo_blocks = [f for f in findings if f.get('category') in ('Content Blocks', 'HTML Comments')]
        dropped_pages = [f for f in findings if f.get('category') == 'Dropped Pages']
        hyperlinks = [f for f in findings if f.get('category') == 'Hyperlinks']
        text_mentions = [f for f in findings if f.get('category') == 'Text Mentions']
        metadata_fields = [f for f in findings if f.get('category') == 'Calibre Metadata Fields']

        return {
            'book_id': book_id,
            'title': title,
            'authors': authors,
            'active_profile': active_prof.get('name', 'Global Default'),
            'total_findings': len(findings),
            'findings': findings,
            'categories': {
                'ads_and_banners': len(ad_images),
                'dropped_pages': len(dropped_pages),
                'content_blocks': len(promo_blocks),
                'hyperlinks': len(hyperlinks),
                'text_mentions': len(text_mentions),
                'metadata_fields': len(metadata_fields),
            },
            'image_stats': image_stats,
            'has_ads': bool(ad_images),
            'has_dropped_pages': bool(dropped_pages),
            'epub_report': epub_report,
        }

    def get_image_data(self, book_id: int, image_href: str) -> Optional[bytes]:
        """Extracts the raw image bytes for a given image path from the book's EPUB."""
        try:
            raw_epub = self.db.format(book_id, 'EPUB')
            if not raw_epub:
                return None
            import zipfile
            with zipfile.ZipFile(BytesIO(raw_epub)) as zf:
                clean_href = image_href.split('#')[0]
                if clean_href in zf.namelist():
                    return zf.read(clean_href)
                target_base = os.path.basename(clean_href).lower()
                for name in zf.namelist():
                    if os.path.basename(name).lower() == target_base:
                        return zf.read(name)
        except Exception as exc:
            logger.debug("Failed extracting image %s from book %s: %s", image_href, book_id, exc)
        return None

    def _scan_metadata_fields(self, book_id: int, mi: Any, matcher: Matcher) -> List[Dict[str, Any]]:
        changes = []
        for field, value in (('title', mi.title), ('publisher', mi.publisher), ('comments', mi.comments)):
            if value and matcher.hit(value):
                new_val = tidy(matcher.clean_text(value))
                if field == 'title' and not new_val:
                    continue
                change_id = f"book_{book_id}:field_{field}"
                changes.append({
                    'file': f'Metadata: {field}',
                    'action': 'calibre metadata trimmed',
                    'text': value[:80] + ('...' if len(value) > 80 else ''),
                    'id': change_id,
                    'detail': f'Calibre metadata field "{field}" trimmed:\nBefore: {value}\nAfter: {new_val or "(empty)"}',
                    'category': 'Calibre Metadata Fields'
                })
        return changes

    def scan_library(
        self,
        book_ids: Optional[List[int]] = None,
        on_progress: Optional[Callable[[int, int, int, str, int], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """Scans all requested books (or all books in library).

        Returns comprehensive library scan report dictionary.
        """
        all_ids = sorted(book_ids if book_ids is not None else list(self.db.all_book_ids()))
        total = len(all_ids)
        flagged_books = []
        clean_count = 0
        total_findings = 0
        total_ad_images = 0
        total_dropped_pages = 0

        start_time = time.time()

        for idx, book_id in enumerate(all_ids):
            if should_cancel and should_cancel():
                break

            result = self.scan_book(book_id)
            if result:
                flagged_books.append(result)
                total_findings += result['total_findings']
                total_ad_images += result['categories']['ads_and_banners']
                total_dropped_pages += result['categories']['dropped_pages']
                count_for_book = result['total_findings']
            else:
                clean_count += 1
                count_for_book = 0

            if on_progress:
                mi = self.db.get_metadata(book_id)
                t = mi.title if mi else f"Book {book_id}"
                on_progress(idx + 1, total, book_id, t, count_for_book)

        duration = round(time.time() - start_time, 2)

        return {
            'timestamp': time.time(),
            'total_scanned': len(flagged_books) + clean_count,
            'clean_count': clean_count,
            'flagged_count': len(flagged_books),
            'total_findings': total_findings,
            'total_ad_images': total_ad_images,
            'total_dropped_pages': total_dropped_pages,
            'duration_seconds': duration,
            'books': flagged_books,
        }

    def apply_clean(
        self,
        scan_results: List[Dict[str, Any]],
        allowed_change_ids: Optional[Set[str]] = None,
        backup: bool = True,
        on_progress: Optional[Callable[[int, int, str], None]] = None,
        should_cancel: Optional[Callable[[], bool]] = None
    ) -> Dict[str, Any]:
        """Applies scrubbing to books with approved findings."""
        cleaned_books = []
        errors = []
        total = len(scan_results)

        for idx, res in enumerate(scan_results):
            if should_cancel and should_cancel():
                break

            book_id = res['book_id']
            title = res['title']
            if on_progress:
                on_progress(idx + 1, total, title)

            try:
                mi = self.db.get_metadata(book_id)
                doms = extract_domains_from_metadata(mi)
                active_prof = resolve_profile(doms, self.profiles)
                p_extra = active_prof.get('extra_patterns', '')
                if isinstance(p_extra, str):
                    p_extra = [line.strip() for line in p_extra.splitlines() if line.strip()]
                p_heuristic = active_prof.get('heuristic', True)
                p_drop = active_prof.get('drop_empty_pages', True)

                book_allowed = None
                if allowed_change_ids is not None:
                    book_finding_ids = {f['id'] for f in res.get('findings', [])}
                    book_allowed = book_finding_ids.intersection(allowed_change_ids)
                    if not book_allowed:
                        continue

                data = self.db.format(book_id, 'EPUB')
                book_changed = False
                if data:
                    new_bytes, report = scrub_epub(
                        data,
                        extra_patterns=p_extra,
                        heuristic=p_heuristic,
                        drop_empty_pages=p_drop,
                        allowed_change_ids=book_allowed,
                        profiles=self.profiles
                    )
                    if new_bytes is not None:
                        if backup:
                            self.db.save_original_format(book_id, 'EPUB')
                        elif 'ORIGINAL_EPUB' in (self.db.formats(book_id) or ()):
                            self.db.remove_formats({book_id: {'ORIGINAL_EPUB'}})
                        if not self.db.add_format(book_id, 'EPUB', BytesIO(new_bytes), replace=True):
                            raise ValueError('Calibre could not save the scrubbed EPUB')
                        book_changed = True

                # Clean metadata fields
                field_matcher = Matcher(p_extra, p_heuristic)
                for field, value in (('title', mi.title), ('publisher', mi.publisher), ('comments', mi.comments)):
                    if value and field_matcher.hit(value):
                        cid = f"book_{book_id}:field_{field}"
                        if book_allowed is None or cid in book_allowed:
                            new_val = tidy(field_matcher.clean_text(value))
                            if field == 'title' and not new_val:
                                continue
                            self.db.set_field(field, {book_id: new_val or None})
                            book_changed = True

                if book_changed:
                    cleaned_books.append(book_id)
            except Exception as exc:
                errors.append(f"Book {book_id} ('{title}'): {exc}")
                logger.error("Failed scrubbing book %s: %s", book_id, exc)

        return {
            'cleaned_count': len(cleaned_books),
            'cleaned_book_ids': cleaned_books,
            'errors': errors,
        }
