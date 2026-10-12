"""Generate reports from analyzer model."""

import html
import re
from collections import defaultdict


def _escape_markdown(s):
    """Escape special characters for Markdown."""
    s = s.replace('&', '&amp;')
    s = s.replace('<', '&lt;')
    s = s.replace('>', '&gt;')
    # Backslash-escape Markdown special characters
    for char in '*_[]':
        s = s.replace(char, '\\' + char)
    return s


def _escape_html(s):
    """Escape for HTML output."""
    return html.escape(s, quote=True)


def _pluralize(count, singular, plural=None):
    """Return count with singular or plural form.
    
    Args:
        count: The count
        singular: Singular form (e.g., "package")
        plural: Plural form (defaults to singular + "s")
    
    Returns:
        str: e.g., "1 package" or "2 packages"
    """
    if plural is None:
        plural = singular + 's'
    return f"{count} {singular if count == 1 else plural}"


def _anchor_ids(names):
    """Generate unique, attribute-safe anchor IDs for module names.
    
    Args:
        names: Iterable of module names
    
    Returns:
        dict: {module_name: anchor_id}
    """
    ids = {}
    used = set()
    
    for name in sorted(names):
        # Start with "mod-" + name with "." -> "-"
        base_id = "mod-" + name.replace('.', '-')
        
        # Replace any character not in [A-Za-z0-9_-] with "_"
        safe_id = re.sub(r'[^A-Za-z0-9_-]', '_', base_id)
        
        # Handle collisions
        final_id = safe_id
        counter = 2
        while final_id in used:
            final_id = f"{safe_id}-{counter}"
            counter += 1
        
        ids[name] = final_id
        used.add(final_id)
    
    return ids


def _get_most_imported_internal(model):
    """Return list of (module_name, count) for top 5 most-imported internal modules."""
    importers = defaultdict(set)
    
    # Build set of test module names
    test_modules = {test['module'] for test in model.get('tests', [])}
    
    for mod_name, mod_info in model['modules'].items():
        if mod_name in test_modules:
            continue
        for imp in mod_info.get('imports', []):
            if imp.get('internal'):
                importers[imp['target']].add(mod_name)
    
    # Sort by count descending, then by name for determinism
    sorted_mods = sorted(importers.items(), key=lambda x: (-len(x[1]), x[0]))
    return [(name, len(importers_set)) for name, importers_set in sorted_mods[:5]]


def _get_untested_modules(model):
    """Return sorted list of internal non-test modules with no inferred test targets."""
    untested = []
    
    # Build set of test module names and their targets
    test_modules = {test['module'] for test in model.get('tests', [])}
    tested_modules = set()
    for test in model.get('tests', []):
        tested_modules.update(test.get('inferred_targets', []))
    
    for mod_name in model['modules'].keys():
        if mod_name in test_modules:
            continue
        if mod_name not in tested_modules:
            untested.append(mod_name)
    
    return sorted(untested)


def _count_unresolved_by_kind(model):
    """Return dict of {kind: count} for unresolved imports."""
    counts = defaultdict(int)
    
    for unres in model.get('unresolved', []):
        counts[unres['kind']] += 1
    
    return dict(sorted(counts.items()))


def summary(model):
    """Generate plain-English summary sentences from model.
    
    Returns:
        list[str]: Deterministic summary sentences.
    """
    lines = []
    
    # Counts
    num_modules = len(model['modules'])
    num_packages = len(model.get('packages', []))
    
    total_classes = sum(len(m.get('classes', [])) for m in model['modules'].values())
    total_functions = sum(len(m.get('functions', [])) for m in model['modules'].values())
    total_tests = len(model.get('tests', []))
    
    lines.append(f"Found {_pluralize(num_modules, 'module')} in {_pluralize(num_packages, 'package')}.")
    lines.append(f"Total: {_pluralize(total_classes, 'class', 'classes')}, {_pluralize(total_functions, 'function')}, {_pluralize(total_tests, 'test module')}.")
    
    # Entry points
    entry_points = model.get('entry_points', [])
    if entry_points:
        sorted_eps = sorted(entry_points, key=lambda ep: (ep['kind'], ep['module'], ep['name']))
        ep_strs = [f"{ep['name']} ({ep['kind']}) in {ep['module']}" for ep in sorted_eps]
        lines.append(f"Entry points: {', '.join(ep_strs)}.")
    else:
        lines.append("No entry points found.")
    
    # Most imported
    most_imported = _get_most_imported_internal(model)
    if most_imported:
        parts = [f"{name} ({count})" for name, count in most_imported]
        lines.append(f"Most-imported internal modules: {', '.join(parts)}.")
    
    # Untested modules
    untested = _get_untested_modules(model)
    if untested:
        lines.append(f"{_pluralize(len(untested), 'internal non-test module')} have no statically inferred test target.")
    
    # Unresolved imports
    unresolved_counts = _count_unresolved_by_kind(model)
    if unresolved_counts:
        parts = [f"{count} {kind}" for kind, count in unresolved_counts.items()]
        lines.append(f"Unresolved/dynamic imports: {', '.join(parts)}.")
    
    return lines


