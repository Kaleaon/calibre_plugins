"""Image classification and promo detection for EPUB books.

Distinguishes between:
- 'cover': Front or back cover image
- 'logo': Publisher or imprint logo on title/copyright page (safe - kept)
- 'ornament': Chapter divider, fleuron, header ornament, or drop cap (safe - kept)
- 'illustration': Story illustration, map, character sketch (safe - kept)
- 'ad': Promotional banner, pirate watermark, crowdfunding badge, or sponsor ad (flagged)
- 'misplaced_image': Promotional image injected into the wrong structural place (flagged)
"""

import io
import os
import re
import struct
from typing import Any, Dict, List, Optional, Tuple


PROMO_IMAGE_PATTERNS = [
    r'oceanofpdf',
    r'pdfdrive',
    r'z-?library',
    r'libgen',
    r'library\s*genesis',
    r'mobilism',
    r'patreon',
    r'discord',
    r'paypal',
    r'ko-?fi',
    r'telegram',
    r'watermark',
    r'advert(?:isement)?',
    r'\bpromo(?:tion)?\b',
    r'\bbanner\b',
    r'\bsponsor(?:ed)?\b',
    r'\bdonate\b',
    r'\bsubscribe\b',
    r'qr[-_]?code',
]

PROMO_IMAGE_RE = re.compile('|'.join(f'(?:{p})' for p in PROMO_IMAGE_PATTERNS), re.I)

PUBLISHER_KEYWORDS = [
    'publish', 'press', 'imprint', 'books', 'house', 'media', 'edition',
    'copyright', 'all rights reserved', 'isbn', 'cataloging', 'printed'
]


def get_image_dimensions(data: bytes, filename: str = '') -> Tuple[Optional[int], Optional[int], str]:
    """Extract (width, height, format) from raw image bytes.

    Uses lightweight pure-Python binary header parsing, with fallback to QImage or PIL if available.
    """
    if not data:
        return None, None, 'unknown'
    if isinstance(data, str):
        data = data.encode('latin1', errors='ignore')
    elif not isinstance(data, (bytes, bytearray)):
        return None, None, 'unknown'

    # 1. PNG
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data) >= 24:
        try:
            w, h = struct.unpack('>II', data[16:24])
            return w, h, 'png'
        except Exception:
            pass

    # 2. GIF
    if (data.startswith(b'GIF87a') or data.startswith(b'GIF89a')) and len(data) >= 10:
        try:
            w, h = struct.unpack('<HH', data[6:10])
            return w, h, 'gif'
        except Exception:
            pass

    # 3. JPEG
    if data.startswith(b'\xff\xd8'):
        try:
            idx = 2
            size = len(data)
            while idx < size - 8:
                marker, length = struct.unpack('>2sH', data[idx:idx + 4])
                idx += 2
                if marker in [
                    b'\xff\xc0', b'\xff\xc1', b'\xff\xc2', b'\xff\xc3',
                    b'\xff\xc5', b'\xff\xc6', b'\xff\xc7', b'\xff\xc9',
                    b'\xff\xca', b'\xff\xcb', b'\xff\xcd', b'\xff\xce', b'\xff\xcf'
                ]:
                    h, w = struct.unpack('>HH', data[idx + 1:idx + 5])
                    return w, h, 'jpeg'
                idx += length
        except Exception:
            pass

    # 4. SVG
    if b'<svg' in data[:500] or filename.lower().endswith('.svg'):
        try:
            header_sample = data[:2000].decode('utf-8', errors='ignore')
            m_w = re.search(r'\bwidth=["\']?(\d+)(?:px)?["\']?', header_sample, re.I)
            m_h = re.search(r'\bheight=["\']?(\d+)(?:px)?["\']?', header_sample, re.I)
            if m_w and m_h:
                return int(m_w.group(1)), int(m_h.group(1)), 'svg'
            m_vb = re.search(r'\bviewBox=["\']?[\d\s,.-]+?[\s,]+([\d.]+)(?:px)?[\s,]+([\d.]+)(?:px)?["\']?', header_sample, re.I)
            if m_vb:
                return int(float(m_vb.group(1))), int(float(m_vb.group(2))), 'svg'
            return 200, 200, 'svg'
        except Exception:
            pass

    # 5. Fallback via QImage if Qt runtime is active
    try:
        from qt.core import QImage
        img = QImage()
        if img.loadFromData(data):
            fmt = filename.split('.')[-1].lower() if '.' in filename else 'unknown'
            return img.width(), img.height(), fmt
    except Exception:
        pass

    # 6. Fallback via PIL if installed
    try:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            return im.width, im.height, im.format.lower() if im.format else 'unknown'
    except Exception:
        pass

    fmt = filename.split('.')[-1].lower() if '.' in filename else 'unknown'
    return None, None, fmt


