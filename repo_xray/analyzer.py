"""Analyzer for Python repositories."""

import ast
import os
import tomllib
from pathlib import Path


class TargetError(Exception):
    """Error with the target repository."""
    pass


class TooManyFiles(TargetError):
    """Too many files in the target repository."""
    pass


def analyze(root, max_files=2000):
    """Analyze a Python repository.
    
    Args:
        root: Path to the repository root
        max_files: Maximum number of .py files to scan
        
    Returns:
        dict: Analysis model
        
    Raises:
        TargetError: If root is not a readable directory
        TooManyFiles: If more than max_files .py files found
    """
    root_path = Path(root).resolve()
    
    if not root_path.is_dir():
        raise TargetError(f"Target is not a readable directory: {root}")
    
    # Initialize model
    model = {
        "files_scanned": 0,
        "packages": [],
        "modules": {},
        "entry_points": [],
        "tests": [],
        "unresolved": [],
        "parse_errors": []
    }
    
    # Find all Python files and packages
    py_files = []
    packages = set()
    
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        # Skip symlinked directories
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        
        # Skip dotfiles, __pycache__, and virtualenvs
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d != '__pycache__']
        dirnames[:] = [d for d in dirnames if d not in ('venv', 'env') and 
                      not os.path.exists(os.path.join(dirpath, d, 'pyvenv.cfg'))]
        
        # Check if this is a package
        if '__init__.py' in filenames:
            rel_path = Path(dirpath).relative_to(root_path)
            if rel_path != Path('.'):
                packages.add(rel_path)
        
        # Collect .py files (skip symlinks)
        for filename in filenames:
            if filename.endswith('.py'):
                filepath = os.path.join(dirpath, filename)
                if not os.path.islink(filepath):
                    py_files.append(filepath)
    
    # Check max_files limit
    if len(py_files) > max_files:
        raise TooManyFiles(f"Found {len(py_files)} .py files, exceeds limit of {max_files}")
    
    model["files_scanned"] = len(py_files)
    
    # Convert package paths to dotted names
    for pkg_path in sorted(packages):
        parts = pkg_path.parts
        # Strip leading "src." for src layouts
        if parts and parts[0] == 'src':
            parts = parts[1:]
        if parts:
            model["packages"].append('.'.join(parts))
    
    # Parse console scripts from pyproject.toml
    pyproject_path = root_path / 'pyproject.toml'
    if pyproject_path.is_file():
        try:
            with open(pyproject_path, 'rb') as f:
                pyproject = tomllib.load(f)
            scripts = pyproject.get('project', {}).get('scripts', {})
            for name, entry in scripts.items():
                if ':' in entry:
                    module, func = entry.split(':', 1)
                    model["entry_points"].append({
                        "kind": "console_script",
                        "module": module,
                        "name": name
                    })
        except Exception:
            pass
    
    # Parse each Python file
    for filepath in py_files:
        rel_path = Path(filepath).relative_to(root_path)
        _analyze_file(root_path, rel_path, model)
    
    return model


def _path_to_module(rel_path):
    """Convert a relative path to a dotted module name."""
    parts = list(rel_path.parts)
    
    # Strip leading "src." for src layouts
    if parts and parts[0] == 'src':
        parts = parts[1:]
    
    if not parts:
        return None
    
    # Remove .py extension
    if parts[-1].endswith('.py'):
        parts[-1] = parts[-1][:-3]
    
    # If it's __init__.py, use the package name
    if parts[-1] == '__init__':
        parts = parts[:-1]
    
    if not parts:
        return None
    
    return '.'.join(parts)


