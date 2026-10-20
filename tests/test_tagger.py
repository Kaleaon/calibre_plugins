import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tagger'))

spec = importlib.util.spec_from_file_location('rules', Path(__file__).resolve().parents[1] / 'tagger/rules.py')
rules = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rules)


def book(i, **kw):
    base = {'id': i, 'title': f'Book {i}', 'authors': ['A'], 'tags': [], 'identifiers': {}, 'publisher': '', 'comments': '', 'series': ''}
    base.update(kw)
    return base


class DetectTests(unittest.TestCase):
    def test_identifier_key_value_and_publisher(self):
        s = rules.DEFAULT_SITES
        self.assertEqual(rules.detect_sites(book(1, identifiers={'metabods': 'x'}), s), ['Metabods'])
        self.assertEqual(rules.detect_sites(book(1, identifiers={'url': 'https://www.royalroad.com/fiction/1/x'}), s), ['Royal Road'])
        self.assertEqual(rules.detect_sites(book(1, publisher='archiveofourown.org'), s), ['Archive of Our Own'])
        self.assertEqual(rules.detect_sites(book(1, identifiers={'isbn': '123'}), s), [])

    def test_host_must_be_whole_word_and_comments_opt_in(self):
        s = rules.DEFAULT_SITES
        self.assertEqual(rules.detect_sites(book(1, identifiers={'url': 'https://notnifty.org.evil.com/'}), s), [])
        text = book(1, comments='<p>originally on royalroad.com</p>')
        self.assertEqual(rules.detect_sites(text, s), [])
        self.assertEqual(rules.detect_sites(text, s, scan_comments=True), ['Royal Road'])


class FanFicFareTests(unittest.TestCase):
    def test_fanficfare_identifier_form(self):
        # FanFicFare stores url identifiers with ':' turned into '|', and the publisher is the site's domain.
        s = rules.DEFAULT_SITES
        rr = book(1, identifiers={'url': 'https|//www.royalroad.com/fiction/21220'}, publisher='www.royalroad.com')
        self.assertEqual(rules.detect_sites(rr, s), ['Royal Road'])
        sb = book(2, identifiers={'url': 'https|//forums.spacebattles.com/threads/x.1/'})
        self.assertEqual(rules.detect_sites(sb, s), ['SpaceBattles'])
        self.assertEqual(rules.detect_sites(book(3, identifiers={'url': 'https|//archiveofourown.org/works/1'}), s), ['Archive of Our Own'])
        self.assertEqual(rules.detect_sites(book(4, identifiers={'uri': 'https|//www.scribblehub.com/series/1/'}), s), ['Scribble Hub'])

    def test_table_has_no_duplicate_hosts(self):
        hosts = [h for _, hs, _ in rules.DEFAULT_SITES for h in hs]
        self.assertEqual(len(hosts), len(set(hosts)))
        self.assertGreater(len(rules.DEFAULT_SITES), 100)
        names = [n for n, _, _ in rules.DEFAULT_SITES]
        self.assertEqual(names.count('Metabods'), 1)


class ParseTests(unittest.TestCase):
    def test_rules_and_errors(self):
        parsed, errors = rules.parse_rules('# c\ntag:=Cock Growth => Theme.Growth, Growth\nlitrpg => LitRPG\nre:(( => Bad\nnonsense\ntitle: => x\n')
        self.assertEqual(len(parsed), 2)
        self.assertEqual(parsed[0].tags, ['Theme.Growth', 'Growth'])
        self.assertEqual(len(errors), 3)

    def test_sites(self):
        sites, errors = rules.parse_sites('storiesonline.net, sol.net => Stories Online\nbroken')
        self.assertEqual(sites, [('Stories Online', ['storiesonline.net', 'sol.net'], [])])
        self.assertEqual(len(errors), 1)


