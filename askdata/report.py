"""Report generation for data file inspection."""
import html
import re
import unicodedata


def _escape_terminal(s):
    """Escape control characters for terminal output.
    
    Replaces control characters (Unicode category Cc except newline) with \xNN form.
    """
    s = str(s)
    result = []
    for char in s:
        if unicodedata.category(char) == 'Cc' and char != '\n':
            result.append(f'\\x{ord(char):02x}')
        else:
            result.append(char)
    return ''.join(result)


def _escape_md(s):
    """Escape for markdown output.
    
    Escapes control characters, and in table cells also escapes | and `.
    """
    return _escape_terminal(s)


def _escape_md_cell(s):
    """Escape for markdown table cell.
    
    Escapes control characters, |, `, and converts newlines to spaces.
    """
    s = _escape_terminal(s)
    s = s.replace('|', r'\|')
    s = s.replace('`', r'\`')
    s = s.replace('\n', ' ')
    return s


def _escape_html(s):
    """Escape for HTML output."""
    return html.escape(str(s), quote=True)


def _format_bytes(n):
    """Format byte count as human-readable string."""
    if n < 1024:
        return f"{n} bytes"
    elif n < 1024 * 1024:
        return f"{n:,} bytes ({n/1024:.1f} KB)"
    elif n < 1024 * 1024 * 1024:
        return f"{n:,} bytes ({n/(1024*1024):.1f} MB)"
    else:
        return f"{n:,} bytes ({n/(1024*1024*1024):.1f} GB)"


def _round_sig(value, sig=4):
    """Round to significant digits."""
    if value == 0:
        return 0.0
    from math import log10, floor
    return round(value, -int(floor(log10(abs(value)))) + (sig - 1))


def render_terminal(model):
    """Render inspection results as terminal text.
    
    Args:
        model: Dictionary from inspect_csv or inspect_sqlite
        
    Returns:
        String with terminal-safe text output
    """
    lines = []
    kind = model["kind"]
    
    # Header
    lines.append(f"Name: {_escape_terminal(model['name'])}")
    lines.append(f"Kind: {kind}")
    lines.append(f"Size: {_format_bytes(model['size_bytes'])}")
    
    if kind == "csv":
        lines.append(f"Encoding: {model['encoding']}")
        lines.append(f"Delimiter: {repr(model['delimiter'])}")
        lines.append(f"Has header: {model['has_header']}")
        lines.append(f"Row count: {model['row_count']:,}")
        lines.append(f"Sampled: {model['sampled']}")
        lines.append(f"Malformed: {model['malformed']['count']:,}")
        if model['malformed']['first_lines']:
            lines.append(f"  First malformed lines: {model['malformed']['first_lines']}")
    else:  # sqlite
        table_count = len([t for t in model['tables'] if t['type'] == 'table'])
        view_count = len([t for t in model['tables'] if t['type'] == 'view'])
        total = table_count + view_count
        lines.append(f"Objects: {total} ({table_count} tables, {view_count} views)")
        
        lines.append("")
        lines.append("Relationships:")
        if model['relationships']:
            for rel in model['relationships']:
                lines.append(f"  {_escape_terminal(rel[0])} -> {_escape_terminal(rel[1])}")
        else:
            lines.append("  none")
    
    lines.append("")
    
    # Objects/Tables
    objects = model.get('tables', [model])  # CSV wraps itself
    if kind == "csv":
        objects = [model]
    
    for obj in objects:
        if kind == "sqlite":
            lines.append(f"## {_escape_terminal(obj['name'])} ({obj['type']})")
            lines.append(f"Row count: {obj['row_count']:,}")
            lines.append(f"Sampled: {obj['sampled']}")
            
            if obj.get('foreign_keys'):
                lines.append("Foreign keys:")
                for fk in obj['foreign_keys']:
                    lines.append(f"  {_escape_terminal(fk['from'])} -> {_escape_terminal(fk['table'])}.{_escape_terminal(fk['to'])}")
            
            if obj.get('indexes'):
                lines.append(f"Indexes: {', '.join(_escape_terminal(idx) for idx in obj['indexes'])}")
            
            lines.append("")
        
        # Column table
        columns = obj.get('columns', [])
        if columns:
            lines.append("Columns:")
            lines.append("")
            
            for col in columns:
                col_line = f"  {_escape_terminal(col['name'])}"
                
                # Type
                if col['type'] == 'mixed':
                    share_pct = int(col['share'] * 100)
                    col_line += f" | mixed ({col['candidate']} {share_pct}%)"
                else:
                    col_line += f" | {col['type']}"
                
                # SQLite extras
                if kind == "sqlite":
                    if col.get('declared_type'):
                        col_line += f" | {_escape_terminal(col['declared_type'])}"
                    if col.get('notnull'):
                        col_line += " | NOT NULL"
                    if col.get('pk'):
                        col_line += " | PK"
                
                lines.append(col_line)
                
                # Stats
                lines.append(f"    Missing: {col['missing']:,}")
                
                distinct_str = f"{col['distinct']:,}"
                if not col['distinct_exact']:
                    distinct_str = f"≥ {distinct_str} (sampled)"
                lines.append(f"    Distinct: {distinct_str}")
                
                if col['samples']:
                    samples_str = ', '.join(_escape_terminal(s) for s in col['samples'][:5])
                    lines.append(f"    Samples: {samples_str}")
                
                if col.get('numeric'):
                    num = col['numeric']
                    mean_r = _round_sig(num['mean'])
                    median_r = _round_sig(num['median'])
                    lines.append(f"    Numeric: {num['min']} / {num['max']} / {mean_r} / {median_r}")
                
                if col.get('dates'):
                    dates = col['dates']
                    lines.append(f"    Dates: {_escape_terminal(dates['min'])} – {_escape_terminal(dates['max'])}")
                
                if col.get('top'):
                    top_strs = [f"{_escape_terminal(val)} ({cnt})" for val, cnt in col['top']]
                    lines.append(f"    Top: {', '.join(top_strs)}")
                
                lines.append("")
    
    # Warnings
    lines.append("Warnings:")
    warnings = model.get('warnings', [])
    if warnings:
        for warning in warnings:
            lines.append(f"  - {_escape_terminal(warning)}")
    else:
        lines.append("  none")
    
    return '\n'.join(lines)


