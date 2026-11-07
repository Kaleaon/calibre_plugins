"""Shared OPML 2.0 and JSON serialization engine for Calibre plugin lists.

Provides export and import utilities for Nifty categories and Metabods tags,
supporting auto-merge, skip existing, and overwrite strategies.
Uses standard Python library (xml.etree.ElementTree and json).
"""

import json
import os
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime
from pathlib import Path

STRATEGY_MERGE = "merge"
STRATEGY_SKIP = "skip"
STRATEGY_OVERWRITE = "overwrite"

VALID_STRATEGIES = {
    "merge": STRATEGY_MERGE,
    "auto_merge": STRATEGY_MERGE,
    "auto-merge": STRATEGY_MERGE,
    "skip": STRATEGY_SKIP,
    "skip_existing": STRATEGY_SKIP,
    "skip-existing": STRATEGY_SKIP,
    "overwrite": STRATEGY_OVERWRITE,
    "overwrite_existing": STRATEGY_OVERWRITE,
    "overwrite-existing": STRATEGY_OVERWRITE,
    "replace": STRATEGY_OVERWRITE,
}


def normalize_strategy(strategy):
    """Normalize a strategy string into 'merge', 'skip', or 'overwrite'."""
    key = str(strategy or "merge").lower().strip()
    if key not in VALID_STRATEGIES:
        raise ValueError(
            f"Unknown import strategy '{strategy}'. Expected one of: merge, skip, overwrite."
        )
    return VALID_STRATEGIES[key]


def infer_item_type(items, explicit_type=None):
    """Infer item type ('categories' or 'tags') from items or explicit hint."""
    if explicit_type:
        exp = str(explicit_type).lower().strip()
        if "cat" in exp or "nifty" in exp:
            return "categories"
        if "tag" in exp or "metabods" in exp:
            return "tags"
        return exp

    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            try:
                int(item[0])
                return "tags"
            except (ValueError, TypeError):
                pass
        elif isinstance(item, dict):
            if "id" in item or "tag_id" in item:
                return "tags"
            if "path" in item:
                return "categories"
        elif isinstance(item, str):
            return "categories"
    return "categories"


# -----------------------------------------------------------------------------
# Export functions
# -----------------------------------------------------------------------------


def export_list_to_opml(
    items, target=None, title="Calibre Plugin Export", item_type=None
):
    """Export items (Nifty categories or Metabods tags) to an OPML 2.0 XML document.

    :param items: list of category path strings or tag tuples [id, name] / dicts
    :param target: optional file path (str, Path) or file-like object
    :param title: title for the OPML document
    :param item_type: 'categories', 'tags', 'nifty', 'metabods', or None to infer
    :return: XML string
    """
    inferred_type = infer_item_type(items, item_type)

    root = ET.Element("opml", version="2.0")
    head = ET.SubElement(root, "head")
    title_elem = ET.SubElement(head, "title")
    title_elem.text = title

    date_created = ET.SubElement(head, "dateCreated")
    date_created.text = datetime.now(UTC).strftime("%a, %d %b %Y %H:%M:%S GMT")

    body = ET.SubElement(root, "body")

    if inferred_type == "tags":
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                tid, name = item[0], item[1]
            elif isinstance(item, dict):
                tid = item.get("id", item.get("tag_id", ""))
                name = item.get("name", item.get("text", ""))
            else:
                tid = str(item)
                name = str(item)

            outline_attrs = {
                "text": str(name),
                "title": str(name),
                "type": "tag",
                "id": str(tid),
                "tag_id": str(tid),
            }
            if tid:
                outline_attrs["htmlUrl"] = (
                    f"https://metabods.com/mbxy/site/archive.php?list=tag&id={tid}"
                )
            ET.SubElement(body, "outline", outline_attrs)
    else:
        # Categories (e.g. Nifty)
        for item in items:
            if isinstance(item, dict):
                path = item.get("path", item.get("text", ""))
                name = item.get("name", path)
            else:
                path = str(item).strip()
                name = path

            outline_attrs = {
                "text": str(name),
                "title": str(name),
                "type": "category",
                "path": str(path),
                "category": str(path),
            }
            if path:
                outline_attrs["htmlUrl"] = (
                    f"https://www.nifty.org/nifty/{path.lstrip('/')}/"
                )
            ET.SubElement(body, "outline", outline_attrs)

    if hasattr(ET, "indent"):
        ET.indent(root)

    raw_xml = ET.tostring(root, encoding="utf-8")
    xml_str = '<?xml version="1.0" encoding="utf-8"?>\n' + raw_xml.decode("utf-8")

    if target is not None:
        if isinstance(target, (str, Path)):
            with open(target, "w", encoding="utf-8") as f:
                f.write(xml_str)
        elif hasattr(target, "write"):
            target.write(xml_str)

    return xml_str


