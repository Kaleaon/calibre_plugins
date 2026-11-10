"""Unit and integration tests for EmptyStateOverlay and empty state transitions across plugin dialogs."""
import importlib
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    HAVE_QT = True
except ImportError:
    HAVE_QT = False

from tests.test_qt_dialogs import QtCase


@unittest.skipUnless(HAVE_QT, 'PyQt6 is not installed')
class EmptyStateOverlayTests(QtCase):

    def test_overlay_standalone_behavior(self):
        from common.guikit import EmptyStateOverlay
        tree = QtWidgets.QTreeWidget()
        overlay = EmptyStateOverlay(
            tree,
            icon='✨',
            title='Test Title',
            message='Test Message',
            action_text='Click Me',
            on_action=lambda: None
        )
        self.assertTrue(overlay.isHidden())
        self.assertEqual(overlay.title_label.text(), 'Test Title')
        self.assertEqual(overlay.message_label.text(), 'Test Message')
        self.assertEqual(overlay.icon_label.text(), '✨')

        overlay.show_empty()
        self.assertFalse(overlay.isHidden())

        overlay.hide_empty()
        self.assertTrue(overlay.isHidden())

        overlay.update_visibility(0)
        self.assertFalse(overlay.isHidden())

        overlay.update_visibility(5)
        self.assertTrue(overlay.isHidden())

    def test_gemini_dialog_empty_state(self):
        from common.guikit import EmptyStateOverlay
        package = self.load('gemini_library_fixer')
        action_mod = importlib.import_module(package + '.action')
        action = action_mod.GeminiFixerAction()
        action.gui = MagicMock()
        action.gui.current_db.new_api.get_selected_ids.return_value = []
        action.gui.current_db.new_api.all_book_ids.return_value = []

        tree = QtWidgets.QTreeWidget()
        overlay = EmptyStateOverlay(
            tree,
            icon='✨',
            title='Check your books with Gemini',
            message='Select books and click "Check books" to ask Gemini for metadata suggestions.',
            action_text='Check books',
            on_action=lambda: None
        )
        overlay.show_empty()
        self.assertFalse(overlay.isHidden())
        self.assertEqual(overlay.title_label.text(), 'Check your books with Gemini')

        overlay.show_empty(
            title='No suggested changes found',
            message='Gemini did not find any metadata changes for the checked books.',
            icon='🔍'
        )
        self.assertFalse(overlay.isHidden())
        self.assertEqual(overlay.title_label.text(), 'No suggested changes found')

    def test_media_matcher_add_and_match_empty_states(self):
        from common.guikit import EmptyStateOverlay
        package = self.load('media_matcher')
        action_mod = importlib.import_module(package + '.action')

        tree_add = QtWidgets.QTreeWidget()
        overlay_add = EmptyStateOverlay(
            tree_add,
            icon='📁',
            title='No audio files added',
            message='Choose audio files or a folder to add music and audiobooks to your library.',
            actions=[('Choose files…', lambda: None), ('Choose folder…', lambda: None)]
        )
        overlay_add.show_empty()
        self.assertFalse(overlay_add.isHidden())
        self.assertEqual(overlay_add.title_label.text(), 'No audio files added')

        tree_match = QtWidgets.QTreeWidget()
        overlay_match = EmptyStateOverlay(
            tree_match,
            icon='🔍',
            title='No matches looked up yet',
            message='Click "Look up" to search MusicBrainz and Open Library for metadata and cover art.'
        )
        overlay_match.show_empty()
        self.assertFalse(overlay_match.isHidden())

        overlay_match.show_empty(
            title='No metadata matches found',
            message='No matches were found for the selected books.'
        )
        self.assertFalse(overlay_match.isHidden())
        self.assertEqual(overlay_match.title_label.text(), 'No metadata matches found')

    def test_reddit_discover_and_manage_empty_states(self):
        package = self.load('reddit_follower')
        ui = importlib.import_module(package + '.ui')

        action_mock = MagicMock()
        dialog = ui.DiscoverDialog(None, action_mock)
        self.assertFalse(dialog.overlay.isHidden())
        self.assertEqual(dialog.overlay.title_label.text(), 'Find stories on Reddit')

        manage_dialog = ui.ManageDialog(None, action_mock)
        self.assertFalse(manage_dialog.overlay.isHidden())
        self.assertEqual(manage_dialog.overlay.title_label.text(), 'No followed stories')

    def test_fanfic_download_dialog_urls_overlay(self):
        if 'fanfic_downloader' not in self.built:
            raise unittest.SkipTest('fanfic zip needs FanFicFare installed to build')
        package = self.load('fanfic_downloader')
        ui = importlib.import_module(package + '.ui')

        dialog = ui.DownloadDialog(None)
        self.assertFalse(dialog.urls_overlay.isHidden())
        self.assertEqual(dialog.urls_overlay.title_label.text(), 'Enter story URLs')

        dialog.urls.setPlainText('https://www.royalroad.com/fiction/21220')
        self.assertTrue(dialog.urls_overlay.isHidden())

        dialog.urls.clear()
        self.assertFalse(dialog.urls_overlay.isHidden())


if __name__ == '__main__':
    unittest.main()
