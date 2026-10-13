"""Conservative AST checks for documented ownership errors before publication.

This is neither a complete API validator nor a security sandbox. Unknown APIs
are allowed. Only known invalid calls, GUI imports, and proven blocking job
calls are rejected; comments and strings do not count as calls.
"""
import ast
from typing import Dict, List, Optional
from abaqus_agent.tools.doc_search import doc_searcher


class ApiInspector(ast.NodeVisitor):
    def __init__(self):
        doc_searcher.load()
        self.rules = {rule["call"]: rule for entry in doc_searcher.entries
                      for rule in entry.get("blocked_calls", [])}
        self.aliases: Dict[str, str] = {"session": "session", "mdb": "mdb"}
        self.issues: List[str] = []

    def path(self, node) -> Optional[str]:
        if isinstance(node, ast.Name):
            return self.aliases.get(node.id)
        if isinstance(node, ast.Attribute):
            root = self.path(node.value)
            if root:
                path = root + "." + node.attr
                return path.removeprefix("abaqus.")
        if isinstance(node, ast.Subscript):
            root = self.path(node.value)
            return "job" if root == "mdb.jobs" else (root + "[]" if root else None)
        if isinstance(node, ast.Call) and self.path(node.func) in ("mdb.Job", "mdb.JobFromInputFile"):
            return "job"
        return None

    def visit_Import(self, node):
        for alias in node.names:
            root = alias.name.split(".")[0]
            if root == "abaqusGui":
                self.issues.append("Line %d: abaqusGui is a GUI-process module; kernel scripts must not import it." % node.lineno)
            self.aliases.pop(alias.asname or root, None)
            if root == "abaqus":
                self.aliases[alias.asname or root] = "abaqus"

    def visit_ImportFrom(self, node):
        module = node.module or ""
        if module.split(".")[0] == "abaqusGui":
            self.issues.append("Line %d: abaqusGui is a GUI-process module; kernel scripts must not import it." % node.lineno)
        for alias in node.names:
            if alias.name == "*":
                if module == "abaqus":
                    self.aliases.update(session="session", mdb="mdb")
                continue
            target = alias.asname or alias.name
            self.aliases.pop(target, None)
            if module == "abaqus" and alias.name in ("session", "mdb"):
                self.aliases[target] = alias.name

    def bind(self, target, value):
        if isinstance(target, ast.Name):
            if value:
                self.aliases[target.id] = value
            else:
                self.aliases.pop(target.id, None)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for child in target.elts:
                self.bind(child, None)

    def visit_Assign(self, node):
        self.visit(node.value)
        value = self.path(node.value)
        for target in node.targets:
            self.bind(target, value)

    def visit_AnnAssign(self, node):
        if node.value is not None:
            self.visit(node.value)
        self.bind(node.target, self.path(node.value))

    def visit_Call(self, node):
        name = self.path(node.func)
        if name in self.rules:
            self.issues.append("Line %d: %s(): %s" % (node.lineno, name, self.rules[name]["reason"]))
        self.generic_visit(node)

    def visit_FunctionDef(self, node):
        # Default values execute in the enclosing scope; parameters can shadow
        # the standard CAE namespace and must not produce false ownership errors.
        for value in list(node.args.defaults) + [v for v in node.args.kw_defaults if v is not None]:
            self.visit(value)
        outer = self.aliases.copy()
        self.aliases.pop(node.name, None)
        for arg in list(node.args.posonlyargs) + list(node.args.args) + list(node.args.kwonlyargs):
            self.aliases.pop(arg.arg, None)
        for arg in (node.args.vararg, node.args.kwarg):
            if arg:
                self.aliases.pop(arg.arg, None)
        for statement in node.body:
            self.visit(statement)
        self.aliases = outer
        self.aliases.pop(node.name, None)

    visit_AsyncFunctionDef = visit_FunctionDef


def precheck_script(script: str) -> Optional[str]:
    try:
        tree = ast.parse(script)
    except SyntaxError as exc:
        return "API_PRECHECK: Script was not submitted. Python syntax error at line %s: %s" % (exc.lineno, exc.msg)
    inspector = ApiInspector()
    inspector.visit(tree)
    if not inspector.issues:
        return None
    return ("API_PRECHECK: Script was not submitted; no model changes were made by this task.\n"
            + "\n".join(inspector.issues)
            + "\nCorrect the call ownership or execution context before submitting again.")