def export_list_to_json(
    items, target=None, title="Calibre Plugin Export", item_type=None, indent=2
):
    """Export items (Nifty categories or Metabods tags) to a JSON string or file.

    :param items: list of category path strings or tag tuples [id, name] / dicts
    :param target: optional file path (str, Path) or file-like object
    :param title: title for the export
    :param item_type: 'categories', 'tags', 'nifty', 'metabods', or None to infer
    :param indent: formatting indentation level
    :return: JSON string
    """
    inferred_type = infer_item_type(items, item_type)

    if inferred_type == "tags":
        formatted_items = []
        for item in items:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                formatted_items.append({"id": int(item[0]), "name": str(item[1])})
            elif isinstance(item, dict):
                tid = int(item.get("id", item.get("tag_id", 0)))
                formatted_items.append({"id": tid, "name": str(item.get("name", ""))})
            else:
                formatted_items.append({"id": int(item), "name": str(item)})
        plugin_name = "Metabods Downloader"
    else:
        formatted_items = []
        for item in items:
            if isinstance(item, dict):
                formatted_items.append(str(item.get("path", item.get("name", ""))))
            else:
                formatted_items.append(str(item).strip())
        plugin_name = "Nifty Downloader"

    doc = {
        "version": 1,
        "title": title,
        "plugin": plugin_name,
        "type": inferred_type,
        "items": formatted_items,
    }

    json_str = json.dumps(doc, indent=indent, ensure_ascii=False)

    if target is not None:
        if isinstance(target, (str, Path)):
            with open(target, "w", encoding="utf-8") as f:
                f.write(json_str)
        elif hasattr(target, "write"):
            target.write(json_str)

    return json_str


# -----------------------------------------------------------------------------
# Conflict resolution / Merge logic
# -----------------------------------------------------------------------------


def resolve_conflicts(
    existing, imported, item_type="categories", strategy=STRATEGY_MERGE
):
    """Merge imported items into existing items based on strategy.

    :param existing: list of existing items (strings for categories, [id, name] for tags)
    :param imported: list of imported items
    :param item_type: 'categories' or 'tags'
    :param strategy: 'merge', 'skip', or 'overwrite'
    :return: (updated_list, stats_dict)
    """
    strat = normalize_strategy(strategy)
    stats = {"added": 0, "updated": 0, "skipped": 0, "total": len(imported)}

    if item_type == "tags":
        existing_list = list(existing or [])
        existing_dict = {}
        for item in existing_list:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                try:
                    existing_dict[int(item[0])] = str(item[1])
                except (ValueError, TypeError):
                    pass
            elif isinstance(item, dict):
                try:
                    existing_dict[int(item["id"])] = str(item.get("name", ""))
                except (ValueError, TypeError):
                    pass

        if strat == STRATEGY_OVERWRITE:
            new_dict = {}
            for item in imported:
                tid, name = int(item[0]), str(item[1])
                new_dict[tid] = name
                stats["added"] += 1
            stats["updated"] = len(existing_dict)
            return [[k, v] for k, v in new_dict.items()], stats

        elif strat == STRATEGY_SKIP:
            res_dict = dict(existing_dict)
            for item in imported:
                tid, name = int(item[0]), str(item[1])
                if tid in res_dict:
                    stats["skipped"] += 1
                else:
                    res_dict[tid] = name
                    stats["added"] += 1
            return [[k, v] for k, v in res_dict.items()], stats

        else:  # STRATEGY_MERGE
            res_dict = dict(existing_dict)
            for item in imported:
                tid, name = int(item[0]), str(item[1])
                if tid in res_dict:
                    if name and name != res_dict[tid]:
                        res_dict[tid] = name
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                else:
                    res_dict[tid] = name
                    stats["added"] += 1
            return [[k, v] for k, v in res_dict.items()], stats

    else:
        # Categories
        existing_list = [str(x).strip() for x in (existing or []) if str(x).strip()]

        if strat == STRATEGY_OVERWRITE:
            res_list = []
            for item in imported:
                clean = str(item).strip()
                if clean and clean not in res_list:
                    res_list.append(clean)
                    stats["added"] += 1
            stats["updated"] = len(existing_list)
            return res_list, stats

        elif strat == STRATEGY_SKIP:
            res_list = list(existing_list)
            for item in imported:
                clean = str(item).strip()
                if not clean:
                    continue
                if clean in res_list:
                    stats["skipped"] += 1
                else:
                    res_list.append(clean)
                    stats["added"] += 1
            return res_list, stats

        else:  # STRATEGY_MERGE
            res_list = list(existing_list)
            for item in imported:
                clean = str(item).strip()
                if not clean:
                    continue
                if clean in res_list:
                    stats["skipped"] += 1
                else:
                    res_list.append(clean)
                    stats["added"] += 1
            return res_list, stats


