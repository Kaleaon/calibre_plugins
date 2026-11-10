import importlib.util
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tagger'))

if 'calibre' not in sys.modules:
    calibre_mock = MagicMock()
    sys.modules['calibre'] = calibre_mock
    sys.modules['calibre.customize'] = calibre_mock
    sys.modules['calibre.utils'] = calibre_mock
    sys.modules['calibre.utils.config'] = calibre_mock

spec = importlib.util.spec_from_file_location('presets', Path(__file__).resolve().parents[1] / 'tagger/presets.py')
presets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(presets)


class TestTaggerPresets(unittest.TestCase):
    def test_load_bundled_presets(self):
        loaded = presets.load_bundled_presets()
        self.assertGreaterEqual(len(loaded), 4)
        names = [p['name'] for p in loaded]
        categories = [p['category'] for p in loaded]
        self.assertIn('LitRPG & Progression Fantasy', names)
        self.assertIn('Sci-Fi & Cyberpunk', names)
        self.assertIn('Story & Character Tropes', names)
        self.assertIn('Web Serials & Light Novels', names)
        self.assertIn('Genres', categories)
        self.assertIn('Tropes', categories)
        self.assertIn('Web Formats', categories)

    def test_rule_dict_string_conversions(self):
        rule_str = "tags:=Muscle Growth => Theme.Growth, Growth"
        d = presets.rule_string_to_dict(rule_str)
        self.assertEqual(d['field'], 'tags')
        self.assertEqual(d['match_mode'], 'exact')
        self.assertEqual(d['pattern'], 'Muscle Growth')
        self.assertEqual(d['tags'], ['Theme.Growth', 'Growth'])

        reconstructed = presets.rule_dict_to_string(d)
        self.assertEqual(reconstructed, rule_str)

    def test_rule_dict_regex_and_substring(self):
        r_str = "re:litrpg|progression => LitRPG"
        d = presets.rule_string_to_dict(r_str)
        self.assertEqual(d['field'], 'any')
        self.assertEqual(d['match_mode'], 'regex')
        self.assertEqual(d['pattern'], 'litrpg|progression')
        self.assertEqual(d['tags'], ['LitRPG'])

        s_str = "source:Royal Road => Format.Web Serial"
        d2 = presets.rule_string_to_dict(s_str)
        self.assertEqual(d2['field'], 'source')
        self.assertEqual(d2['match_mode'], 'substring')
        self.assertEqual(d2['pattern'], 'Royal Road')

    def test_validate_rule_dict(self):
        valid_r = {'field': 'any', 'match_mode': 'regex', 'pattern': 'litrpg|progression', 'tags': ['LitRPG']}
        ok, err = presets.validate_rule_dict(valid_r)
        self.assertTrue(ok)
        self.assertEqual(err, "")

        invalid_regex = {'field': 'any', 'match_mode': 'regex', 'pattern': '((invalid', 'tags': ['LitRPG']}
        ok, err = presets.validate_rule_dict(invalid_regex)
        self.assertFalse(ok)
        self.assertIn('Invalid regular expression', err)

        empty_pattern = {'field': 'any', 'match_mode': 'substring', 'pattern': '', 'tags': ['LitRPG']}
        ok, err = presets.validate_rule_dict(empty_pattern)
        self.assertFalse(ok)

        empty_tags = {'field': 'any', 'match_mode': 'substring', 'pattern': 'litrpg', 'tags': []}
        ok, err = presets.validate_rule_dict(empty_tags)
        self.assertFalse(ok)

    def test_deduplicate_rules(self):
        existing = [
            {'field': 'tags', 'match_mode': 'exact', 'pattern': 'Muscle Growth', 'tags': ['Theme.Growth']},
            {'field': 'source', 'match_mode': 'substring', 'pattern': 'Royal Road', 'tags': ['Format.Web Serial']}
        ]
        incoming = [
            # Exact duplicate -> skipped
            {'field': 'tags', 'match_mode': 'exact', 'pattern': 'Muscle Growth', 'tags': ['Theme.Growth']},
            # Same pattern, new tag -> tag added to existing rule
            {'field': 'tags', 'match_mode': 'exact', 'pattern': 'Muscle Growth', 'tags': ['Growth']},
            # Brand new rule -> added
            {'field': 'any', 'match_mode': 'regex', 'pattern': 'litrpg', 'tags': ['Genre.LitRPG']}
        ]

        merged, added = presets.deduplicate_rules(existing, incoming)
        self.assertEqual(len(merged), 3)
        # Check Muscle Growth rule has both tags now
        self.assertEqual(merged[0]['tags'], ['Theme.Growth', 'Growth'])
        self.assertEqual(merged[2]['pattern'], 'litrpg')

    def test_export_and_parse_preset_json(self):
        rules_list = [{'field': 'any', 'match_mode': 'regex', 'pattern': 'litrpg', 'tags': ['Genre.LitRPG']}]
        sites_list = [('Custom Site', ['custom.com'], [])]

        exported_json = presets.export_preset_json('My Custom Preset', 'Custom', 'Test Description', rules_list, sites_list)
        parsed = presets.parse_preset_json(exported_json)

        self.assertEqual(parsed['name'], 'My Custom Preset')
        self.assertEqual(parsed['category'], 'Custom')
        self.assertEqual(parsed['description'], 'Test Description')
        self.assertEqual(len(parsed['rules']), 1)
        self.assertEqual(parsed['rules'][0]['pattern'], 'litrpg')
        self.assertEqual(len(parsed['sites']), 1)
        self.assertEqual(parsed['sites'][0][0], 'Custom Site')

    def test_merge_preset_into_active(self):
        active_rules = "source:Royal Road => Format.Web Serial"
        active_sites = "storiesonline.net => Stories Online"

        preset_data = {
            'rules': [
                {'field': 'source', 'match_mode': 'substring', 'pattern': 'Royal Road', 'tags': ['Format.Web Serial']}, # Duplicate
                {'field': 'any', 'match_mode': 'regex', 'pattern': 'litrpg', 'tags': ['Genre.LitRPG']} # New
            ],
            'sites': [
                ('Stories Online', ['storiesonline.net'], []), # Duplicate
                ('My New Site', ['newsite.org'], []) # New
            ]
        }

        up_rules, up_sites, r_added, s_added = presets.merge_preset_into_active(active_rules, active_sites, preset_data)
        self.assertIn("Genre.LitRPG", up_rules)
        self.assertIn("newsite.org => My New Site", up_sites)
        self.assertEqual(r_added, 1)
        self.assertEqual(s_added, 1)


if __name__ == '__main__':
    unittest.main()