def _compute_graph_layers(model):
    """Compute layered layout for dependency graph.
    
    Returns dict {module_name: layer_number} where layer is longest path
    from modules with no internal imports. Uses DFS coloring to detect
    and ignore back edges that would create cycles.
    """
    # Build adjacency list (module -> modules it imports)
    graph = defaultdict(list)
    all_modules = set()
    
    for mod_name, mod_info in model['modules'].items():
        all_modules.add(mod_name)
        for imp in mod_info.get('imports', []):
            if imp.get('internal'):
                target = imp['target']
                if target in model['modules']:
                    graph[mod_name].append(target)
                    all_modules.add(target)
    
    # DFS to compute longest path, ignoring back edges
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {mod: WHITE for mod in all_modules}
    layer = {}
    
    def dfs(node):
        if color[node] == BLACK:
            return layer[node]
        if color[node] == GRAY:
            # Back edge, ignore
            return -1
        
        color[node] = GRAY
        max_child_layer = -1
        
        for child in graph[node]:
            child_layer = dfs(child)
            max_child_layer = max(max_child_layer, child_layer)
        
        color[node] = BLACK
        layer[node] = max_child_layer + 1
        return layer[node]
    
    for mod in all_modules:
        if color[mod] == WHITE:
            dfs(mod)
    
    return layer


def _render_svg_graph(model):
    """Render dependency graph as inline SVG.
    
    Returns SVG string or message if too many modules.
    """
    # Build set of test module names
    test_modules = {test['module'] for test in model.get('tests', [])}
    internal_modules = [m for m in model['modules'].keys() if m not in test_modules]
    
    if len(internal_modules) > 150:
        return f'<p>Graph omitted: {len(internal_modules)} modules (limit 150)</p>'
    
    if not internal_modules:
        return '<p>No internal modules to graph.</p>'
    
    # Compute layers
    layers_map = _compute_graph_layers(model)
    
    # Group modules by layer
    layer_groups = defaultdict(list)
    for mod in sorted(internal_modules):
        layer_groups[layers_map[mod]].append(mod)
    
    # Layout parameters
    node_width = 120
    node_height = 30
    h_spacing = 20
    v_spacing = 50
    margin = 20
    
    # Compute positions
    positions = {}
    max_width = 0
    
    for layer_num in sorted(layer_groups.keys()):
        mods = sorted(layer_groups[layer_num])
        y = margin + layer_num * (node_height + v_spacing)
        
        layer_width = len(mods) * (node_width + h_spacing) - h_spacing
        max_width = max(max_width, layer_width)
        
        for i, mod in enumerate(mods):
            x = margin + i * (node_width + h_spacing)
            positions[mod] = (x, y)
    
    svg_width = max_width + 2 * margin
    svg_height = margin + (max(layer_groups.keys()) + 1) * (node_height + v_spacing)
    
    # Build SVG
    svg_parts = [
        f'<svg width="{svg_width}" height="{svg_height}">',
        '<defs>',
        '<marker id="arrowhead" markerWidth="10" markerHeight="10" refX="9" refY="3" orient="auto">',
        '<polygon points="0 0, 10 3, 0 6" fill="#666" />',
        '</marker>',
        '</defs>',
    ]
    
    # Collect unique edges (source, target) pairs
    edges = set()
    for mod_name, mod_info in model['modules'].items():
        if mod_name not in positions:
            continue
        for imp in mod_info.get('imports', []):
            if imp.get('internal'):
                target = imp['target']
                if target in positions:
                    edges.add((mod_name, target))
    
    # Draw edges first (so they appear behind nodes)
    for source, target in sorted(edges):
        x1, y1 = positions[source]
        cx1 = x1 + node_width / 2
        cy1 = y1 + node_height / 2
        
        x2, y2 = positions[target]
        cx2 = x2 + node_width / 2
        cy2 = y2 + node_height / 2
        
        svg_parts.append(
            f'<line x1="{cx1}" y1="{cy1}" x2="{cx2}" y2="{cy2}" '
            f'stroke="#666" stroke-width="1" marker-end="url(#arrowhead)" />'
        )
    
    # Draw nodes
    for mod in sorted(positions.keys()):
        x, y = positions[mod]
        escaped_name = _escape_html(mod)
        
        svg_parts.append(
            f'<rect x="{x}" y="{y}" width="{node_width}" height="{node_height}" '
            f'fill="#e0e0e0" stroke="#333" stroke-width="1" />'
        )
        svg_parts.append(
            f'<text x="{x + node_width/2}" y="{y + node_height/2}" '
            f'text-anchor="middle" dominant-baseline="middle" '
            f'font-family="sans-serif" font-size="10">{escaped_name}</text>'
        )
    
    svg_parts.append('</svg>')
    
    return '\n'.join(svg_parts)


