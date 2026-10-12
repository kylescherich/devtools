"""Analyzer for Python repositories."""

import ast
import os
import stat
import tomllib
from pathlib import Path


class TargetError(Exception):
    """Error with the target repository."""
    pass


class TooManyFiles(TargetError):
    """Too many files in the target repository."""
    pass


def _link_reason(path):
    """Determine if path is a symlink or reparse point.
    
    Args:
        path: Path to check
    
    Returns:
        None if regular file/dir, "symlink" if symlink, "reparse_point" if reparse point
    """
    try:
        st = os.lstat(path)
    except (OSError, IOError):
        return None
    
    # Check for symlink
    if stat.S_ISLNK(st.st_mode):
        return "symlink"
    
    # Check for reparse point (Windows)
    if getattr(st, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
        return "reparse_point"
    
    return None


def _compute_module_name(rel_path, root_path):
    """Compute dotted module name by walking UP from file's directory.
    
    Start with file stem (nothing for __init__.py), then while current directory
    is inside root (never root itself or above) and contains __init__.py,
    prepend that directory's name and move to parent.
    """
    parts = list(rel_path.parts)
    
    if not parts or not parts[-1].endswith('.py'):
        return None
    
    # Start with file stem
    filename = parts[-1][:-3]  # Remove .py
    is_init = filename == '__init__'
    
    if is_init:
        module_parts = []
    else:
        module_parts = [filename]
    
    # Walk UP from file's directory
    current_dir = rel_path.parent
    
    while current_dir != Path('.'):
        # Check if current directory contains __init__.py
        init_file = root_path / current_dir / '__init__.py'
        if init_file.exists():
            module_parts.insert(0, current_dir.name)
            current_dir = current_dir.parent
        else:
            break
    
    if not module_parts:
        return None
    
    return '.'.join(module_parts)


def _compute_path_based_name(rel_path):
    """Compute full path-based dotted name for collision resolution.
    
    Converts relative path to dotted name: "/" -> ".", no ".py", no trailing "__init__".
    """
    parts = list(rel_path.parts)
    
    if not parts or not parts[-1].endswith('.py'):
        return None
    
    # Remove .py extension
    parts[-1] = parts[-1][:-3]
    
    # Remove trailing __init__
    if parts[-1] == '__init__':
        parts = parts[:-1]
    
    if not parts:
        return None
    
    return '.'.join(parts)


def _parse_file(root_path, rel_path, module_name, model):
    """Parse a single Python file and extract raw data (pass 2)."""
    filepath = root_path / rel_path
    
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            source = f.read()
        tree = ast.parse(source, filename=str(rel_path))
    except UnicodeDecodeError:
        model["skipped"].append({
            "path": str(rel_path.as_posix()),
            "reason": "not_utf8"
        })
        return
    except SyntaxError as e:
        model["parse_errors"].append({
            "path": str(rel_path.as_posix()),
            "lineno": e.lineno or 0
        })
        model["skipped"].append({
            "path": str(rel_path.as_posix()),
            "reason": "syntax_error"
        })
        return
    except Exception:
        model["parse_errors"].append({
            "path": str(rel_path.as_posix()),
            "lineno": 0
        })
        model["skipped"].append({
            "path": str(rel_path.as_posix()),
            "reason": "syntax_error"
        })
        return
    
    # Determine if this is a package
    is_package = rel_path.name == '__init__.py'
    
    # Extract docstring
    docstring = ast.get_docstring(tree) or ""
    if docstring:
        docstring = docstring.split('\n')[0]
    
    # Initialize module info with raw_imports for pass 2
    module_info = {
        "path": str(rel_path.as_posix()),
        "is_package": is_package,
        "docstring": docstring,
        "classes": [],
        "functions": [],
        "raw_imports": [],  # Will be resolved in pass 3
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
    
    # Collect all imports from anywhere in the file (not just top-level)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            _extract_raw_imports(node, module_name, package, model, module_info)
    
    model["modules"][module_name] = module_info


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


def _extract_raw_imports(node, module_name, package, model, module_info):
    """Extract raw import information from an import node (pass 2)."""
    if isinstance(node, ast.Import):
        for alias in node.names:
            # Record the written name
            module_info["raw_imports"].append({
                "kind": "import",
                "name": alias.name,
                "lineno": node.lineno
            })
    elif isinstance(node, ast.ImportFrom):
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
            base_module = resolved
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
        
        # Record raw import data
        for alias in node.names:
            if alias.name == '*':
                model["unresolved"].append({
                    "module": module_name,
                    "kind": "star",
                    "detail": base_module,
                    "lineno": node.lineno
                })
            else:
                module_info["raw_imports"].append({
                    "kind": "from",
                    "base": base_module,
                    "name": alias.name,
                    "lineno": node.lineno
                })


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


def _resolve_imports_pass3(model):
    """Resolve raw imports to targets (pass 3, after all modules are known)."""
    for module_name, module_info in model["modules"].items():
        imports = []
        
        for raw_import in module_info["raw_imports"]:
            if raw_import["kind"] == "import":
                # import X.Y - find longest known prefix
                name = raw_import["name"]
                parts = name.split('.')
                
                target = None
                for i in range(len(parts), 0, -1):
                    candidate = '.'.join(parts[:i])
                    if candidate in model["modules"]:
                        target = candidate
                        break
                
                if target is None:
                    target = name
                
                internal = target in model["modules"]
                imports.append({
                    "target": target,
                    "internal": internal,
                    "lineno": raw_import["lineno"]
                })
            
            elif raw_import["kind"] == "from":
                # from X import a - check if X.a is a module, else use X
                base = raw_import["base"]
                name = raw_import["name"]
                
                candidate = f"{base}.{name}" if base else name
                if candidate in model["modules"]:
                    target = candidate
                else:
                    target = base
                
                internal = target in model["modules"]
                imports.append({
                    "target": target,
                    "internal": internal,
                    "lineno": raw_import["lineno"]
                })
        
        # Deduplicate by (target, lineno) to get one entry per distinct target per statement
        seen = set()
        unique_imports = []
        for imp in imports:
            key = (imp["target"], imp["lineno"])
            if key not in seen:
                seen.add(key)
                unique_imports.append(imp)
        
        module_info["imports"] = unique_imports
        del module_info["raw_imports"]


def _build_tests_pass3(model):
    """Build test information from resolved imports (pass 3)."""
    for module_name, module_info in model["modules"].items():
        # Check if this is a test file
        parts = module_name.split('.')
        test_file = parts[-1]
        
        if not (test_file.startswith('test_') or test_file.endswith('_test')):
            continue
        
        # Get imported internal modules
        inferred_targets = []
        for imp in module_info["imports"]:
            if imp["internal"] and imp["target"] not in inferred_targets:
                inferred_targets.append(imp["target"])
        
        # Try to infer target from test name
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
        
        model["tests"].append({
            "module": module_name,
            "inferred_targets": inferred_targets,
            "inferred": True
        })


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


def analyze(root, max_files=2000, max_file_bytes=1_000_000):
    """Analyze a Python repository.
    
    Args:
        root: Path to the repository root
        max_files: Maximum number of .py files to scan
        max_file_bytes: Maximum size in bytes for a single .py file
        
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
        "parse_errors": [],
        "skipped": []
    }
    
    # Collect all Python files
    py_files = []
    
    for dirpath, dirnames, filenames in os.walk(root_path, followlinks=False):
        # Filter directories: skip dotfiles, __pycache__, virtualenvs, and linked dirs
        filtered_dirs = []
        for d in dirnames:
            # Skip dotfiles and __pycache__
            if d.startswith('.') or d == '__pycache__':
                continue
            
            # Skip virtualenvs
            if d in ('venv', 'env') or os.path.exists(os.path.join(dirpath, d, 'pyvenv.cfg')):
                continue
            
            # Check for symlink/reparse point
            dir_path = os.path.join(dirpath, d)
            link_reason = _link_reason(dir_path)
            if link_reason:
                # Record skipped directory
                rel_dir = Path(dir_path).relative_to(root_path)
                model["skipped"].append({
                    "path": str(rel_dir.as_posix()),
                    "reason": link_reason
                })
                continue
            
            filtered_dirs.append(d)
        
        dirnames[:] = filtered_dirs
        
        # Collect .py files (skip symlinks and check size)
        for filename in filenames:
            if filename.endswith('.py'):
                filepath = os.path.join(dirpath, filename)
                rel_path = Path(filepath).relative_to(root_path)
                
                # Check for symlink/reparse point
                link_reason = _link_reason(filepath)
                if link_reason:
                    model["skipped"].append({
                        "path": str(rel_path.as_posix()),
                        "reason": link_reason
                    })
                    continue
                
                # Check file size
                try:
                    file_size = os.lstat(filepath).st_size
                    if file_size > max_file_bytes:
                        model["skipped"].append({
                            "path": str(rel_path.as_posix()),
                            "reason": "too_large"
                        })
                        continue
                except (OSError, IOError):
                    continue
                
                py_files.append(filepath)
    
    # Check max_files limit
    if len(py_files) > max_files:
        raise TooManyFiles(f"Found {len(py_files)} .py files, exceeds limit of {max_files}")
    
    model["files_scanned"] = len(py_files)
    
    # PASS 1: Compute module names for all files
    path_to_name = {}
    name_to_paths = {}
    
    for filepath in py_files:
        rel_path = Path(filepath).relative_to(root_path)
        module_name = _compute_module_name(rel_path, root_path)
        
        if module_name:
            path_to_name[str(rel_path.as_posix())] = module_name
            if module_name not in name_to_paths:
                name_to_paths[module_name] = []
            name_to_paths[module_name].append(str(rel_path.as_posix()))
    
    # Handle collisions: use path-based names for any name with multiple paths
    for module_name, paths in name_to_paths.items():
        if len(paths) > 1:
            for path in paths:
                rel_path = Path(path)
                path_based_name = _compute_path_based_name(rel_path)
                if path_based_name:
                    path_to_name[path] = path_based_name
    
    # Build packages list from __init__.py files
    for filepath in py_files:
        rel_path = Path(filepath).relative_to(root_path)
        if rel_path.name == '__init__.py':
            path_str = str(rel_path.as_posix())
            if path_str in path_to_name:
                model["packages"].append(path_to_name[path_str])
    
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
    
    # PASS 2: Parse each Python file
    for filepath in py_files:
        rel_path = Path(filepath).relative_to(root_path)
        path_str = str(rel_path.as_posix())
        
        if path_str in path_to_name:
            module_name = path_to_name[path_str]
            _parse_file(root_path, rel_path, module_name, model)
    
    # PASS 3: Resolve imports now that all module names are known
    _resolve_imports_pass3(model)
    
    # Second pass for dynamic imports (try blocks, importlib, __import__)
    _second_pass_unresolved(root_path, model)
    
    _build_tests_pass3(model)
    
    # Sort skipped list by (reason, path)
    model["skipped"].sort(key=lambda x: (x["reason"], x["path"]))
    
    return model