# -----------------------------------------------------------------------------
# Import functions
# -----------------------------------------------------------------------------


def _read_source(source):
    """Read content string from path, file-like object, bytes, or string."""
    if isinstance(source, (str, Path)):
        if os.path.exists(str(source)):
            with open(source, encoding="utf-8", errors="replace") as f:
                return f.read()
        return str(source)
    if isinstance(source, bytes):
        return source.decode("utf-8", errors="replace")
    if hasattr(source, "read"):
        raw = source.read()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return raw
    raise ValueError(f"Unsupported source type: {type(source)}")


def import_list_from_opml(
    source, item_type=None, existing=None, strategy=STRATEGY_MERGE, return_stats=False
):
    """Import items (Nifty categories or Metabods tags) from an OPML 2.0 XML document.

    :param source: file path, XML string/bytes, or file-like object
    :param item_type: 'categories', 'tags', 'nifty', 'metabods', or None to infer
    :param existing: existing list of items to merge against
    :param strategy: 'merge', 'skip', or 'overwrite'
    :param return_stats: if True, returns (items, stats_dict)
    :return: list of items, or (list, stats_dict)
    """
    content = _read_source(source)
    if not content or not content.strip():
        raise ValueError("Invalid OPML content: input is empty.")

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise ValueError(f"Invalid OPML XML content: {exc}") from exc

    if root.tag.lower() != "opml":
        opml_child = root.find(".//opml")
        if opml_child is not None:
            root = opml_child
        else:
            raise ValueError(
                f"Invalid OPML file: root element is <{root.tag}> instead of <opml>."
            )

    body = root.find("body")
    if body is None:
        raise ValueError("Invalid OPML file: missing <body> element.")

    # Determine desired item type if specified
    target_type = infer_item_type([], item_type) if item_type else None

    # Traverse outline elements
    raw_tags = []
    raw_categories = []

    def _traverse(elem, folder_stack):
        outlines = elem.findall("outline")
        for outline in outlines:
            children = outline.findall("outline")
            text = (outline.get("text") or outline.get("title") or "").strip()
            path_attr = outline.get("path") or outline.get("category") or ""
            type_attr = (outline.get("type") or "").lower().strip()
            id_attr = outline.get("id") or outline.get("tag_id") or ""
            html_url = (
                outline.get("htmlUrl")
                or outline.get("xmlUrl")
                or outline.get("url")
                or ""
            )

            # Check if this outline represents a tag
            if not id_attr and html_url:
                m = re.search(r"[?&]id=(\d+)", html_url)
                if m:
                    id_attr = m.group(1)

            if children:
                # Folder or parent node
                new_stack = list(folder_stack)
                if text:
                    new_stack.append(text)
                _traverse(outline, new_stack)
            else:
                # Leaf node
                if id_attr or type_attr == "tag":
                    try:
                        tid = int(id_attr) if id_attr else 0
                    except (ValueError, TypeError):
                        tid = 0
                    raw_tags.append([tid, text or str(tid)])
                else:
                    # Category leaf
                    if path_attr:
                        cat_path = path_attr.strip()
                    elif folder_stack:
                        cat_path = "/".join(folder_stack + ([text] if text else []))
                    else:
                        cat_path = text
                    if cat_path:
                        raw_categories.append(cat_path)

    _traverse(body, [])

    if target_type == "tags":
        imported_items = raw_tags
        resolved_type = "tags"
    elif target_type == "categories":
        # If no categories were collected but tags were, convert tags to paths
        if not raw_categories and raw_tags:
            imported_items = [t[1] for t in raw_tags]
        else:
            imported_items = raw_categories
        resolved_type = "categories"
    else:
        # Auto-detect
        if raw_tags and not raw_categories:
            imported_items = raw_tags
            resolved_type = "tags"
        else:
            imported_items = raw_categories
            resolved_type = "categories"

    updated_list, stats = resolve_conflicts(
        existing, imported_items, item_type=resolved_type, strategy=strategy
    )
    if return_stats:
        return updated_list, stats
    return updated_list