def _analyze_file(root_path, rel_path, model):
    """Analyze a single Python file."""
    filepath = root_path / rel_path
    module_name = _path_to_module(rel_path)
    
    if not module_name:
        return
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source, filename=str(rel_path))
    except SyntaxError as e:
        model["parse_errors"].append({
            "path": str(rel_path.as_posix()),
            "lineno": e.lineno or 0
        })
        return
    except Exception:
        model["parse_errors"].append({
            "path": str(rel_path.as_posix()),
            "lineno": 0
        })
        return
    
    # Determine if this is a package
    is_package = rel_path.name == '__init__.py'
    
    # Extract docstring
    docstring = ast.get_docstring(tree) or ""
    if docstring:
        docstring = docstring.split('\n')[0]
    
    # Initialize module info
    module_info = {
        "path": str(rel_path.as_posix()),
        "is_package": is_package,
        "docstring": docstring,
        "classes": [],
        "functions": [],
        "imports": [],
        "has_main_guard": False
    }
    
    # Determine package for relative import resolution
    if is_package:
        package = module_name
    else:
        parts = module_name.split('.')
        package = '.'.join(parts[:-1]) if len(parts) > 1 else ''
    
    # Walk the AST
    has_main_guard = False
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            # Check for if __name__ == "__main__":
            if (isinstance(node.test, ast.Compare) and
                isinstance(node.test.left, ast.Name) and
                node.test.left.id == '__name__' and
                len(node.test.ops) == 1 and
                isinstance(node.test.ops[0], ast.Eq) and
                len(node.test.comparators) == 1 and
                isinstance(node.test.comparators[0], ast.Constant) and
                node.test.comparators[0].value == '__main__'):
                has_main_guard = True
    
    module_info["has_main_guard"] = has_main_guard
    
    if has_main_guard:
        model["entry_points"].append({
            "kind": "main_guard",
            "module": module_name,
            "name": "__main__"
        })
    
    # Extract top-level classes and functions
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            class_info = _extract_class(node)
            module_info["classes"].append(class_info)
        elif isinstance(node, ast.FunctionDef) or isinstance(node, ast.AsyncFunctionDef):
            func_info = _extract_function(node)
            module_info["functions"].append(func_info)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            _extract_imports(node, module_name, package, root_path, model, module_info)
    
    model["modules"][module_name] = module_info
    
    # Check if this is a test file
    filename = rel_path.name
    if filename.startswith('test_') or filename.endswith('_test.py'):
        test_info = _extract_test_info(module_name, module_info, model)
        model["tests"].append(test_info)


def _extract_class(node):
    """Extract class information from an AST node."""
    docstring = ast.get_docstring(node) or ""
    if docstring:
        docstring = docstring.split('\n')[0]
    
    bases = []
    for base in node.bases:
        if isinstance(base, ast.Name):
            bases.append(base.id)
        elif isinstance(base, ast.Attribute):
            bases.append(_get_attribute_name(base))
    
    methods = []
    for item in node.body:
        if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            methods.append(_extract_function(item))
    
    return {
        "name": node.name,
        "bases": bases,
        "docstring": docstring,
        "methods": methods
    }


def _extract_function(node):
    """Extract function information from an AST node."""
    docstring = ast.get_docstring(node) or ""
    if docstring:
        docstring = docstring.split('\n')[0]
    
    signature = _build_signature(node.args)
    
    return {
        "name": node.name,
        "signature": signature,
        "docstring": docstring
    }


def _build_signature(args):
    """Build a function signature string from ast.arguments."""
    parts = []
    
    # Regular positional arguments
    defaults_offset = len(args.args) - len(args.defaults)
    for i, arg in enumerate(args.args):
        if i >= defaults_offset:
            parts.append(f"{arg.arg}=…")
        else:
            parts.append(arg.arg)
    
    # *args
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    elif args.kwonlyargs:
        # Bare * for keyword-only args
        parts.append("*")
    
    # Keyword-only arguments
    kw_defaults_map = {kw.arg: i for i, kw in enumerate(args.kwonlyargs)}
    for i, arg in enumerate(args.kwonlyargs):
        if i < len(args.kw_defaults) and args.kw_defaults[i] is not None:
            parts.append(f"{arg.arg}=…")
        else:
            parts.append(arg.arg)
    
    # **kwargs
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    
    return f"({', '.join(parts)})"


def _get_attribute_name(node):
    """Get the full name of an attribute node."""
    if isinstance(node, ast.Name):
        return node.id
    elif isinstance(node, ast.Attribute):
        base = _get_attribute_name(node.value)
        return f"{base}.{node.attr}"
    return ""


