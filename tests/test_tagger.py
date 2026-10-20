import importlib.util
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'common'))
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

    def test_host_pattern_memoization(self):
        rules._host_pattern.cache_clear()
        p1 = rules._host_pattern('example.com')
        p2 = rules._host_pattern('example.com')
        self.assertIs(p1, p2)
        info = rules._host_pattern.cache_info()
        self.assertEqual(info.hits, 1)
        self.assertEqual(info.misses, 1)

    def test_bulk_site_detection_uses_cache(self):
        rules._host_pattern.cache_clear()
        books = [
            book(i, publisher=f'publisher-{i}.com', identifiers={'url': f'https://www.royalroad.com/fiction/{i}'})
            for i in range(10)
        ]
        plan = rules.plan_changes(books)
        self.assertEqual(len(plan), 10)
        info = rules._host_pattern.cache_info()
        self.assertGreater(info.hits, 0)
        self.assertGreater(info.currsize, 0)


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
        tagger_pkg.config = MagicMock(prefs={'add_source': True, 'source_prefix': 'Source.', 'series_prefix': 'Series.', 'scan_comments': False, 'rules': '', 'sites': ''})
        tagger_pkg.rules = rules
        tagger_pkg.error_handler = MagicMock()
        from common.guikit import DialogAction
        tagger_pkg.guikit = MagicMock()
        tagger_pkg.guikit.DialogAction = DialogAction
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.story_collection_tagger'] = tagger_pkg
        sys.modules['calibre_plugins.story_collection_tagger.config'] = tagger_pkg.config
        sys.modules['calibre_plugins.story_collection_tagger.rules'] = tagger_pkg.rules
        sys.modules['calibre_plugins.story_collection_tagger.error_handler'] = tagger_pkg.error_handler
        sys.modules['calibre_plugins.story_collection_tagger.guikit'] = tagger_pkg.guikit

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

    def _load_action_mod(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)
        return action_mod

    def test_invalidate_preview_clears_plan_and_disables_apply(self):
        action_mod = self._load_action_mod()
        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]
        action.books_for = MagicMock(return_value=[book(1, identifiers={'nifty': 'a'})])

        qt_core = sys.modules['qt.core']
        widgets = {}

        def mock_widget(cls_name):
            m = MagicMock()
            widgets.setdefault(cls_name, []).append(m)
            return m

        qt_core.QPlainTextEdit.side_effect = lambda *a, **k: mock_widget('QPlainTextEdit')
        qt_core.QLineEdit.side_effect = lambda *a, **k: mock_widget('QLineEdit')
        qt_core.QCheckBox.side_effect = lambda *a, **k: mock_widget('QCheckBox')
        qt_core.QRadioButton.side_effect = lambda *a, **k: mock_widget('QRadioButton')
        qt_core.QPushButton.side_effect = lambda *a, **k: mock_widget('QPushButton')

        action.show_dialog()

        rules_edit = widgets['QPlainTextEdit'][0]
        preview = widgets['QPlainTextEdit'][2]
        preview_btn = widgets['QPushButton'][0]
        apply_btn = widgets['QPushButton'][1]

        do_preview = preview_btn.clicked.connect.call_args[0][0]
        invalidate_preview = rules_edit.textChanged.connect.call_args[0][0]
        do_apply = apply_btn.clicked.connect.call_args[0][0]

        # First run preview
        do_preview()
        apply_btn.setEnabled.assert_called_with(True)

        # Trigger invalidation
        preview.clear.reset_mock()
        apply_btn.setEnabled.reset_mock()
        invalidate_preview()

        # Verify plan was cleared and apply button disabled
        apply_btn.setEnabled.assert_called_with(False)
        preview.clear.assert_called_once()

        # Verify do_apply now does nothing because plan is cleared
        action.gui.current_db.new_api.set_field.reset_mock()
        do_apply()
        action.gui.current_db.new_api.set_field.assert_not_called()

    def test_invalidate_preview_preserves_placeholder_when_plan_empty(self):
        action_mod = self._load_action_mod()
        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]

        qt_core = sys.modules['qt.core']
        widgets = {}

        def mock_widget(cls_name):
            m = MagicMock()
            widgets.setdefault(cls_name, []).append(m)
            return m

        qt_core.QPlainTextEdit.side_effect = lambda *a, **k: mock_widget('QPlainTextEdit')
        qt_core.QLineEdit.side_effect = lambda *a, **k: mock_widget('QLineEdit')
        qt_core.QCheckBox.side_effect = lambda *a, **k: mock_widget('QCheckBox')
        qt_core.QRadioButton.side_effect = lambda *a, **k: mock_widget('QRadioButton')
        qt_core.QPushButton.side_effect = lambda *a, **k: mock_widget('QPushButton')

        action.show_dialog()

        rules_edit = widgets['QPlainTextEdit'][0]
        preview = widgets['QPlainTextEdit'][2]
        invalidate_preview = rules_edit.textChanged.connect.call_args[0][0]

        # Call invalidate_preview when state['plan'] is empty
        preview.clear.reset_mock()
        invalidate_preview()

        # preview.clear should NOT be called when state['plan'] is already empty
        preview.clear.assert_not_called()

    def test_all_control_signals_connected_to_invalidate_preview(self):
        action_mod = self._load_action_mod()
        action = action_mod.TaggerAction()
        action.gui = MagicMock()
        action.gui.library_view.get_selected_ids.return_value = [1]

        qt_core = sys.modules['qt.core']
        widgets = {}

        def mock_widget(cls_name):
            m = MagicMock()
            widgets.setdefault(cls_name, []).append(m)
            return m

        qt_core.QPlainTextEdit.side_effect = lambda *a, **k: mock_widget('QPlainTextEdit')
        qt_core.QLineEdit.side_effect = lambda *a, **k: mock_widget('QLineEdit')
        qt_core.QCheckBox.side_effect = lambda *a, **k: mock_widget('QCheckBox')
        qt_core.QRadioButton.side_effect = lambda *a, **k: mock_widget('QRadioButton')
        qt_core.QPushButton.side_effect = lambda *a, **k: mock_widget('QPushButton')

        action.show_dialog()

        rules_edit = widgets['QPlainTextEdit'][0]
        sites_edit = widgets['QPlainTextEdit'][1]
        prefix = widgets['QLineEdit'][0]
        series_prefix = widgets['QLineEdit'][1]
        add_source = widgets['QCheckBox'][0]
        scan = widgets['QCheckBox'][1]
        scope_selected = widgets['QRadioButton'][0]
        scope_all = widgets['QRadioButton'][1]

        handler = rules_edit.textChanged.connect.call_args[0][0]

        rules_edit.textChanged.connect.assert_called_with(handler)
        sites_edit.textChanged.connect.assert_called_with(handler)
        prefix.textChanged.connect.assert_called_with(handler)
        series_prefix.textChanged.connect.assert_called_with(handler)
        add_source.toggled.connect.assert_called_with(handler)
        scan.toggled.connect.assert_called_with(handler)
        scope_selected.toggled.connect.assert_called_with(handler)
        scope_all.toggled.connect.assert_called_with(handler)


if __name__ == '__main__':
    unittest.main()
