"""Tests for askdata report generation."""
import unittest
import tempfile
from pathlib import Path
from html.parser import HTMLParser

from askdata.report import render_terminal, render_md, render_html
from tests.askdata_fixtures import clean_csv, messy_csv, fk_db, hostile_db


class TestReports(unittest.TestCase):
    """Test report generation for CSV and SQLite."""
    
    def test_hostile_db_html_no_script(self):
        """Hostile database HTML has no script tags and escapes dangerous content."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.sqliteinspect import inspect_sqlite
            path = hostile_db(tmpdir)
            model = inspect_sqlite(path)
            
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            # No script or img tags
            self.assertNotIn('<script', html_output.lower())
            self.assertNotIn('<img', html_output.lower())
            
            # Check escaped content
            self.assertIn('t&quot;&lt;script&gt;', html_output)
            self.assertIn('&lt;img src=x onerror=alert(1)&gt;', html_output)
    
    def test_html_only_safe_tags(self):
        """HTML output only contains safe tags."""
        allowed_tags = {
            'html', 'head', 'meta', 'title', 'style', 'body', 'header',
            'section', 'h1', 'h2', 'h3', 'p', 'table', 'thead', 'tbody',
            'tr', 'th', 'td', 'ul', 'li', 'a', 'code', 'span', 'div',
            'strong', 'br'
        }
        
        class TagCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.tags = set()
            
            def handle_starttag(self, tag, attrs):
                self.tags.add(tag)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.csvinspect import inspect_csv
            from askdata.sqliteinspect import inspect_sqlite
            
            # Test clean_csv
            path = clean_csv(tmpdir)
            model = inspect_csv(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = TagCollector()
            parser.feed(html_output)
            self.assertTrue(parser.tags.issubset(allowed_tags),
                          f"clean_csv: Found disallowed tags: {parser.tags - allowed_tags}")
            
            # Test messy_csv
            path = messy_csv(tmpdir)
            model = inspect_csv(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = TagCollector()
            parser.feed(html_output)
            self.assertTrue(parser.tags.issubset(allowed_tags),
                          f"messy_csv: Found disallowed tags: {parser.tags - allowed_tags}")
            
            # Test fk_db
            path = fk_db(tmpdir)
            model = inspect_sqlite(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = TagCollector()
            parser.feed(html_output)
            self.assertTrue(parser.tags.issubset(allowed_tags),
                          f"fk_db: Found disallowed tags: {parser.tags - allowed_tags}")
            
            # Test hostile_db
            path = hostile_db(tmpdir)
            model = inspect_sqlite(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = TagCollector()
            parser.feed(html_output)
            self.assertTrue(parser.tags.issubset(allowed_tags),
                          f"hostile_db: Found disallowed tags: {parser.tags - allowed_tags}")
    
    def test_html_anchors_internal_only(self):
        """All <a href> attributes start with #."""
        class HrefCollector(HTMLParser):
            def __init__(self):
                super().__init__()
                self.hrefs = []
            
            def handle_starttag(self, tag, attrs):
                if tag == 'a':
                    for name, value in attrs:
                        if name == 'href':
                            self.hrefs.append(value)
        
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.csvinspect import inspect_csv
            from askdata.sqliteinspect import inspect_sqlite
            
            # Test clean_csv
            path = clean_csv(tmpdir)
            model = inspect_csv(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = HrefCollector()
            parser.feed(html_output)
            for href in parser.hrefs:
                self.assertTrue(href.startswith('#'),
                              f"clean_csv: Found non-internal href: {href}")
            
            # Test messy_csv
            path = messy_csv(tmpdir)
            model = inspect_csv(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = HrefCollector()
            parser.feed(html_output)
            for href in parser.hrefs:
                self.assertTrue(href.startswith('#'),
                              f"messy_csv: Found non-internal href: {href}")
            
            # Test fk_db
            path = fk_db(tmpdir)
            model = inspect_sqlite(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = HrefCollector()
            parser.feed(html_output)
            for href in parser.hrefs:
                self.assertTrue(href.startswith('#'),
                              f"fk_db: Found non-internal href: {href}")
            
            # Test hostile_db
            path = hostile_db(tmpdir)
            model = inspect_sqlite(path)
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            parser = HrefCollector()
            parser.feed(html_output)
            for href in parser.hrefs:
                self.assertTrue(href.startswith('#'),
                              f"hostile_db: Found non-internal href: {href}")
    
    def test_clean_csv_warnings_none(self):
        """Clean CSV shows 'none' in warnings section for all formats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.csvinspect import inspect_csv
            path = clean_csv(tmpdir)
            model = inspect_csv(path)
            
            terminal_output = render_terminal(model)
            self.assertNotEqual(terminal_output, "")
            self.assertIn('Warnings:', terminal_output)
            self.assertIn('none', terminal_output.split('Warnings:')[1])
            
            md_output = render_md(model)
            self.assertNotEqual(md_output, "")
            self.assertIn('## Warnings', md_output)
            self.assertIn('none', md_output.split('## Warnings')[1])
            
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            self.assertIn('<h2>Warnings</h2>', html_output)
            self.assertIn('<p>none</p>', html_output)
    
    def test_clean_csv_html_tok_classes(self):
        """Clean CSV HTML contains 1-5 distinct tok-<n> values in data, none in style."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.csvinspect import inspect_csv
            path = clean_csv(tmpdir)
            model = inspect_csv(path)
            
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            
            # Find all tok-N patterns
            import re
            tok_matches = re.findall(r'tok-(\d+)', html_output)
            tok_classes = set(tok_matches)
            
            # Should have 1-5 distinct values
            self.assertGreaterEqual(len(tok_classes), 1)
            self.assertLessEqual(len(tok_classes), 5)
            
            
            # Style block should not contain "tok-"
            style_start = html_output.find('<style>')
            style_end = html_output.find('</style>')
            if style_start != -1 and style_end != -1:
                style_block = html_output[style_start:style_end]
                self.assertNotIn('tok-', style_block)
    
    def test_messy_csv_malformed_and_encoding(self):
        """Messy CSV shows malformed lines and encoding fallback in all formats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.csvinspect import inspect_csv
            path = messy_csv(tmpdir)
            model = inspect_csv(path)
            
            terminal_output = render_terminal(model)
            self.assertNotEqual(terminal_output, "")
            self.assertIn('[5, 9]', terminal_output)
            self.assertIn('encoding fallback', terminal_output)
            
            md_output = render_md(model)
            self.assertNotEqual(md_output, "")
            self.assertIn('[5, 9]', md_output)
            self.assertIn('encoding fallback', md_output)
            
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            self.assertIn('[5, 9]', html_output)
            self.assertIn('encoding fallback', html_output)
    
    def test_control_char_escaping(self):
        """Control characters are escaped in terminal and markdown output."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create CSV with control character in value
            csv_path = Path(tmpdir) / "control.csv"
            with open(csv_path, 'w', encoding='utf-8') as f:
                f.write('a|b,value\n')
                f.write('x\x1b[31mred,123\n')
            
            from askdata.csvinspect import inspect_csv
            model = inspect_csv(csv_path)
            
            terminal_output = render_terminal(model)
            self.assertNotEqual(terminal_output, "")
            # ESC character should be escaped
            self.assertNotIn('\x1b', terminal_output)
            self.assertIn(r'\x1b', terminal_output)
            
            md_output = render_md(model)
            self.assertNotEqual(md_output, "")
            # ESC character should be escaped
            self.assertNotIn('\x1b', md_output)
            self.assertIn(r'\x1b', md_output)
            # Pipe in header should be escaped
            self.assertIn(r'a\|b', md_output)
    
    def test_fk_db_relationships_and_indexes(self):
        """Foreign key database shows relationships and indexes in all formats."""
        with tempfile.TemporaryDirectory() as tmpdir:
            from askdata.sqliteinspect import inspect_sqlite
            path = fk_db(tmpdir)
            model = inspect_sqlite(path)
            
            terminal_output = render_terminal(model)
            self.assertNotEqual(terminal_output, "")
            self.assertIn('orders.customer_id -> customers.id', terminal_output)
            self.assertIn('idx_orders_customer', terminal_output)
            
            md_output = render_md(model)
            self.assertNotEqual(md_output, "")
            self.assertIn('orders.customer_id -> customers.id', md_output)
            self.assertIn('idx_orders_customer', md_output)
            
            html_output = render_html(model)
            self.assertNotEqual(html_output, "")
            self.assertIn('orders.customer_id -> customers.id', html_output)
            self.assertIn('idx_orders_customer', html_output)
    
    def test_newline_and_bidi_escaping(self):
        """CSV with newline and bidi control in quoted cell is escaped in terminal and md."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create CSV with newline and bidi control in quoted value
            csv_path = Path(tmpdir) / "special.csv"
            with open(csv_path, 'w', encoding='utf-8', newline='') as f:
                f.write('name,value\n')
                f.write('"test\nline",123\n')
                f.write('"\u202eRTL",456\n')
            
            from askdata.csvinspect import inspect_csv
            model = inspect_csv(csv_path)
            
            terminal_output = render_terminal(model)
            self.assertNotEqual(terminal_output, "")
            
            # Find the samples section for the name column
            # Should contain escaped newline, not raw newline
            self.assertIn(r'\x0a', terminal_output)
            # Should not contain raw newline in the value
            lines = terminal_output.split('\n')
            for line in lines:
                if 'Samples:' in line and 'test' in line:
                    self.assertNotIn('test\nline', line)
            
            # Should contain escaped bidi control
            self.assertIn(r'\u202e', terminal_output)
            # Should not contain raw bidi control
            self.assertNotIn('\u202e', terminal_output)
            
            md_output = render_md(model)
            self.assertNotEqual(md_output, "")
            
            # Should contain escaped newline
            self.assertIn(r'\x0a', md_output)
            # Should not contain raw newline in table cell
            md_lines = md_output.split('\n')
            for line in md_lines:
                if line.startswith('|') and 'test' in line:
                    # This is a table row - should not have embedded newline
                    self.assertEqual(line.count('\n'), 0)
            
            # Should contain escaped bidi control
            self.assertIn(r'\u202e', md_output)
            # Should not contain raw bidi control
            self.assertNotIn('\u202e', md_output)


if __name__ == '__main__':
    unittest.main()