def render_markdown(model, title):
    """Render model as Markdown report.
    
    Args:
        model: Analyzer model dict
        title: Report title
    
    Returns:
        str: Markdown document
    """
    lines = []
    
    # Title
    lines.append(f"# {_escape_markdown(title)}")
    lines.append("")
    
    # Summary
    lines.append("## Summary")
    lines.append("")
    for line in summary(model):
        lines.append(_escape_markdown(line))
    lines.append("")
    
    # Entry points
    lines.append("## Entry points")
    lines.append("")
    lines.append(_escape_markdown('Detected forms: if __name__ == "__main__" blocks, and [project.scripts] console scripts in the root pyproject.toml.'))
    lines.append("")
    entry_points = model.get('entry_points', [])
    if entry_points:
        sorted_eps = sorted(entry_points, key=lambda ep: (ep['kind'], ep['module'], ep['name']))
        for ep in sorted_eps:
            ep_str = f"{ep['name']} ({ep['kind']}) in {ep['module']}"
            lines.append(f"- {_escape_markdown(ep_str)}")
    else:
        lines.append("None")
    lines.append("")
    
    # Modules
    lines.append("## Modules")
    lines.append("")
    
    module_names = sorted(model['modules'].keys())
    for mod_name in module_names:
        lines.append(f"- {_escape_markdown(mod_name)}")
    lines.append("")
    
    # Module details
    for mod_name in module_names:
        mod_info = model['modules'][mod_name]
        lines.append(f"### {_escape_markdown(mod_name)}")
        lines.append("")
        lines.append(f"**Path:** {_escape_markdown(mod_info['path'])}")
        lines.append("")
        
        if mod_info.get('docstring'):
            lines.append(_escape_markdown(mod_info['docstring']))
            lines.append("")
        
        # Classes
        if mod_info.get('classes'):
            lines.append("**Classes:**")
            lines.append("")
            for cls in mod_info['classes']:
                bases_str = ""
                if cls.get('bases'):
                    bases_str = f"({_escape_markdown(', '.join(cls['bases']))})"
                lines.append(f"- `{_escape_markdown(cls['name'])}{bases_str}`")
                if cls.get('docstring'):
                    lines.append(f"  - {_escape_markdown(cls['docstring'])}")
                
                for method in cls.get('methods', []):
                    sig = _escape_markdown(f"{method['name']}{method['signature']}")
                    lines.append(f"  - `{sig}`")
                    if method.get('docstring'):
                        lines.append(f"    - {_escape_markdown(method['docstring'])}")
            lines.append("")
        
        # Functions
        if mod_info.get('functions'):
            lines.append("**Functions:**")
            lines.append("")
            for func in mod_info['functions']:
                sig = _escape_markdown(f"{func['name']}{func['signature']}")
                lines.append(f"- `{sig}`")
                if func.get('docstring'):
                    lines.append(f"  - {_escape_markdown(func['docstring'])}")
            lines.append("")
        
        # Internal imports
        internal_imports = sorted(set(imp['target'] for imp in mod_info.get('imports', []) if imp.get('internal')))
        if internal_imports:
            lines.append("**Internal imports:**")
            lines.append("")
            for imp in internal_imports:
                lines.append(f"- {_escape_markdown(imp)}")
            lines.append("")
        
        # External imports
        external_imports = sorted(set(imp['target'] for imp in mod_info.get('imports', []) if not imp.get('internal')))
        if external_imports:
            lines.append("**External imports:**")
            lines.append("")
            for imp in external_imports:
                lines.append(f"- {_escape_markdown(imp)}")
            lines.append("")
    
    # Test modules (inferred targets)
    lines.append("## Test modules \\(inferred targets\\)")
    lines.append("")
    
    tests = model.get('tests', [])
    if tests:
        for test in sorted(tests, key=lambda t: t['module']):
            for target in sorted(test.get('inferred_targets', [])):
                lines.append(f"- {_escape_markdown(test['module'])} → {_escape_markdown(target)} (inferred)")
    else:
        lines.append("None")
    lines.append("")
    
    # Unresolved / dynamic
    lines.append("## Unresolved / dynamic")
    lines.append("")
    
    unresolved = model.get('unresolved', [])
    if unresolved:
        sorted_unresolved = sorted(unresolved, key=lambda u: (u['kind'], u['module'], u.get('detail', ''), u['lineno']))
        for unres in sorted_unresolved:
            parts = [_escape_markdown(unres['kind']), _escape_markdown(unres['module'])]
            if unres.get('detail'):
                parts.append(_escape_markdown(unres['detail']))
            parts.append(f"line {unres['lineno']}")
            lines.append(f"- {', '.join(parts)}")
    else:
        lines.append("None")
    lines.append("")
    
    # Parse errors
    lines.append("## Parse errors")
    lines.append("")
    
    parse_errors = model.get('parse_errors', [])
    if parse_errors:
        sorted_errors = sorted(parse_errors, key=lambda e: (e['path'], e['lineno']))
        for err in sorted_errors:
            lines.append(f"- {_escape_markdown(err['path'])} line {err['lineno']}")
    else:
        lines.append("None")
    lines.append("")
    
    return '\n'.join(lines)


