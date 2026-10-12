"""Tests for report module."""

import unittest
from html.parser import HTMLParser
from pathlib import Path
from repo_xray.analyzer import analyze
from repo_xray.report import summary, render_html, render_markdown


class TestReport(unittest.TestCase):
    """Test report generation."""
    
    def setUp(self):
        """Set up test fixtures."""
        self.fixtures_dir = Path(__file__).parent / 'fixtures'
        self.sample_repo = self.fixtures_dir / 'sample_repo'
        self.hostile_repo = self.fixtures_dir / 'hostile_repo'
    
    def test_sample_repo_html_contains_modules(self):
        """Sample repo HTML contains every module name and expected sections."""
        model = analyze(self.sample_repo)
        html = render_html(model, "Test Report")
        
        # Assert non-empty
        self.assertNotEqual(html, "")
        self.assertGreater(len(html), 100)
        
        # Check for all module names
        for mod_name in model['modules'].keys():
            self.assertIn(mod_name, html)
        
        # Check for expected sections
        self.assertIn("Unresolved / dynamic", html)
        self.assertIn("inferred", html)
        
        # Check for exactly one SVG
        self.assertEqual(html.count('<svg'), 1)
        
        # Verify it parses without error
        parser = HTMLParser()
        parser.feed(html)
    
    def test_sample_repo_html_no_dangerous_content(self):
        """Sample repo HTML contains no scripts, http links, or secrets."""
        model = analyze(self.sample_repo)
        html = render_html(model, "Test Report")
        
        # Assert non-empty
        self.assertNotEqual(html, "")
        
        # No dangerous content
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("http", html.lower())
        self.assertNotIn("javascript:", html.lower())
        
        # No secrets or config files mentioned
        self.assertNotIn("samplepkg.plugins", html)
        self.assertNotIn("do-not-read", html)
    
    def test_hostile_repo_html_escaping(self):
        """Hostile repo HTML properly escapes malicious content."""
        model = analyze(self.hostile_repo)
        html = render_html(model, "Hostile Test")
        
        # Assert non-empty
        self.assertNotEqual(html, "")
        
        # Check escaped content is present
        self.assertIn("&lt;script&gt;", html)
        self.assertIn("&lt;img", html)
        
        # Check unescaped tags are NOT present
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("<img", html.lower())
    
    def test_hostile_repo_markdown_escaping(self):
        """Hostile repo Markdown properly escapes malicious content."""
        model = analyze(self.hostile_repo)
        md = render_markdown(model, "Hostile Test")
        
        # Assert non-empty
        self.assertNotEqual(md, "")
        
        # Check escaped content
        self.assertIn("&lt;script&gt;", md)
        
        # Check unescaped tags are NOT present
        self.assertNotIn("<script", md)
    
    def test_summary_deterministic(self):
        """Summary is non-empty, mentions expected modules, and is deterministic."""
        model = analyze(self.sample_repo)
        
        sum1 = summary(model)
        sum2 = summary(model)
        
        # Assert non-empty
        self.assertNotEqual(sum1, [])
        self.assertGreater(len(sum1), 0)
        
        # Check for expected content
        summary_text = ' '.join(sum1)
        self.assertIn("samplepkg.util", summary_text)
        
        # Check determinism
        self.assertEqual(sum1, sum2)
    
    def test_render_html_deterministic(self):
        """HTML rendering is deterministic."""
        model = analyze(self.sample_repo)
        
        html1 = render_html(model, "Test")
        html2 = render_html(model, "Test")
        
        # Assert non-empty
        self.assertNotEqual(html1, "")
        
        # Check determinism
        self.assertEqual(html1, html2)
    
    def test_graph_omitted_for_large_repos(self):
        """Graph is omitted for repos with >150 internal modules."""
        # Create a fake model with 151 modules
        model = {
            'files_scanned': 151,
            'packages': [],
            'modules': {},
            'entry_points': [],
            'tests': [],
            'unresolved': [],
            'parse_errors': []
        }
        
        for i in range(151):
            mod_name = f'module{i}'
            model['modules'][mod_name] = {
                'path': f'{mod_name}.py',
                'is_package': False,
                'docstring': '',
                'classes': [],
                'functions': [],
                'imports': [],
                'has_main_guard': False
            }
        
        html = render_html(model, "Large Repo")
        
        # Assert non-empty
        self.assertNotEqual(html, "")
        
        # Check for omission message
        self.assertIn("Graph omitted", html)
        self.assertIn("151 modules", html)
        
        # No SVG should be present
        self.assertNotIn("<svg", html)


if __name__ == '__main__':
    unittest.main()