def import_list_from_json(
    source, item_type=None, existing=None, strategy=STRATEGY_MERGE, return_stats=False
):
    """Import items (Nifty categories or Metabods tags) from a JSON string or file.

    :param source: file path, JSON string/bytes, or file-like object
    :param item_type: 'categories', 'tags', 'nifty', 'metabods', or None to infer
    :param existing: existing list of items to merge against
    :param strategy: 'merge', 'skip', or 'overwrite'
    :param return_stats: if True, returns (items, stats_dict)
    :return: list of items, or (list, stats_dict)
    """
    content = _read_source(source)
    if not content or not content.strip():
        raise ValueError("Invalid JSON content: input is empty.")

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON content: {exc}") from exc

    if not isinstance(data, (dict, list)):
        raise ValueError("JSON content must be an object or a list.")

    doc_type = None
    if isinstance(data, dict):
        doc_type = data.get("type")
        items_raw = (
            data.get("items")
            or data.get("categories")
            or data.get("tags")
            or data.get("favorite_categories")
            or data.get("favorite_tags")
        )
        if items_raw is None:
            raise ValueError(
                "Invalid JSON format: missing 'items', 'categories', or 'tags' array."
            )
    else:
        items_raw = data

    if not isinstance(items_raw, list):
        raise ValueError("Invalid JSON format: 'items' field must be a list/array.")

    target_type = (
        infer_item_type([], item_type or doc_type) if (item_type or doc_type) else None
    )
    if not target_type:
        target_type = infer_item_type(items_raw)

    imported_items = []
    if target_type == "tags":
        for it in items_raw:
            if isinstance(it, (list, tuple)) and len(it) >= 2:
                try:
                    imported_items.append([int(it[0]), str(it[1])])
                except (ValueError, TypeError):
                    continue
            elif isinstance(it, dict):
                tid = it.get("id", it.get("tag_id", 0))
                name = it.get("name", it.get("text", str(tid)))
                try:
                    imported_items.append([int(tid), str(name)])
                except (ValueError, TypeError):
                    continue
            elif isinstance(it, int):
                imported_items.append([it, str(it)])
            elif isinstance(it, str):
                imported_items.append([0, it])
    else:
        for it in items_raw:
            if isinstance(it, dict):
                val = it.get("path", it.get("name", it.get("text", "")))
            else:
                val = str(it).strip()
            if val:
                imported_items.append(val)

    updated_list, stats = resolve_conflicts(
        existing, imported_items, item_type=target_type, strategy=strategy
    )
    if return_stats:
        return updated_list, stats
    return updated_list


def import_list_from_file(
    file_path,
    item_type=None,
    existing=None,
    strategy=STRATEGY_MERGE,
    return_stats=False,
):
    """Import items from a file, automatically determining OPML or JSON format."""
    fpath = str(file_path).lower()
    if fpath.endswith(".json"):
        return import_list_from_json(
            file_path,
            item_type=item_type,
            existing=existing,
            strategy=strategy,
            return_stats=return_stats,
        )

    with open(file_path, encoding="utf-8", errors="replace") as stream:
        head = stream.read(256).strip()

    if head.startswith("{") or head.startswith("["):
        return import_list_from_json(
            file_path,
            item_type=item_type,
            existing=existing,
            strategy=strategy,
            return_stats=return_stats,
        )
    return import_list_from_opml(
        file_path,
        item_type=item_type,
        existing=existing,
        strategy=strategy,
        return_stats=return_stats,
    )


# -----------------------------------------------------------------------------
# Optional UI Helper
# -----------------------------------------------------------------------------


def ask_import_strategy(parent=None):
    """Prompt the user with an options dialog to select the import conflict resolution strategy.

    Returns: 'merge', 'skip', 'overwrite', or None if cancelled.
    """
    try:
        from qt.core import QDialog, QDialogButtonBox, QLabel, QRadioButton, QVBoxLayout
    except ImportError:
        try:
            from PyQt6.QtWidgets import (
                QDialog,
                QDialogButtonBox,
                QLabel,
                QRadioButton,
                QVBoxLayout,
            )
        except ImportError:
            return STRATEGY_MERGE

    dialog = QDialog(parent)
    dialog.setWindowTitle("Import Options")
    dialog.resize(360, 200)
    layout = QVBoxLayout(dialog)

    layout.addWidget(QLabel("Choose how to resolve conflicts with existing favorites:"))

    rb_merge = QRadioButton("Auto-merge: Combine new and existing favorites")
    rb_merge.setChecked(True)
    layout.addWidget(rb_merge)

    rb_skip = QRadioButton("Skip existing: Only add items not in favorites")
    layout.addWidget(rb_skip)

    rb_overwrite = QRadioButton("Overwrite: Replace current favorites completely")
    layout.addWidget(rb_overwrite)

    button_box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    button_box.accepted.connect(dialog.accept)
    button_box.rejected.connect(dialog.reject)
    layout.addWidget(button_box)

    result = dialog.exec()
    if result != QDialog.DialogCode.Accepted and result != 1:
        return None

    if rb_overwrite.isChecked():
        return STRATEGY_OVERWRITE
    if rb_skip.isChecked():
        return STRATEGY_SKIP
    return STRATEGY_MERGE