def render_md(model):
    """Render inspection results as Markdown.
    
    Args:
        model: Dictionary from inspect_csv or inspect_sqlite
        
    Returns:
        String with Markdown output
    """
    lines = []
    kind = model["kind"]
    
    # Header
    lines.append(f"# {_escape_md(model['name'])}")
    lines.append("")
    lines.append(f"**Kind:** {kind}")
    lines.append("")
    lines.append(f"**Size:** {_format_bytes(model['size_bytes'])}")
    lines.append("")
    
    if kind == "csv":
        lines.append(f"**Encoding:** {model['encoding']}")
        lines.append("")
        lines.append(f"**Delimiter:** {repr(model['delimiter'])}")
        lines.append("")
        lines.append(f"**Has header:** {model['has_header']}")
        lines.append("")
        lines.append(f"**Row count:** {model['row_count']:,}")
        lines.append("")
        lines.append(f"**Sampled:** {model['sampled']}")
        lines.append("")
        lines.append(f"**Malformed:** {model['malformed']['count']:,}")
        if model['malformed']['first_lines']:
            lines.append(f" (first lines: {model['malformed']['first_lines']})")
        lines.append("")
    else:  # sqlite
        table_count = len([t for t in model['tables'] if t['type'] == 'table'])
        view_count = len([t for t in model['tables'] if t['type'] == 'view'])
        total = table_count + view_count
        lines.append(f"**Objects:** {total} ({table_count} tables, {view_count} views)")
        lines.append("")
        
        lines.append("## Relationships")
        lines.append("")
        if model['relationships']:
            for rel in model['relationships']:
                lines.append(f"- {_escape_md(rel[0])} -> {_escape_md(rel[1])}")
        else:
            lines.append("none")
        lines.append("")
    
    # Objects/Tables
    objects = model.get('tables', [model])
    if kind == "csv":
        objects = [model]
    
    for obj in objects:
        if kind == "sqlite":
            lines.append(f"## {_escape_md(obj['name'])} ({obj['type']})")
            lines.append("")
            lines.append(f"**Row count:** {obj['row_count']:,}")
            lines.append("")
            lines.append(f"**Sampled:** {obj['sampled']}")
            lines.append("")
            
            if obj.get('foreign_keys'):
                lines.append("**Foreign keys:**")
                lines.append("")
                for fk in obj['foreign_keys']:
                    lines.append(f"- {_escape_md(fk['from'])} -> {_escape_md(fk['table'])}.{_escape_md(fk['to'])}")
                lines.append("")
            
            if obj.get('indexes'):
                idx_list = ', '.join(_escape_md(idx) for idx in obj['indexes'])
                lines.append(f"**Indexes:** {idx_list}")
                lines.append("")
        
        # Column table
        columns = obj.get('columns', [])
        if columns:
            if kind == "sqlite":
                lines.append("### Columns")
                lines.append("")
            else:
                lines.append("## Columns")
                lines.append("")
            
            # Table header
            if kind == "sqlite":
                lines.append("| Name | Type | Declared | Missing | Distinct | Samples | Stats |")
                lines.append("|------|------|----------|---------|----------|---------|-------|")
            else:
                lines.append("| Name | Type | Missing | Distinct | Samples | Stats |")
                lines.append("|------|------|---------|----------|---------|-------|")
            
            for col in columns:
                row = []
                
                # Name
                row.append(_escape_md_cell(col['name']))
                
                # Type
                if col['type'] == 'mixed':
                    share_pct = int(col['share'] * 100)
                    row.append(f"mixed ({col['candidate']} {share_pct}%)")
                else:
                    row.append(col['type'])
                
                # SQLite declared type + flags
                if kind == "sqlite":
                    decl_parts = []
                    if col.get('declared_type'):
                        decl_parts.append(_escape_md_cell(col['declared_type']))
                    if col.get('notnull'):
                        decl_parts.append('NOT NULL')
                    if col.get('pk'):
                        decl_parts.append('PK')
                    row.append(' '.join(decl_parts) if decl_parts else '')
                
                # Missing
                row.append(f"{col['missing']:,}")
                
                # Distinct
                distinct_str = f"{col['distinct']:,}"
                if not col['distinct_exact']:
                    distinct_str = f"≥ {distinct_str} (sampled)"
                row.append(distinct_str)
                
                # Samples
                if col['samples']:
                    samples_str = ', '.join(_escape_md_cell(s) for s in col['samples'][:5])
                    row.append(samples_str)
                else:
                    row.append('')
                
                # Stats (numeric, dates, top)
                stats_parts = []
                if col.get('numeric'):
                    num = col['numeric']
                    mean_r = _round_sig(num['mean'])
                    median_r = _round_sig(num['median'])
                    stats_parts.append(f"{num['min']} / {num['max']} / {mean_r} / {median_r}")
                
                if col.get('dates'):
                    dates = col['dates']
                    stats_parts.append(f"{_escape_md_cell(dates['min'])} – {_escape_md_cell(dates['max'])}")
                
                if col.get('top'):
                    top_strs = [f"{_escape_md_cell(val)} ({cnt})" for val, cnt in col['top']]
                    stats_parts.append('; '.join(top_strs))
                
                row.append(' '.join(stats_parts))
                
                lines.append('| ' + ' | '.join(row) + ' |')
            
            lines.append("")
    
    # Warnings
    lines.append("## Warnings")
    lines.append("")
    warnings = model.get('warnings', [])
    if warnings:
        for warning in warnings:
            lines.append(f"- {_escape_md(warning)}")
    else:
        lines.append("none")
    
    return '\n'.join(lines)


