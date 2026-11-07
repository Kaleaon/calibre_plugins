"""Tree manipulation and checkbox selection utilities shared across Calibre plugins."""

try:
    from qt.core import Qt
except ImportError:
    try:
        from PyQt6.QtCore import Qt
    except ImportError:
        Qt = None


def is_item_checked(item, column=0):
    """Check if a tree item's checkState is checked (either Qt.CheckState.Checked or integer 2)."""
    state = item.checkState(column)
    if (
        Qt is not None
        and hasattr(Qt, "CheckState")
        and hasattr(Qt.CheckState, "Checked")
    ):
        if state == Qt.CheckState.Checked:
            return True
    return state == 2


def set_children_check_state(item, state, column=0):
    """Recursively set check state for all descendants of a tree item."""
    for i in range(item.childCount()):
        child = item.child(i)
        child.setCheckState(column, state)
        set_children_check_state(child, state, column=column)


def update_parent_check_state(item, column=0):
    """Update parent item check state based on children states (Checked, Unchecked, or PartiallyChecked)."""
    parent = item.parent()
    if parent is None:
        return

    checked_state = (
        getattr(Qt.CheckState, "Checked", 2)
        if (Qt is not None and hasattr(Qt, "CheckState"))
        else 2
    )
    unchecked_state = (
        getattr(Qt.CheckState, "Unchecked", 0)
        if (Qt is not None and hasattr(Qt, "CheckState"))
        else 0
    )
    partially_state = (
        getattr(Qt.CheckState, "PartiallyChecked", 1)
        if (Qt is not None and hasattr(Qt, "CheckState"))
        else 1
    )

    checked_count = sum(
        1
        for i in range(parent.childCount())
        if is_item_checked(parent.child(i), column)
    )
    unchecked_count = sum(
        1
        for i in range(parent.childCount())
        if parent.child(i).checkState(column) in (unchecked_state, 0)
    )
    total = parent.childCount()

    if checked_count == total:
        parent.setCheckState(column, checked_state)
    elif unchecked_count == total:
        parent.setCheckState(column, unchecked_state)
    else:
        parent.setCheckState(column, partially_state)

    update_parent_check_state(parent, column=column)


def collect_tree_changes(tree):
    """Extract checked field items and metadata payloads from a tree widget.

    Returns list of (top_item_user_data, {child_user_data: child_user_data_plus_1}).
    """
    role_user = (
        getattr(Qt.ItemDataRole, "UserRole", 32)
        if (Qt is not None and hasattr(Qt, "ItemDataRole"))
        else 32
    )
    try:
        role_user_int = int(role_user)
    except (ValueError, TypeError):
        role_user_int = 32

    items = []
    for i in range(tree.topLevelItemCount()):
        top = tree.topLevelItem(i)
        changes = {}
        for j in range(top.childCount()):
            child = top.child(j)
            if is_item_checked(child, 0):
                key = child.data(0, role_user)
                if key is None and role_user_int != role_user:
                    key = child.data(0, role_user_int)
                val = child.data(0, role_user + 1)
                if val is None:
                    val = child.data(0, role_user_int + 1)
                changes[key] = val
        if changes:
            top_key = top.data(0, role_user)
            if top_key is None and role_user_int != role_user:
                top_key = top.data(0, role_user_int)
            items.append((top_key, changes))
    return items


def collect_checked_fields(boxes, labels=None):
    """Return tuple of field keys whose checkboxes in `boxes` dict are checked.

    If `labels` sequence of (field_key, label) is provided, preserves that order.
    """
    if labels:
        return tuple(f for f, _ in labels if f in boxes and boxes[f].isChecked())
    return tuple(f for f, cb in boxes.items() if cb.isChecked())


def chosen_fields(boxes, field_labels=None):
    """Query active metadata selection checkboxes.

    `boxes`: dict mapping field names to QCheckBox widgets or boolean values.
    `field_labels`: list of (field_key, label) tuples, list of field keys, or None.
    Returns a tuple of checked field keys.
    """
    if field_labels is None:
        if isinstance(boxes, dict):
            keys = list(boxes.keys())
        else:
            keys = []
    else:
        keys = [
            item[0] if isinstance(item, (tuple, list)) else item
            for item in field_labels
        ]

    result = []
    for key in keys:
        box = boxes.get(key) if isinstance(boxes, dict) else getattr(boxes, key, None)
        if box is not None:
            if callable(getattr(box, "isChecked", None)):
                checked = box.isChecked()
            else:
                checked = bool(box)
            if checked:
                result.append(key)
    return tuple(result)