class PlanTests(unittest.TestCase):
    def test_source_tags_additive_and_case_insensitive(self):
        books = [book(1, identifiers={'nifty': 'a'}, tags=['source.nifty', 'Keep']), book(2, identifiers={'metabods': 'b'}, tags=['X']),
                 book(3)]
        plan = rules.plan_changes(books)
        self.assertEqual([p['id'] for p in plan], [2])  # book 1 already has the tag (any case); book 3 has no source
        self.assertEqual(plan[0]['tags'], ['X', 'Source.Metabods'])

    def test_rules_fields_and_series(self):
        parsed, _ = rules.parse_rules('tag:=Muscle Growth => Theme.Growth\nsource:Royal => Web serial\ntitle:re:^The\\b => Starts with The\nlitrpg => LitRPG')
        books = [book(1, tags=['Muscle Growth', 'Other'], title='The End', series='Saga', identifiers={'royalroad': '1'}),
                 book(2, tags=['Muscle Growth Extra'], title='Zed')]
        plan = {p['id']: p['add'] for p in rules.plan_changes(books, series_prefix='Series.', rules=parsed)}
        self.assertEqual(plan[1], ['Source.Royal Road', 'Series.Saga', 'Theme.Growth', 'Web serial', 'Starts with The'])
        self.assertNotIn(2, plan)  # '=' means exact tag match, so "Muscle Growth Extra" does not trigger

    def test_idempotent(self):
        b = book(1, identifiers={'nifty': 'a'})
        first = rules.plan_changes([b])
        b['tags'] = first[0]['tags']
        self.assertEqual(rules.plan_changes([b]), [])

    def test_merge_sites_deduplication(self):
        extra_text = 'archiveofourown.org => ao3\nao3 => ao3_key\nmycustomsite.org => My Custom Site'
        extra, _ = rules.parse_sites(extra_text)
        merged = rules.merge_sites(rules.DEFAULT_SITES, extra)
        names = [n for n, _, _ in merged]
        self.assertNotIn('ao3', names)
        self.assertNotIn('ao3_key', names)
        self.assertIn('My Custom Site', names)

    def test_plan_changes_with_overlapping_extra_sites(self):
        extra, _ = rules.parse_sites('archiveofourown.org => ao3\nroyalroad.com => rr')
        merged = rules.merge_sites(rules.DEFAULT_SITES, extra)
        ao3_book = book(1, publisher='archiveofourown.org')
        rr_book = book(2, identifiers={'url': 'https://www.royalroad.com/fiction/1/test'})
        plan = rules.plan_changes([ao3_book, rr_book], sites=merged)
        self.assertEqual(len(plan), 2)
        self.assertEqual(plan[0]['add'], ['Source.Archive of Our Own'])
        self.assertEqual(plan[1]['add'], ['Source.Royal Road'])


class TaggerDialogTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object
        qt_mod = MagicMock()
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        tagger_pkg = types.ModuleType('calibre_plugins.story_collection_tagger')
        tagger_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'tagger')]
        tagger_pkg.config = MagicMock(prefs={'add_source': True, 'source_prefix': 'Source.', 'series_prefix': 'Series.', 'scan_comments': False, 'rules': '', 'sites': ''})
        tagger_pkg.rules = rules
        tagger_pkg.error_handler = MagicMock()
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins'].__path__ = []
        sys.modules['calibre_plugins.story_collection_tagger'] = tagger_pkg
        sys.modules['calibre_plugins.story_collection_tagger.config'] = tagger_pkg.config
        sys.modules['calibre_plugins.story_collection_tagger.rules'] = tagger_pkg.rules
        sys.modules['calibre_plugins.story_collection_tagger.error_handler'] = tagger_pkg.error_handler
        sys.modules['error_handler'] = tagger_pkg.error_handler

    def tearDown(self):
        for k in list(sys.modules.keys()):
            if k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_show_dialog_sets_word_wrap_on_labels(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]
        action.show_dialog()

        qt_core = sys.modules['qt.core']
        self.assertGreaterEqual(qt_core.QLabel.return_value.setWordWrap.call_count, 4)

    def test_main_dialog_preview_launches_modal_and_applies_selected(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        db_mock = MagicMock()
        action.gui.current_db.new_api = db_mock
        action.gui.library_view.get_selected_ids.return_value = [1]

        mi = MagicMock()
        mi.title = 'Test Book'
        mi.authors = ['Author A']
        mi.tags = ['ExistingTag']
        mi.identifiers = {'nifty': '123'}
        mi.publisher = ''
        mi.comments = ''
        mi.series = ''
        db_mock.get_metadata.return_value = mi

        with unittest.mock.patch.object(action_mod, 'TaggerPreviewDialog') as MockPreviewDialog:
            mock_dlg_instance = MagicMock()
            mock_dlg_instance.exec.return_value = True
            mock_dlg_instance.get_selected_plan.return_value = [
                {'id': 1, 'title': 'Test Book', 'add': ['Source.Nifty'], 'tags': ['ExistingTag', 'Source.Nifty']}
            ]
            MockPreviewDialog.return_value = mock_dlg_instance

            action.show_dialog()

            # Find the do_preview function connected to the Preview button's clicked signal
            preview_btn_mock = sys.modules['qt.core'].QPushButton.return_value
            do_preview_slot = preview_btn_mock.clicked.connect.call_args_list[0][0][0]
            do_preview_slot()

            MockPreviewDialog.assert_called_once()
            db_mock.set_field.assert_called_once_with('tags', {1: ('ExistingTag', 'Source.Nifty')})


try:
    from PyQt6 import QtCore, QtGui, QtWidgets
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


@unittest.skipUnless(HAVE_QT, 'PyQt6 is required for UI tests')
class TaggerPreviewDialogUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        spec_ui = importlib.util.spec_from_file_location('tagger_ui', Path(__file__).resolve().parents[1] / 'tagger/ui.py')
        self.tagger_ui = importlib.util.module_from_spec(spec_ui)
        spec_ui.loader.exec_module(self.tagger_ui)

    def sample_plan(self):
        return [
            {
                'id': 1,
                'title': 'Book One',
                'add': ['Source.Metabods', 'Theme.Growth'],
                'tags': ['Fiction', 'Source.Metabods', 'Theme.Growth']
            },
            {
                'id': 2,
                'title': 'Book Two',
                'add': ['LitRPG'],
                'tags': ['Sci-Fi', 'LitRPG']
            }
        ]

    def test_tree_populates_correctly(self):
        dialog = self.tagger_ui.TaggerPreviewDialog(None, self.sample_plan())
        self.assertEqual(dialog.tree.topLevelItemCount(), 2)

        book1_item = dialog.tree.topLevelItem(0)
        self.assertEqual(book1_item.text(0), 'Book One')
        self.assertEqual(book1_item.childCount(), 2)
        self.assertEqual(book1_item.child(0).text(0), 'Source.Metabods')
        self.assertEqual(book1_item.child(1).text(0), 'Theme.Growth')

        book2_item = dialog.tree.topLevelItem(1)
        self.assertEqual(book2_item.text(0), 'Book Two')
        self.assertEqual(book2_item.childCount(), 1)
        self.assertEqual(book2_item.child(0).text(0), 'LitRPG')

    def test_checkbox_propagation(self):
        dialog = self.tagger_ui.TaggerPreviewDialog(None, self.sample_plan())
        book1_item = dialog.tree.topLevelItem(0)

        # Uncheck book 1 -> child tag items should become unchecked
        book1_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(book1_item.child(0).checkState(0), QtCore.Qt.CheckState.Unchecked)
        self.assertEqual(book1_item.child(1).checkState(0), QtCore.Qt.CheckState.Unchecked)

        # Check child item 0 -> book item should become PartiallyChecked
        book1_item.child(0).setCheckState(0, QtCore.Qt.CheckState.Checked)
        self.assertEqual(book1_item.checkState(0), QtCore.Qt.CheckState.PartiallyChecked)

    def test_select_all_and_deselect_all(self):
        dialog = self.tagger_ui.TaggerPreviewDialog(None, self.sample_plan())

        dialog.deselect_all()
        selected_plan = dialog.get_selected_plan()
        self.assertEqual(len(selected_plan), 0)

        ok_btn = dialog.button_box.button(QtWidgets.QDialogButtonBox.StandardButton.Ok)
        self.assertFalse(ok_btn.isEnabled())

        dialog.select_all()
        selected_plan = dialog.get_selected_plan()
        self.assertEqual(len(selected_plan), 2)
        self.assertTrue(ok_btn.isEnabled())

    def test_get_selected_plan_filtering(self):
        dialog = self.tagger_ui.TaggerPreviewDialog(None, self.sample_plan())

        # Uncheck Theme.Growth on Book One
        book1_item = dialog.tree.topLevelItem(0)
        book1_item.child(1).setCheckState(0, QtCore.Qt.CheckState.Unchecked)

        # Uncheck Book Two completely
        book2_item = dialog.tree.topLevelItem(1)
        book2_item.setCheckState(0, QtCore.Qt.CheckState.Unchecked)

        filtered = dialog.get_selected_plan()
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]['id'], 1)
        self.assertEqual(filtered[0]['add'], ['Source.Metabods'])
        self.assertEqual(filtered[0]['tags'], ['Fiction', 'Source.Metabods'])


if __name__ == '__main__':
    unittest.main()
