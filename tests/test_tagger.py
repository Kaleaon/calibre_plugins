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
        tagger_pkg.config = MagicMock(prefs={'add_source': True, 'source_prefix': 'Source.', 'series_prefix': 'Series.', 'scan_comments': False, 'rules': '', 'sites': ''})
        tagger_pkg.rules = rules
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.story_collection_tagger'] = tagger_pkg
        sys.modules['calibre_plugins.story_collection_tagger.config'] = tagger_pkg.config
        sys.modules['calibre_plugins.story_collection_tagger.rules'] = tagger_pkg.rules

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


class RuleSetSchemaTests(unittest.TestCase):
    def test_export_and_import_roundtrip(self):
        rules_text = "tags:=Muscle Growth => Theme.Growth\nlitrpg => LitRPG"
        sites_text = "storiesonline.net, sol.net => Stories Online"
        data = rules.export_rule_set_data(rules_text, sites_text, title="Test Set")
        self.assertEqual(data["version"], 1)
        self.assertEqual(len(data["rules"]), 2)
        self.assertEqual(len(data["sites"]), 1)

        imp_rules, imp_sites, errors = rules.import_rule_set_data(data)
        self.assertEqual(errors, [])
        self.assertEqual(len(imp_rules), 2)
        self.assertEqual(len(imp_sites), 1)
        self.assertEqual(imp_rules[0].tags, ["Theme.Growth"])
        self.assertEqual(imp_sites[0][0], "Stories Online")

    def test_import_rejects_unsupported_version(self):
        data = {"version": 99, "rules": []}
        imp_rules, imp_sites, errors = rules.import_rule_set_data(data)
        self.assertEqual(imp_rules, [])
        self.assertIn("Unsupported schema version '99'", errors[0])

    def test_import_rejects_malformed_json_structure(self):
        imp_rules, imp_sites, errors = rules.import_rule_set_data("not a dict")
        self.assertIn("Invalid rule set data", errors[0])

    def test_import_rejects_invalid_rule_syntax(self):
        data = {
            "version": 1,
            "rules": ["re:(( => Bad Regex"]
        }
        imp_rules, imp_sites, errors = rules.import_rule_set_data(data)
        self.assertEqual(imp_rules, [])
        self.assertTrue(any("bad regular expression" in e for e in errors))

    def test_classify_rule_duplicate_conflict_new(self):
        parsed_local, _ = rules.parse_rules("litrpg => LitRPG\ntags:=Muscle Growth => Theme.Growth")
        inc_new, _ = rules.parse_rules("action => Action")
        inc_dup, _ = rules.parse_rules("litrpg => LitRPG")
        inc_conf, _ = rules.parse_rules("litrpg => LitRPG, Game")

        self.assertEqual(rules.classify_rule(inc_new[0], parsed_local), "New")
        self.assertEqual(rules.classify_rule(inc_dup[0], parsed_local), "Duplicate")
        self.assertEqual(rules.classify_rule(inc_conf[0], parsed_local), "Conflict")

    def test_classify_site_duplicate_conflict_new(self):
        local_sites, _ = rules.parse_sites("storiesonline.net => Stories Online")
        inc_new, _ = rules.parse_sites("mysite.org => My Site")
        inc_dup, _ = rules.parse_sites("storiesonline.net => Stories Online")
        inc_conf, _ = rules.parse_sites("storiesonline.net => SOL New Name")

        self.assertEqual(rules.classify_site(inc_new[0], local_sites), "New")
        self.assertEqual(rules.classify_site(inc_dup[0], local_sites), "Duplicate")
        self.assertEqual(rules.classify_site(inc_conf[0], local_sites), "Conflict")