def render_html(model, title):
    """Render model as self-contained HTML report.
    
    Args:
        model: Analyzer model dict
        title: Report title
    
    Returns:
        str: HTML document
    """
    lines = []
    
    # HTML header
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="en">')
    lines.append('<head>')
    lines.append('<meta charset="UTF-8">')
    lines.append(f'<title>{_escape_html(title)}</title>')
    lines.append('<style>')
    lines.append('body { font-family: sans-serif; margin: 20px; max-width: 1200px; }')
    lines.append('h1 { color: #333; }')
    lines.append('h2 { color: #555; border-bottom: 2px solid #ddd; padding-bottom: 5px; margin-top: 30px; }')
    lines.append('h3 { color: #666; margin-top: 20px; }')
    lines.append('code { background: #f4f4f4; padding: 2px 5px; border-radius: 3px; }')
    lines.append('pre { background: #f4f4f4; padding: 10px; border-radius: 5px; overflow-x: auto; }')
    lines.append('ul { line-height: 1.6; }')
    lines.append('a { color: #0066cc; text-decoration: none; }')
    lines.append('a:hover { text-decoration: underline; }')
    lines.append('.module-list { columns: 2; }')
    lines.append('.summary-line { margin: 5px 0; }')
    lines.append('</style>')
    lines.append('</head>')
    lines.append('<body>')
    
    # Title
    lines.append(f'<h1>{_escape_html(title)}</h1>')
    
    # Summary
    lines.append('<h2>Summary</h2>')
    for line in summary(model):
        lines.append(f'<div class="summary-line">{_escape_html(line)}</div>')
    
    # Entry points
    lines.append('<h2>Entry points</h2>')
    lines.append(f'<p>{_escape_html("Detected forms: if __name__ == \"__main__\" blocks, and [project.scripts] console scripts in the root pyproject.toml.")}</p>')
    entry_points = model.get('entry_points', [])
    if entry_points:
        sorted_eps = sorted(entry_points, key=lambda ep: (ep['kind'], ep['module'], ep['name']))
        lines.append('<ul>')
        for ep in sorted_eps:
            ep_str = f"{ep['name']} ({ep['kind']}) in {ep['module']}"
            lines.append(f'<li>{_escape_html(ep_str)}</li>')
        lines.append('</ul>')
    else:
        lines.append('<p>None</p>')
    
    # Dependency graph
    lines.append('<h2>Dependency graph</h2>')
    lines.append(_render_svg_graph(model))
    
    # Modules list
    lines.append('<h2>Modules</h2>')
    lines.append('<div class="module-list">')
    lines.append('<ul>')
    
    module_names = sorted(model['modules'].keys())
    anchor_map = _anchor_ids(module_names)
    for mod_name in module_names:
        anchor = _escape_html(anchor_map[mod_name], quote=True)
        lines.append(f'<li><a href="#{anchor}">{_escape_html(mod_name)}</a></li>')
    
    lines.append('</ul>')
    lines.append('</div>')
    
    # Module details
    for mod_name in module_names:
        mod_info = model['modules'][mod_name]
        anchor = _escape_html(anchor_map[mod_name], quote=True)
        
        lines.append(f'<h3 id="{anchor}">{_escape_html(mod_name)}</h3>')
        lines.append(f'<p><strong>Path:</strong> {_escape_html(mod_info["path"])}</p>')
        
        if mod_info.get('docstring'):
            lines.append(f'<p>{_escape_html(mod_info["docstring"])}</p>')
        
        # Classes
        if mod_info.get('classes'):
            lines.append('<p><strong>Classes:</strong></p>')
            lines.append('<ul>')
            for cls in mod_info['classes']:
                bases_str = ""
                if cls.get('bases'):
                    bases_str = f"({_escape_html(', '.join(cls['bases']))})"
                lines.append(f'<li><code>{_escape_html(cls["name"])}{bases_str}</code>')
                if cls.get('docstring'):
                    lines.append(f'<ul><li>{_escape_html(cls["docstring"])}</li></ul>')
                
                if cls.get('methods'):
                    lines.append('<ul>')
                    for method in cls['methods']:
                        sig = _escape_html(f"{method['name']}{method['signature']}")
                        lines.append(f'<li><code>{sig}</code>')
                        if method.get('docstring'):
                            lines.append(f'<ul><li>{_escape_html(method["docstring"])}</li></ul>')
                        lines.append('</li>')
                    lines.append('</ul>')
                
                lines.append('</li>')
            lines.append('</ul>')
        
        # Functions
        if mod_info.get('functions'):
            lines.append('<p><strong>Functions:</strong></p>')
            lines.append('<ul>')
            for func in mod_info['functions']:
                sig = _escape_html(f"{func['name']}{func['signature']}")
                lines.append(f'<li><code>{sig}</code>')
                if func.get('docstring'):
                    lines.append(f'<ul><li>{_escape_html(func["docstring"])}</li></ul>')
                lines.append('</li>')
            lines.append('</ul>')
        
        # Internal imports
        internal_imports = sorted(set(imp['target'] for imp in mod_info.get('imports', []) if imp.get('internal')))
        if internal_imports:
            lines.append('<p><strong>Internal imports:</strong></p>')
            lines.append('<ul>')
            for imp in internal_imports:
                lines.append(f'<li>{_escape_html(imp)}</li>')
            lines.append('</ul>')
        
        # External imports
        external_imports = sorted(set(imp['target'] for imp in mod_info.get('imports', []) if not imp.get('internal')))
        if external_imports:
            lines.append('<p><strong>External imports:</strong></p>')
            lines.append('<ul>')
            for imp in external_imports:
                lines.append(f'<li>{_escape_html(imp)}</li>')
            lines.append('</ul>')
    
    # Test modules (inferred targets)
    lines.append('<h2>Test modules (inferred targets)</h2>')
    
    tests = model.get('tests', [])
    if tests:
        lines.append('<ul>')
        for test in sorted(tests, key=lambda t: t['module']):
            for target in sorted(test.get('inferred_targets', [])):
                lines.append(
                    f'<li>{_escape_html(test["module"])} → {_escape_html(target)} '
                    f'<em>(inferred)</em></li>'
                )
        lines.append('</ul>')
    else:
        lines.append('<p>None</p>')
    
    # Unresolved / dynamic
    lines.append('<h2>Unresolved / dynamic</h2>')
    
    unresolved = model.get('unresolved', [])
    if unresolved:
        sorted_unresolved = sorted(unresolved, key=lambda u: (u['kind'], u['module'], u.get('detail', ''), u['lineno']))
        lines.append('<ul>')
        for unres in sorted_unresolved:
            parts = [_escape_html(unres['kind']), _escape_html(unres['module'])]
            if unres.get('detail'):
                parts.append(_escape_html(unres['detail']))
            parts.append(f'line {unres["lineno"]}')
            lines.append(f'<li>{", ".join(parts)}</li>')
        lines.append('</ul>')
    else:
        lines.append('<p>None</p>')
    
    # Parse errors
    lines.append('<h2>Parse errors</h2>')
    
    parse_errors = model.get('parse_errors', [])
    if parse_errors:
        sorted_errors = sorted(parse_errors, key=lambda e: (e['path'], e['lineno']))
        lines.append('<ul>')
        for err in sorted_errors:
            lines.append(f'<li>{_escape_html(err["path"])} line {err["lineno"]}</li>')
        lines.append('</ul>')
    else:
        lines.append('<p>None</p>')
    
    # Close HTML
    lines.append('</body>')
    lines.append('</html>')
    
    return '\n'.join(lines)