def get_image_info(data: bytes, filename: str = '') -> Dict[str, Any]:
    """Return dictionary with image metadata: width, height, format, aspect_ratio, size_bytes."""
    w, h, fmt = get_image_dimensions(data, filename)
    aspect = round(w / h, 2) if (w and h and h > 0) else None
    return {
        'width': w,
        'height': h,
        'format': fmt,
        'aspect_ratio': aspect,
        'size_bytes': len(data) if data else 0,
        'filename': filename,
    }


def classify_image_context(
    image_name: Any,
    image_data: Any = None,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    if isinstance(image_name, (bytes, bytearray)) or (image_name is None and isinstance(image_data, str)):
        image_data, image_name = image_name, image_data
    if not isinstance(image_name, str):
        image_name = str(image_name or '')
    """Classifies an image inside an EPUB based on dimensions, page position, surrounding text,
    and markup context.

    Context dictionary can contain:
        - page_file: filename of the XHTML page containing the image
        - page_role: 'cover' | 'titlepage' | 'copyright' | 'toc' | 'chapter' | 'backmatter'
        - surrounding_text: text in the block or immediate vicinity of the image
        - is_linked: whether the image is inside an <a> tag
        - link_href: target URL if linked
        - alt_text: alt or title attribute on the <img> tag
        - frequency: how many times this image is referenced in the EPUB
        - spine_index: index of this page in the spine (0-based)
        - total_spine: total number of spine documents
        - is_cover_manifest: whether this image is marked as cover in OPF manifest/metadata

    Returns dict with:
        - category: 'ad' | 'logo' | 'ornament' | 'illustration' | 'cover' | 'misplaced_image'
        - is_promo: bool
        - confidence: 'high' | 'medium' | 'low'
        - reason: str explanation
        - suggested_action: 'remove_image' | 'remove_page' | 'keep'
        - image_info: dict with width, height, aspect_ratio
    """
    ctx = dict(context or {})
    info = get_image_info(image_data, image_name) if image_data else {
        'width': None, 'height': None, 'format': 'unknown', 'aspect_ratio': None, 'size_bytes': 0, 'filename': image_name
    }

    base_name = os.path.basename(image_name).lower()
    page_file = str(ctx.get('page_file') or '').lower()
    page_role = str(ctx.get('page_role') or '').lower()
    surrounding_text = str(ctx.get('surrounding_text') or '').strip()
    alt_text = str(ctx.get('alt_text') or '').strip()
    is_linked = bool(ctx.get('is_linked', False))
    link_href = str(ctx.get('link_href') or '').strip()
    frequency = int(ctx.get('frequency', 1))
    spine_index = ctx.get('spine_index')
    total_spine = ctx.get('total_spine', 1)
    is_cover_manifest = bool(ctx.get('is_cover_manifest', False))

    w, h, aspect = info.get('width'), info.get('height'), info.get('aspect_ratio')
    all_text = f"{base_name} {alt_text} {link_href} {surrounding_text}".lower()

    # ---------------------------------------------------------------------------------------------
    # 1. Definite Cover Image (Protected)
    # ---------------------------------------------------------------------------------------------
    if is_cover_manifest or 'cover' in base_name or 'cover' in page_file or page_role == 'cover':
        # If it's a cover, verify it's not a pirate watermark pretending to be cover
        if not PROMO_IMAGE_RE.search(base_name) and not (is_linked and PROMO_IMAGE_RE.search(link_href)):
            return {
                'category': 'cover',
                'is_promo': False,
                'confidence': 'high',
                'reason': 'Designated book cover image',
                'suggested_action': 'keep',
                'image_info': info
            }

    # ---------------------------------------------------------------------------------------------
    # 2. Definite Pirate Watermark / Ad Banner by Pattern
    # ---------------------------------------------------------------------------------------------
    hit_match = PROMO_IMAGE_RE.search(all_text)
    if hit_match:
        hit_term = hit_match.group(0)
        return {
            'category': 'ad',
            'is_promo': True,
            'confidence': 'high',
            'reason': f'Contains promotional watermark pattern "{hit_term}" in image or context',
            'suggested_action': 'remove_image',
            'image_info': info
        }

    # ---------------------------------------------------------------------------------------------
    # 3. Linked Images pointing to External Web Domains
    # ---------------------------------------------------------------------------------------------
    if is_linked and link_href:
        # If image links to external promo, discord, telegram, patreon, or tracking domain
        if re.search(r'(?:patreon\.com|discord\.(?:gg|com)|paypal\.me|ko-fi\.com|t\.me|twitter\.com|x\.com)', link_href, re.I):
            return {
                'category': 'ad',
                'is_promo': True,
                'confidence': 'high',
                'reason': f'Image links to crowdfunding or social promotional site: {link_href[:60]}',
                'suggested_action': 'remove_image',
                'image_info': info
            }
        # Web ad banner link
        if re.search(r'https?://', link_href, re.I) and not any(k in all_text for k in PUBLISHER_KEYWORDS):
            if w and h and (aspect and aspect >= 2.5 or (w >= 300 and h <= 120)):
                return {
                    'category': 'ad',
                    'is_promo': True,
                    'confidence': 'high',
                    'reason': f'Banner image ({w}x{h}) linked to external URL: {link_href[:60]}',
                    'suggested_action': 'remove_image',
                    'image_info': info
                }

    # ---------------------------------------------------------------------------------------------
    # 4. Standard Web Ad Banners by Dimension
    # ---------------------------------------------------------------------------------------------
    # Standard banner dimensions: 728x90, 468x60, 320x50, 300x250, 160x600, etc.
    if w and h:
        if aspect and aspect >= 4.0 and w >= 250 and h <= 120:
            # Check if it's not a repeated decorative divider
            if frequency == 1 and not ('divider' in base_name or 'rule' in base_name or 'ornament' in base_name):
                return {
                    'category': 'ad',
                    'is_promo': True,
                    'confidence': 'medium',
                    'reason': f'Standard web banner aspect ratio ({w}x{h}, {aspect}:1)',
                    'suggested_action': 'remove_image',
                    'image_info': info
                }

    # ---------------------------------------------------------------------------------------------
    # 5. Publisher / Imprint Logo (Protected)
    # ---------------------------------------------------------------------------------------------
    # Typically found on title page or copyright page, modest dimensions, publisher text present
    is_frontmatter_page = (
        page_role in ('titlepage', 'copyright') or
        any(k in page_file for k in ('title', 'copyright', 'imprint', 'halftitle', 'edition')) or
        (spine_index is not None and spine_index <= 2)
    )
    has_pub_text = any(k in surrounding_text.lower() for k in PUBLISHER_KEYWORDS)

    if is_frontmatter_page:
        # Typical logo size: 50x50 to 450x250, or mentions logo/imprint/colophon
        if 'logo' in base_name or 'colophon' in base_name or 'imprint' in base_name or has_pub_text:
            return {
                'category': 'logo',
                'is_promo': False,
                'confidence': 'high',
                'reason': 'Publisher or imprint colophon logo on frontmatter page',
                'suggested_action': 'keep',
                'image_info': info
            }
        if w and h and w <= 450 and h <= 250 and (not aspect or (0.5 <= aspect <= 3.5)):
            return {
                'category': 'logo',
                'is_promo': False,
                'confidence': 'medium',
                'reason': f'Small frontmatter image ({w}x{h}) consistent with publisher logo',
                'suggested_action': 'keep',
                'image_info': info
            }

    # ---------------------------------------------------------------------------------------------
    # 6. Decorative Ornament / Divider / Fleuron (Protected)
    # ---------------------------------------------------------------------------------------------
    # Repeated divider across chapters or thin horizontal rule
    if frequency > 1 or any(k in base_name for k in ('divider', 'fleuron', 'ornament', 'flourish', 'rule', 'break', 'line', 'star', 'dot', 'sep')):
        return {
            'category': 'ornament',
            'is_promo': False,
            'confidence': 'high',
            'reason': f'Decorative chapter divider or fleuron (frequency: {frequency})',
            'suggested_action': 'keep',
            'image_info': info
        }
    if (w is not None and h is not None) and ((h <= 30 and w >= 80) or (w <= 60 and h <= 60)):
        return {
            'category': 'ornament',
            'is_promo': False,
            'confidence': 'high',
            'reason': f'Small decorative break or ornament element ({w}x{h})',
            'suggested_action': 'keep',
            'image_info': info
        }

    # ---------------------------------------------------------------------------------------------
    # 7. Standalone Promo / Ad Page in Spine (Misplaced Image)
    # ---------------------------------------------------------------------------------------------
    # A single image document with almost no text placed at the beginning or end of book
    is_spine_boundary = (spine_index is not None and (spine_index <= 2 or spine_index >= total_spine - 2))
    if is_spine_boundary and page_role not in ('cover', 'titlepage', 'copyright', 'toc'):
        if len(surrounding_text) < 100:
            if any(k in all_text for k in ('download', 'visit', 'free', 'pdf', 'site', 'website', 'join')):
                return {
                    'category': 'misplaced_image',
                    'is_promo': True,
                    'confidence': 'high',
                    'reason': f'Standalone promo image page at spine boundary ({page_file})',
                    'suggested_action': 'remove_page',
                    'image_info': info
                }

    # ---------------------------------------------------------------------------------------------
    # 8. Regular Story Illustration (Protected)
    # ---------------------------------------------------------------------------------------------
    # Embedded inside chapter story text or substantial narrative
    if len(surrounding_text) > 300 or page_role == 'chapter' or 'illus' in base_name or 'figure' in base_name or 'map' in base_name:
        return {
            'category': 'illustration',
            'is_promo': False,
            'confidence': 'medium',
            'reason': 'Story illustration or narrative figure in chapter content',
            'suggested_action': 'keep',
            'image_info': info
        }

    # Default: safe illustration if ambiguous
    return {
        'category': 'illustration',
        'is_promo': False,
        'confidence': 'low',
        'reason': 'Standard internal image without promotional indicators',
        'suggested_action': 'keep',
        'image_info': info
    }