def _extract_imports(node, module_name, package, root_path, model, module_info):
    """Extract import information from an import node."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            target = alias.name
            internal = _is_internal_module(target, root_path)
            module_info["imports"].append({
                "target": target,
                "internal": internal,
                "lineno": node.lineno
            })
    elif isinstance(node, ast.ImportFrom):
        # Check if inside try block
        in_try = _is_in_try(node)
        
        if node.module is None and node.level > 0:
            # from . import something or from .. import something
            resolved = _resolve_relative_import(package, node.level, None)
            if resolved is None:
                # Goes above top package
                detail = '.' * node.level
                model["unresolved"].append({
                    "module": module_name,
                    "kind": "relative",
                    "detail": detail,
                    "lineno": node.lineno
                })
                return
            
            for alias in node.names:
                if alias.name == '*':
                    model["unresolved"].append({
                        "module": module_name,
                        "kind": "star",
                        "detail": resolved,
                        "lineno": node.lineno
                    })
                else:
                    target = f"{resolved}.{alias.name}"
                    if in_try:
                        model["unresolved"].append({
                            "module": module_name,
                            "kind": "try",
                            "detail": target,
                            "lineno": node.lineno
                        })
                    else:
                        internal = _is_internal_module(target, root_path)
                        module_info["imports"].append({
                            "target": target,
                            "internal": internal,
                            "lineno": node.lineno
                        })
        else:
            # from module import something
            if node.level > 0:
                # Relative import
                resolved = _resolve_relative_import(package, node.level, node.module)
                if resolved is None:
                    detail = '.' * node.level + (node.module or '')
                    model["unresolved"].append({
                        "module": module_name,
                        "kind": "relative",
                        "detail": detail,
                        "lineno": node.lineno
                    })
                    return
                base_module = resolved
            else:
                base_module = node.module
            
            for alias in node.names:
                if alias.name == '*':
                    model["unresolved"].append({
                        "module": module_name,
                        "kind": "star",
                        "detail": base_module,
                        "lineno": node.lineno
                    })
                else:
                    target = f"{base_module}.{alias.name}" if base_module else alias.name
                    if in_try:
                        model["unresolved"].append({
                            "module": module_name,
                            "kind": "try",
                            "detail": target,
                            "lineno": node.lineno
                        })
                    else:
                        internal = _is_internal_module(target, root_path)
                        module_info["imports"].append({
                            "target": target,
                            "internal": internal,
                            "lineno": node.lineno
                        })


def _is_in_try(node):
    """Check if a node is inside a try block."""
    # This is a simplified check - we'd need parent tracking for full accuracy
    # For now, we'll detect this during a separate pass
    return False


def _resolve_relative_import(package, level, module):
    """Resolve a relative import to an absolute module name."""
    if not package:
        if level > 0:
            return None
        return module
    
    parts = package.split('.')
    
    # Go up 'level' packages
    if level > len(parts):
        return None
    
    if level > 0:
        parts = parts[:-(level - 1)] if level > 1 else parts
    
    if module:
        parts.append(module)
    
    return '.'.join(parts) if parts else None


def _is_internal_module(module_name, root_path):
    """Check if a module is internal to the repository."""
    # A module is internal if it corresponds to a package in the repo
    parts = module_name.split('.')
    
    # Check both with and without src/ prefix
    for prefix in [[], ['src']]:
        check_parts = prefix + parts
        for i in range(len(check_parts)):
            check_path = root_path / Path(*check_parts[:i+1])
            if check_path.is_dir() and (check_path / '__init__.py').exists():
                return True
            if i == len(check_parts) - 1:
                py_file = check_path.with_suffix('.py')
                if py_file.exists():
                    return True
    
    return False


def _extract_test_info(module_name, module_info, model):
    """Extract test information."""
    # Get imported internal modules
    inferred_targets = []
    for imp in module_info["imports"]:
        if imp["internal"]:
            inferred_targets.append(imp["target"])
    
    # Try to infer target from test name
    # test_core.py -> core, core_test.py -> core
    parts = module_name.split('.')
    test_file = parts[-1]
    
    if test_file.startswith('test_'):
        target_name = test_file[5:]  # Remove 'test_'
    elif test_file.endswith('_test'):
        target_name = test_file[:-5]  # Remove '_test'
    else:
        target_name = None
    
    if target_name:
        # Try to find a matching module
        test_package = '.'.join(parts[:-1])
        if test_package:
            candidate = f"{test_package}.{target_name}"
        else:
            candidate = target_name
        
        # Check if this module exists in our model
        if candidate in model["modules"] and candidate not in inferred_targets:
            inferred_targets.append(candidate)
        
        # Also check parent package
        if test_package:
            parent_parts = test_package.split('.')
            if len(parent_parts) > 1:
                parent_package = '.'.join(parent_parts[:-1])
                candidate = f"{parent_package}.{target_name}"
                if candidate in model["modules"] and candidate not in inferred_targets:
                    inferred_targets.append(candidate)
    
    return {
        "module": module_name,
        "inferred_targets": inferred_targets,
        "inferred": True
    }


# Second pass to detect imports in try blocks and importlib/__import__ calls
def _second_pass_unresolved(root_path, model):
    """Second pass to detect dynamic imports and try blocks."""
    for module_name, module_info in model["modules"].items():
        filepath = root_path / module_info["path"]
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()
            tree = ast.parse(source, filename=module_info["path"])
        except Exception:
            continue
        
        # Track try blocks
        try_blocks = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                try_blocks.append(node)
        
        # Find imports in try blocks
        for try_node in try_blocks:
            for node in ast.walk(try_node):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    # Mark this import as being in a try block
                    if isinstance(node, ast.Import):
                        for alias in node.names:
                            model["unresolved"].append({
                                "module": module_name,
                                "kind": "try",
                                "detail": alias.name,
                                "lineno": node.lineno
                            })
                            # Remove from regular imports
                            module_info["imports"] = [
                                imp for imp in module_info["imports"]
                                if not (imp["target"] == alias.name and imp["lineno"] == node.lineno)
                            ]
                    elif isinstance(node, ast.ImportFrom):
                        base = node.module or ''
                        for alias in node.names:
                            if alias.name != '*':
                                target = f"{base}.{alias.name}" if base else alias.name
                                model["unresolved"].append({
                                    "module": module_name,
                                    "kind": "try",
                                    "detail": target,
                                    "lineno": node.lineno
                                })
                                # Remove from regular imports
                                module_info["imports"] = [
                                    imp for imp in module_info["imports"]
                                    if not (imp["target"] == target and imp["lineno"] == node.lineno)
                                ]
        
        # Find importlib.import_module and __import__ calls
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                # importlib.import_module(...)
                if (isinstance(node.func, ast.Attribute) and
                    node.func.attr == 'import_module' and
                    isinstance(node.func.value, ast.Name) and
                    node.func.value.id == 'importlib'):
                    model["unresolved"].append({
                        "module": module_name,
                        "kind": "importlib",
                        "detail": "",
                        "lineno": node.lineno
                    })
                # __import__(...)
                elif isinstance(node.func, ast.Name) and node.func.id == '__import__':
                    model["unresolved"].append({
                        "module": module_name,
                        "kind": "__import__",
                        "detail": "",
                        "lineno": node.lineno
                    })


# Update analyze to include second pass
def analyze(root, max_files=2000):
    """Analyze a Python repository.
    
    Args:
        root: Path to the repository root
        max_files: Maximum number of .py files to scan
        
    Returns:
        dict: Analysis model
        
    Raises:
        TargetError: If root is not a readable directory
        TooManyFiles: If more than max_files .py files found
    """
    root_path = Path(root).resolve()
    
    if not root_path.is_dir():
        raise TargetError(f"Target is not a readable directory: {root}")
    
    # Initialize model
    model = {
        "files_scanned": 0,
        "packages": [],
        "modules": {},
        "entry_points": [],
        "tests": [],
        "unresolved": [],
        "parse_errors": []
    }
    
    # Find all Python files and packages
    py_files = []
    packages = set()
    
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        # Skip symlinked directories
        dirnames[:] = [d for d in dirnames if not os.path.islink(os.path.join(dirpath, d))]
        
        # Skip dotfiles, __pycache__, and virtualenvs
        dirnames[:] = [d for d in dirnames if not d.startswith('.') and d != '__pycache__']
        dirnames[:] = [d for d in dirnames if d not in ('venv', 'env') and 
                      not os.path.exists(os.path.join(dirpath, d, 'pyvenv.cfg'))]
        
        # Check if this is a package
        if '__init__.py' in filenames:
            rel_path = Path(dirpath).relative_to(root_path)
            if rel_path != Path('.'):
                packages.add(rel_path)
        
        # Collect .py files (skip symlinks)
        for filename in filenames:
            if filename.endswith('.py'):
                filepath = os.path.join(dirpath, filename)
                if not os.path.islink(filepath):
                    py_files.append(filepath)
    
    # Check max_files limit
    if len(py_files) > max_files:
        raise TooManyFiles(f"Found {len(py_files)} .py files, exceeds limit of {max_files}")
    
    model["files_scanned"] = len(py_files)
    
    # Convert package paths to dotted names
    for pkg_path in sorted(packages):
        parts = pkg_path.parts
        # Strip leading "src." for src layouts
        if parts and parts[0] == 'src':
            parts = parts[1:]
        if parts:
            model["packages"].append('.'.join(parts))
    
    # Parse console scripts from pyproject.toml
    pyproject_path = root_path / 'pyproject.toml'
    if pyproject_path.is_file():
        try:
            with open(pyproject_path, 'rb') as f:
                pyproject = tomllib.load(f)
            scripts = pyproject.get('project', {}).get('scripts', {})
            for name, entry in scripts.items():
                if ':' in entry:
                    module, func = entry.split(':', 1)
                    model["entry_points"].append({
                        "kind": "console_script",
                        "module": module,
                        "name": name
                    })
        except Exception:
            pass
    
    # Parse each Python file
    for filepath in py_files:
        rel_path = Path(filepath).relative_to(root_path)
        _analyze_file(root_path, rel_path, model)
    
    # Second pass for dynamic imports
    _second_pass_unresolved(root_path, model)
    
    return model