class RuleSetManagerDialogTests(unittest.TestCase):
    def setUp(self):
        self.saved_modules = dict(sys.modules)
        for name in ('calibre', 'calibre.gui2', 'calibre.gui2.actions'):
            sys.modules[name] = MagicMock()
        sys.modules['calibre.gui2.actions'].InterfaceAction = object

        class StubDialog(object):
            def __init__(self, *a, **k): pass
            def setWindowTitle(self, t): pass
            def resize(self, w, h): pass
            def accept(self): pass
            def reject(self): pass

        qt_mod = MagicMock()
        qt_mod.core.QDialog = StubDialog
        sys.modules['qt'] = qt_mod
        sys.modules['qt.core'] = qt_mod.core

        tagger_pkg = types.ModuleType('calibre_plugins.story_collection_tagger')
        tagger_pkg.__path__ = [str(Path(__file__).resolve().parents[1] / 'tagger')]
        tagger_pkg.config = MagicMock(prefs={'add_source': True, 'source_prefix': 'Source.', 'series_prefix': 'Series.', 'scan_comments': False, 'rules': 'litrpg => LitRPG', 'sites': 'sol.net => Stories Online'})
        tagger_pkg.rules = rules
        sys.modules['calibre_plugins'] = types.ModuleType('calibre_plugins')
        sys.modules['calibre_plugins.story_collection_tagger'] = tagger_pkg
        sys.modules['calibre_plugins.story_collection_tagger.config'] = tagger_pkg.config
        sys.modules['calibre_plugins.story_collection_tagger.rules'] = tagger_pkg.rules

    def tearDown(self):
        for k in list(sys.modules.keys()):
            if k not in self.saved_modules:
                del sys.modules[k]
        sys.modules.update(self.saved_modules)

    def test_manager_dialog_initialization_and_commit(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        dlg = action_mod.RuleSetManagerDialog(None, current_rules="litrpg => LitRPG", current_sites="sol.net => Stories Online")
        self.assertEqual(len(dlg.local_rules), 1)
        self.assertEqual(len(dlg.local_sites), 1)

        rules_text, sites_text = dlg.get_committed_rules_and_sites()
        self.assertEqual(rules_text, "litrpg => LitRPG")
        self.assertEqual(sites_text, "sol.net => Stories Online")

    def test_manager_dialog_1000_rules_performance(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        many_rules = "\n".join([f"pattern_{i} => Tag_{i}" for i in range(1000)])
        import time
        start = time.time()
        dlg = action_mod.RuleSetManagerDialog(None, current_rules=many_rules, current_sites="")
        elapsed = time.time() - start

        self.assertEqual(len(dlg.local_rules), 1000)
        self.assertLess(elapsed, 2.0, f"Table population for 1000 rules took too long ({elapsed:.2f}s)")

    def test_manager_dialog_unchecked_rules_are_excluded(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        dlg = action_mod.RuleSetManagerDialog(None, current_rules="rule1 => Tag1\nrule2 => Tag2", current_sites="site1.com => Site1\nsite2.com => Site2")

        # Mock table item check state for row 1 to be Unchecked
        mock_chk_checked = MagicMock()
        mock_chk_checked.checkState.return_value = action_mod.Qt.CheckState.Checked
        mock_chk_unchecked = MagicMock()
        mock_chk_unchecked.checkState.return_value = action_mod.Qt.CheckState.Unchecked

        def get_item(row, col):
            item = MagicMock()
            if col == 0:
                return mock_chk_checked if row == 0 else mock_chk_unchecked
            elif col == 1:
                item.text.return_value = "(default)" if row < 2 else "site1.com"
            elif col == 2:
                item.text.return_value = f"rule{row+1}" if row < 2 else "Site1"
            elif col == 3:
                item.text.return_value = f"Tag{row+1}"
            return item

        dlg.rules_table.rowCount = MagicMock(return_value=2)
        dlg.rules_table.item = MagicMock(side_effect=get_item)

        dlg.sites_table.rowCount = MagicMock(return_value=1)
        dlg.sites_table.item = MagicMock(side_effect=get_item)

        rules_out, sites_out = dlg._get_checked_rules_and_sites_text()
        self.assertEqual(rules_out, "rule1 => Tag1")
        self.assertNotIn("rule2", rules_out)

    def test_manager_dialog_syntax_error_blocks_accept(self):
        spec = importlib.util.spec_from_file_location('action', Path(__file__).resolve().parents[1] / 'tagger/action.py')
        action_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(action_mod)

        dlg = action_mod.RuleSetManagerDialog(None, current_rules="", current_sites="")
        dlg._get_checked_rules_and_sites_text = MagicMock(return_value=("re:(( => Bad Regex", ""))
        dlg.accept = MagicMock()

        dlg._on_accept()
        dlg.accept.assert_not_called()


if __name__ == '__main__':
    unittest.main()