def render_html(model):
    """Render inspection results as HTML.
    
    Args:
        model: Dictionary from inspect_csv or inspect_sqlite
        
    Returns:
        String with complete HTML document
    """
    lines = []
    kind = model["kind"]
    
    # HTML header
    lines.append('<!DOCTYPE html>')
    lines.append('<html>')
    lines.append('<head>')
    lines.append('<meta charset="utf-8">')
    lines.append(f'<title>{_escape_html(model["name"])}</title>')
    lines.append('<style>')
    lines.append('body { font-family: sans-serif; max-width: 1200px; margin: 2em auto; padding: 0 1em; }')
    lines.append('header { border-bottom: 2px solid #333; padding-bottom: 1em; margin-bottom: 2em; }')
    lines.append('h1 { margin: 0; }')
    lines.append('h2 { margin-top: 2em; border-bottom: 1px solid #ccc; padding-bottom: 0.3em; }')
    lines.append('h3 { margin-top: 1.5em; }')
    lines.append('table { border-collapse: collapse; width: 100%; margin: 1em 0; }')
    lines.append('th, td { border: 1px solid #ddd; padding: 0.5em; text-align: left; }')
    lines.append('th { background: #f5f5f5; font-weight: bold; }')
    lines.append('code { background: #f5f5f5; padding: 0.2em 0.4em; border-radius: 3px; }')
    lines.append('.tok-1 { color: #0066cc; }')
    lines.append('.tok-2 { color: #009900; }')
    lines.append('.tok-3 { color: #cc6600; }')
    lines.append('.tok-4 { color: #990099; }')
    lines.append('.tok-5 { color: #cc0000; }')
    lines.append('ul { line-height: 1.6; }')
    lines.append('</style>')
    lines.append('</head>')
    lines.append('<body>')
    
    # Header section
    lines.append('<header>')
    lines.append(f'<h1>{_escape_html(model["name"])}</h1>')
    lines.append(f'<p><strong>Kind:</strong> {kind}</p>')
    lines.append(f'<p><strong>Size:</strong> {_format_bytes(model["size_bytes"])}</p>')
    
    if kind == "csv":
        lines.append(f'<p><strong>Encoding:</strong> {model["encoding"]}</p>')
        lines.append(f'<p><strong>Delimiter:</strong> {_escape_html(repr(model["delimiter"]))}</p>')
        lines.append(f'<p><strong>Has header:</strong> {model["has_header"]}</p>')
        lines.append(f'<p><strong>Row count:</strong> {model["row_count"]:,}</p>')
        lines.append(f'<p><strong>Sampled:</strong> {model["sampled"]}</p>')
        lines.append(f'<p><strong>Malformed:</strong> {model["malformed"]["count"]:,}')
        if model['malformed']['first_lines']:
            lines.append(f' (first lines: {model["malformed"]["first_lines"]})')
        lines.append('</p>')
    else:  # sqlite
        table_count = len([t for t in model['tables'] if t['type'] == 'table'])
        view_count = len([t for t in model['tables'] if t['type'] == 'view'])
        total = table_count + view_count
        lines.append(f'<p><strong>Objects:</strong> {total} ({table_count} tables, {view_count} views)</p>')
        
        lines.append('<h2>Relationships</h2>')
        if model['relationships']:
            lines.append('<ul>')
            for rel in model['relationships']:
                lines.append(f'<li>{_escape_html(rel[0])} -> {_escape_html(rel[1])}</li>')
            lines.append('</ul>')
        else:
            lines.append('<p>none</p>')
    
    lines.append('</header>')
    
    # Objects/Tables
    objects = model.get('tables', [model])
    if kind == "csv":
        objects = [model]
    
    obj_id = 1
    for obj in objects:
        if kind == "sqlite":
            lines.append(f'<section id="o{obj_id}">')
            obj_id += 1
            lines.append(f'<h2>{_escape_html(obj["name"])} ({obj["type"]})</h2>')
            lines.append(f'<p><strong>Row count:</strong> {obj["row_count"]:,}</p>')
            lines.append(f'<p><strong>Sampled:</strong> {obj["sampled"]}</p>')
            
            if obj.get('foreign_keys'):
                lines.append('<p><strong>Foreign keys:</strong></p>')
                lines.append('<ul>')
                for fk in obj['foreign_keys']:
                    lines.append(f'<li>{_escape_html(fk["from"])} -> {_escape_html(fk["table"])}.{_escape_html(fk["to"])}</li>')
                lines.append('</ul>')
            
            if obj.get('indexes'):
                idx_list = ', '.join(_escape_html(idx) for idx in obj['indexes'])
                lines.append(f'<p><strong>Indexes:</strong> {idx_list}</p>')
        else:
            lines.append('<section>')
        
        # Column table
        columns = obj.get('columns', [])
        if columns:
            if kind == "sqlite":
                lines.append('<h3>Columns</h3>')
            else:
                lines.append('<h2>Columns</h2>')
            
            lines.append('<table>')
            lines.append('<thead><tr>')
            lines.append('<th>Name</th>')
            lines.append('<th>Type</th>')
            if kind == "sqlite":
                lines.append('<th>Declared</th>')
            lines.append('<th>Missing</th>')
            lines.append('<th>Distinct</th>')
            lines.append('<th>Samples</th>')
            lines.append('<th>Stats</th>')
            lines.append('</tr></thead>')
            lines.append('<tbody>')
            
            # Assign tok classes to columns (cycle through 1-5)
            for idx, col in enumerate(columns):
                tok_class = f'tok-{(idx % 5) + 1}'
                
                lines.append('<tr>')
                
                # Name
                lines.append(f'<td><code class="{tok_class}">{_escape_html(col["name"])}</code></td>')
                
                # Type
                if col['type'] == 'mixed':
                    share_pct = int(col['share'] * 100)
                    lines.append(f'<td>mixed ({col["candidate"]} {share_pct}%)</td>')
                else:
                    lines.append(f'<td>{col["type"]}</td>')
                
                # SQLite declared type + flags
                if kind == "sqlite":
                    decl_parts = []
                    if col.get('declared_type'):
                        decl_parts.append(_escape_html(col['declared_type']))
                    if col.get('notnull'):
                        decl_parts.append('NOT NULL')
                    if col.get('pk'):
                        decl_parts.append('PK')
                    lines.append(f'<td>{" ".join(decl_parts)}</td>')
                
                # Missing
                lines.append(f'<td>{col["missing"]:,}</td>')
                
                # Distinct
                distinct_str = f"{col['distinct']:,}"
                if not col['distinct_exact']:
                    distinct_str = f"≥ {distinct_str} (sampled)"
                lines.append(f'<td>{distinct_str}</td>')
                
                # Samples
                if col['samples']:
                    samples_html = ', '.join(_escape_html(s) for s in col['samples'][:5])
                    lines.append(f'<td>{samples_html}</td>')
                else:
                    lines.append('<td></td>')
                
                # Stats
                stats_parts = []
                if col.get('numeric'):
                    num = col['numeric']
                    mean_r = _round_sig(num['mean'])
                    median_r = _round_sig(num['median'])
                    stats_parts.append(f"{num['min']} / {num['max']} / {mean_r} / {median_r}")
                
                if col.get('dates'):
                    dates = col['dates']
                    stats_parts.append(f"{_escape_html(dates['min'])} – {_escape_html(dates['max'])}")
                
                if col.get('top'):
                    top_strs = [f"{_escape_html(val)} ({cnt})" for val, cnt in col['top']]
                    stats_parts.append('<br>'.join(top_strs))
                
                lines.append(f'<td>{" ".join(stats_parts)}</td>')
                
                lines.append('</tr>')
            
            lines.append('</tbody>')
            lines.append('</table>')
        
        lines.append('</section>')
    
    # Warnings
    lines.append('<section>')
    lines.append('<h2>Warnings</h2>')
    warnings = model.get('warnings', [])
    if warnings:
        lines.append('<ul>')
        for warning in warnings:
            lines.append(f'<li>{_escape_html(warning)}</li>')
        lines.append('</ul>')
    else:
        lines.append('<p>none</p>')
    lines.append('</section>')
    
    lines.append('</body>')
    lines.append('</html>')
    
    return '\n'.join(lines)
