#!/usr/bin/env python3
"""Query Forge: an educational SQL analyzer and rule-based reviewer.

This intentionally supports a useful SELECT-focused SQL subset rather than all
of SQL. It uses only the Python standard library and never executes queries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable


KEYWORDS = {
    "SELECT", "FROM", "WHERE", "AND", "OR", "NOT", "IN", "IS", "NULL",
    "LIKE", "BETWEEN", "AS", "DISTINCT", "JOIN", "INNER", "LEFT", "RIGHT",
    "FULL", "OUTER", "CROSS", "ON", "GROUP", "BY", "HAVING", "ORDER",
    "ASC", "DESC", "LIMIT", "OFFSET", "TRUE", "FALSE", "UPDATE", "INSERT",
    "INTO", "VALUES", "SET", "DELETE", "DROP", "TABLE", "CREATE", "ALTER",
    "TRUNCATE", "DATABASE", "INDEX", "DEFAULT", "PRIMARY", "KEY", "REFERENCES",
    "UNIQUE", "CONSTRAINT", "ADD", "COLUMN", "IF", "EXISTS", "REPLACE"
}
TOKEN_RE = re.compile(
    r"(?P<SPACE>\s+)"
    r"|(?P<COMMENT>--[^\n]*|/\*[\s\S]*?\*/)"
    r"|(?P<STRING>'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\")"
    r"|(?P<NUMBER>\d+(?:\.\d+)?)"
    r"|(?P<OP><=|>=|<>|!=|=|<|>|\+|-|\*|/|%)"
    r"|(?P<PUNCT>[(),.;])"
    r"|(?P<IDENT>[A-Za-z_][A-Za-z0-9_$]*)"
    r"|(?P<INVALID>.)"
)


@dataclass
class Token:
    kind: str
    value: str
    line: int
    column: int


@dataclass
class Diagnostic:
    phase: str
    severity: str
    message: str
    line: int | None = None
    column: int | None = None


@dataclass
class Expr:
    kind: str
    value: Any = None
    children: list["Expr"] = field(default_factory=list)


@dataclass
class SelectItem:
    expression: Expr
    alias: str | None = None


@dataclass
class TableRef:
    name: str
    alias: str | None = None


@dataclass
class Join:
    join_type: str
    table: TableRef
    condition: Expr | None = None


@dataclass
class OrderItem:
    expression: Expr
    direction: str = "ASC"


@dataclass
class SelectStatement:
    distinct: bool
    columns: list[SelectItem]
    from_table: TableRef
    joins: list[Join] = field(default_factory=list)
    where: Expr | None = None
    group_by: list[Expr] = field(default_factory=list)
    having: Expr | None = None
    order_by: list[OrderItem] = field(default_factory=list)
    limit: int | None = None


@dataclass
class OtherStatement:
    """Token-preserving representation for supported non-SELECT statements."""
    statement_type: str
    tokens: list[Token]


class QueryError(Exception):
    def __init__(self, message: str, token: Token | None = None, phase: str = "syntax"):
        super().__init__(message)
        self.token = token
        self.phase = phase


class Lexer:
    def __init__(self, sql: str):
        self.sql = sql

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        line = column = 1
        for match in TOKEN_RE.finditer(self.sql):
            value = match.group()
            kind = match.lastgroup or "INVALID"
            start_line, start_column = line, column
            line += value.count("\n")
            column = (len(value.rsplit("\n", 1)[-1]) + 1) if "\n" in value else column + len(value)
            if kind in {"SPACE", "COMMENT"}:
                continue
            if kind == "INVALID":
                raise QueryError(f"Unexpected character {value!r}.", Token(kind, value, start_line, start_column), "lexical")
            if kind == "IDENT" and value.upper() in KEYWORDS:
                kind = "KEYWORD"
                value = value.upper()
            tokens.append(Token(kind, value, start_line, start_column))
        tokens.append(Token("EOF", "", line, column))
        return tokens


class Parser:
    """Recursive-descent parser for a practical SELECT query subset."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def advance(self) -> Token:
        token = self.current
        if token.kind != "EOF":
            self.pos += 1
        return token

    def accept(self, value: str) -> Token | None:
        if self.current.value.upper() == value.upper():
            return self.advance()
        return None

    def expect(self, value: str) -> Token:
        token = self.accept(value)
        if token is None:
            raise QueryError(f"Expected {value!r}, found {self.current.value or 'end of input'!r}.", self.current)
        return token

    def expect_identifier(self) -> Token:
        if self.current.kind != "IDENT":
            raise QueryError(f"Expected an identifier, found {self.current.value or 'end of input'!r}.", self.current)
        return self.advance()

    def parse(self) -> SelectStatement | OtherStatement:
        if self.current.value.upper() in {"INSERT", "UPDATE", "DELETE", "DROP", "CREATE", "ALTER", "TRUNCATE", "REPLACE"}:
            statement_type = self.advance().value.upper()
            body = []
            while self.current.kind != "EOF" and self.current.value != ";":
                body.append(self.advance())
            self.accept(";")
            if not body:
                raise QueryError(f"{statement_type} statement is missing its body.", self.current)
            if self.current.kind != "EOF":
                raise QueryError(f"Unexpected token {self.current.value!r} after the statement.", self.current)
            return OtherStatement(statement_type, body)
        self.expect("SELECT")
        distinct = bool(self.accept("DISTINCT"))
        columns = [self.select_item()]
        while self.accept(","):
            columns.append(self.select_item())
        self.expect("FROM")
        from_table = self.table_ref()
        joins: list[Join] = []
        while self.current.value.upper() in {"JOIN", "INNER", "LEFT", "RIGHT", "FULL", "CROSS"}:
            join_type = "INNER"
            if self.current.value.upper() in {"INNER", "LEFT", "RIGHT", "FULL", "CROSS"}:
                join_type = self.advance().value.upper()
                self.accept("OUTER")
            self.expect("JOIN")
            table = self.table_ref()
            condition = None if join_type == "CROSS" else None
            if self.accept("ON"):
                condition = self.expression()
            elif join_type != "CROSS":
                raise QueryError("Expected ON after this JOIN.", self.current)
            joins.append(Join(join_type, table, condition))
        where = self.expression() if self.accept("WHERE") else None
        group_by: list[Expr] = []
        if self.accept("GROUP"):
            self.expect("BY")
            group_by = self.expression_list()
        having = self.expression() if self.accept("HAVING") else None
        order_by: list[OrderItem] = []
        if self.accept("ORDER"):
            self.expect("BY")
            while True:
                expr = self.expression()
                direction = "DESC" if self.accept("DESC") else "ASC"
                if direction == "ASC":
                    self.accept("ASC")
                order_by.append(OrderItem(expr, direction))
                if not self.accept(","):
                    break
        limit = None
        if self.accept("LIMIT"):
            if self.current.kind != "NUMBER" or "." in self.current.value:
                raise QueryError("LIMIT must be a non-negative integer.", self.current)
            limit = int(self.advance().value)
            if self.accept("OFFSET"):
                if self.current.kind != "NUMBER" or "." in self.current.value:
                    raise QueryError("OFFSET must be a non-negative integer.", self.current)
                self.advance()
        self.accept(";")
        if self.current.kind != "EOF":
            raise QueryError(f"Unexpected token {self.current.value!r} after the query.", self.current)
        return SelectStatement(distinct, columns, from_table, joins, where, group_by, having, order_by, limit)

    def select_item(self) -> SelectItem:
        expr = self.expression()
        alias = None
        if self.accept("AS"):
            alias = self.expect_identifier().value
        elif self.current.kind == "IDENT":
            alias = self.advance().value
        return SelectItem(expr, alias)

    def table_ref(self) -> TableRef:
        name = self.expect_identifier().value
        if self.accept("."):
            name += "." + self.expect_identifier().value
        alias = None
        if self.accept("AS"):
            alias = self.expect_identifier().value
        elif self.current.kind == "IDENT":
            alias = self.advance().value
        return TableRef(name, alias)

    def expression_list(self) -> list[Expr]:
        result = [self.expression()]
        while self.accept(","):
            result.append(self.expression())
        return result

    # Pratt expression parser. Precedence increases from OR to arithmetic.
    PRECEDENCE = {"OR": 1, "AND": 2, "=": 3, "<>": 3, "!=": 3, "<": 3, ">": 3,
                  "<=": 3, ">=": 3, "LIKE": 3, "IN": 3, "IS": 3, "BETWEEN": 3,
                  "+": 4, "-": 4, "*": 5, "/": 5, "%": 5}

    def expression(self, min_precedence: int = 1) -> Expr:
        left = self.prefix()
        while True:
            op = self.current.value.upper()
            if op == "NOT" and self.pos + 1 < len(self.tokens) and self.tokens[self.pos + 1].value.upper() in {"IN", "LIKE", "BETWEEN"}:
                op = "NOT " + self.tokens[self.pos + 1].value.upper()
            base_op = op.removeprefix("NOT ")
            precedence = self.PRECEDENCE.get(base_op, 0)
            if precedence < min_precedence:
                break
            if op.startswith("NOT "):
                self.advance()
            self.advance()
            if base_op == "IS":
                negated = bool(self.accept("NOT"))
                right = self.prefix()
                left = Expr("binary", "IS NOT" if negated else "IS", [left, right])
            elif base_op == "IN":
                self.expect("(")
                values = self.expression_list()
                self.expect(")")
                left = Expr("binary", "NOT IN" if op.startswith("NOT ") else "IN", [left, Expr("list", children=values)])
            elif base_op == "BETWEEN":
                low = self.expression(precedence + 1)
                self.expect("AND")
                high = self.expression(precedence + 1)
                left = Expr("binary", "NOT BETWEEN" if op.startswith("NOT ") else "BETWEEN", [left, Expr("range", children=[low, high])])
            else:
                right = self.expression(precedence + 1)
                left = Expr("binary", "NOT LIKE" if op.startswith("NOT ") else base_op, [left, right])
        return left

    def prefix(self) -> Expr:
        token = self.current
        if self.accept("NOT"):
            return Expr("unary", "NOT", [self.expression(3)])
        if token.value in {"+", "-"}:
            self.advance()
            return Expr("unary", token.value, [self.expression(6)])
        if self.accept("("):
            expr = self.expression()
            self.expect(")")
            return expr
        if token.kind == "NUMBER":
            self.advance()
            return Expr("number", token.value)
        if token.kind == "STRING":
            self.advance()
            return Expr("string", token.value)
        if token.value.upper() in {"NULL", "TRUE", "FALSE"}:
            self.advance()
            return Expr("keyword", token.value.upper())
        if token.kind == "IDENT":
            name = self.advance().value
            if self.accept("("):
                args = [] if self.accept(")") else self.expression_list()
                if args and self.accept(")") is None:
                    raise QueryError("Expected ')' after function arguments.", self.current)
                return Expr("call", name, args)
            if self.accept("."):
                if self.accept("*"):
                    return Expr("column", name + ".*")
                return Expr("column", name + "." + self.expect_identifier().value)
            return Expr("column", name)
        if self.accept("*"):
            return Expr("star", "*")
        raise QueryError(f"Expected an expression, found {token.value or 'end of input'!r}.", token)


def expr_dict(expr: Expr) -> dict[str, Any]:
    return {"type": expr.kind, "value": expr.value, "children": [expr_dict(child) for child in expr.children]}


def _name_key(name: str) -> str:
    return name.rsplit(".", 1)[-1].strip('"`[]').lower()


class SemanticAnalyzer:
    def __init__(self, schema: dict[str, list[str]]):
        self.schema = {table.lower(): {column.lower() for column in columns} for table, columns in schema.items()}
        self.diagnostics: list[Diagnostic] = []

    def analyze(self, stmt: SelectStatement) -> list[Diagnostic]:
        refs = [stmt.from_table] + [join.table for join in stmt.joins]
        tables: dict[str, str] = {}
        for ref in refs:
            table_key = ref.name.lower()
            if table_key not in self.schema:
                self.diagnostics.append(Diagnostic("semantic", "error", f"Unknown table {ref.name!r}."))
                continue
            qualifier = (ref.alias or ref.name.rsplit(".", 1)[-1]).lower()
            if qualifier in tables:
                self.diagnostics.append(Diagnostic("semantic", "error", f"Duplicate table alias {qualifier!r}."))
            tables[qualifier] = table_key
        known_columns = set().union(*(self.schema.get(table, set()) for table in tables.values()))
        expressions: list[Expr] = []
        for item in stmt.columns:
            expressions.append(item.expression)
        expressions += [expr for join in stmt.joins if join.condition for expr in [join.condition]]
        expressions += [expr for expr in (stmt.where, stmt.having) if expr]
        expressions += stmt.group_by + [item.expression for item in stmt.order_by]
        for expr in expressions:
            self._check_expr(expr, tables, known_columns)
        return self.diagnostics

    def _check_expr(self, expr: Expr, tables: dict[str, str], known_columns: set[str]) -> None:
        if expr.kind == "column":
            parts = expr.value.split(".")
            if len(parts) == 2:
                qualifier, column = parts[0].lower(), parts[1].lower()
                if qualifier not in tables:
                    self.diagnostics.append(Diagnostic("semantic", "error", f"Unknown table or alias {parts[0]!r} in column reference."))
                elif column not in self.schema[tables[qualifier]]:
                    self.diagnostics.append(Diagnostic("semantic", "error", f"Unknown column {column!r} on {parts[0]!r}."))
            elif expr.value.lower() not in known_columns:
                self.diagnostics.append(Diagnostic("semantic", "error", f"Unknown column {expr.value!r}."))
        for child in expr.children:
            self._check_expr(child, tables, known_columns)


def format_expr(expr: Expr) -> str:
    if expr.kind in {"column", "number", "string", "keyword", "star"}:
        return str(expr.value)
    if expr.kind == "call":
        return f"{expr.value.upper()}({', '.join(format_expr(c) for c in expr.children)})"
    if expr.kind == "list":
        return ", ".join(format_expr(c) for c in expr.children)
    if expr.kind == "range":
        return f"{format_expr(expr.children[0])} AND {format_expr(expr.children[1])}"
    if expr.kind == "unary":
        return f"{expr.value} {format_expr(expr.children[0])}" if expr.value == "NOT" else f"{expr.value}{format_expr(expr.children[0])}"
    if expr.kind == "binary":
        return f"{format_expr(expr.children[0])} {expr.value} {format_expr(expr.children[1])}"
    return str(expr.value or "")


def format_sql(stmt: SelectStatement) -> str:
    lines = ["SELECT" + (" DISTINCT" if stmt.distinct else "")]
    lines.extend("    " + format_expr(item.expression) + (f" AS {item.alias}" if item.alias else "") + ("," if i < len(stmt.columns) - 1 else "") for i, item in enumerate(stmt.columns))
    base = "FROM " + stmt.from_table.name + (f" AS {stmt.from_table.alias}" if stmt.from_table.alias else "")
    lines.append(base)
    for join in stmt.joins:
        part = f"{join.join_type} JOIN {join.table.name}"
        if join.table.alias:
            part += f" AS {join.table.alias}"
        if join.condition:
            part += " ON " + format_expr(join.condition)
        lines.append(part)
    if stmt.where:
        lines.append("WHERE " + format_expr(stmt.where))
    if stmt.group_by:
        lines.append("GROUP BY " + ", ".join(map(format_expr, stmt.group_by)))
    if stmt.having:
        lines.append("HAVING " + format_expr(stmt.having))
    if stmt.order_by:
        lines.append("ORDER BY " + ", ".join(format_expr(i.expression) + (" DESC" if i.direction == "DESC" else "") for i in stmt.order_by))
    if stmt.limit is not None:
        lines.append(f"LIMIT {stmt.limit}")
    return "\n".join(lines) + ";"


AGGREGATES = {"COUNT", "SUM", "AVG", "MIN", "MAX"}


def walk_expr(expr: Expr) -> Iterable[Expr]:
    yield expr
    for child in expr.children:
        yield from walk_expr(child)


def analyze_query(sql: str, schema: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """Analyze SQL and return a JSON-serializable review report."""
    schema = schema or DEFAULT_SCHEMA
    diagnostics: list[Diagnostic] = []
    tokens: list[Token] = []
    try:
        tokens = Lexer(sql).tokenize()
    except QueryError as error:
        token = error.token
        diagnostics.append(Diagnostic(error.phase, "error", str(error), token.line if token else None, token.column if token else None))
        return _error_report(sql, tokens, diagnostics)
    try:
        stmt = Parser(tokens).parse()
    except QueryError as error:
        token = error.token
        diagnostics.append(Diagnostic(error.phase, "error", str(error), token.line if token else None, token.column if token else None))
        return _error_report(sql, tokens, diagnostics)

    if isinstance(stmt, OtherStatement):
        formatted = _format_other(stmt)
        ast = {"type": f"{stmt.statement_type.title()}Statement", "tokens": [asdict(t) for t in stmt.tokens]}
        tree = {"node": stmt.statement_type, "children": [t.value for t in stmt.tokens]}
        return {"ok": True, "input_sql": sql, "tokens": [asdict(t) for t in tokens if t.kind != "EOF"],
                "parse_tree": tree, "ast": ast, "formatted_sql": formatted, "diagnostics": [],
                "code_smells": [], "recommendations": [],
                "quality_score": {"score": 100, "label": "informational", "basis": "Statement tokenized and categorized; detailed validation is currently limited to SELECT."},
                "complexity": {"statement_type": stmt.statement_type, "tokens": len(stmt.tokens), "level": "not assessed"},
                "report": f"Query Forge recognized a {stmt.statement_type} statement. Detailed grammar and schema checks are currently implemented for SELECT queries only."}
    diagnostics.extend(SemanticAnalyzer(schema).analyze(stmt))
    smells: list[dict[str, str]] = []
    recommendations: list[dict[str, str]] = []
    if any(item.expression.kind == "star" or (item.expression.kind == "column" and item.expression.value.endswith(".*")) for item in stmt.columns):
        smells.append({"code": "SELECT_STAR", "severity": "warning", "message": "SELECT * can make results fragile and fetch unnecessary columns."})
        recommendations.append({"rule": "explicit_columns", "message": "List only the columns the caller needs; the analyzer cannot infer that list."})
    if not stmt.where:
        smells.append({"code": "NO_FILTER", "severity": "info", "message": "The query has no WHERE clause; confirm that reading every matching row is intended."})
    if len(stmt.joins) >= 4:
        smells.append({"code": "MANY_JOINS", "severity": "warning", "message": f"The query contains {len(stmt.joins)} joins; review whether each join is needed."})
    if stmt.where:
        for node in walk_expr(stmt.where):
            if node.kind == "binary" and node.value == "OR":
                smells.append({"code": "OR_PREDICATE", "severity": "info", "message": "Review OR predicates for clarity and database-specific index behavior."})
                break
    for node in ([stmt.where] if stmt.where else []) + [j.condition for j in stmt.joins if j.condition]:
        for item in walk_expr(node):
            if item.kind == "binary" and item.value == "=" and item.children[0].kind == "column" and item.children[1].kind == "column":
                pass  # Equality joins are normal; no unsafe rewrite is proposed.
    all_exprs = [item.expression for item in stmt.columns] + stmt.group_by + [item.expression for item in stmt.order_by]
    all_exprs += [expr for expr in (stmt.where, stmt.having) if expr]
    all_exprs += [j.condition for j in stmt.joins if j.condition]
    tables = [stmt.from_table] + [j.table for j in stmt.joins]
    join_count = len(stmt.joins)
    conditions = sum(1 for expr in ([stmt.where, stmt.having] + [j.condition for j in stmt.joins]) if expr for node in walk_expr(expr) if node.kind == "binary" and node.value in {"=", "<>", "!=", "<", ">", "<=", ">=", "LIKE", "IN", "NOT IN", "IS", "IS NOT", "BETWEEN", "NOT BETWEEN"})
    nested = sum(1 for token in tokens if token.value.upper() == "SELECT") - 1
    complexity = {"tables": len(tables), "joins": join_count, "conditions": conditions, "nested_queries": max(0, nested), "expression_nodes": sum(1 for expr in all_exprs for _ in walk_expr(expr)), "level": "low"}
    complexity["level"] = "high" if join_count >= 4 or conditions >= 8 or nested else "medium" if join_count >= 2 or conditions >= 4 else "low"
    score = max(0, 100 - (20 if any(s["code"] == "SELECT_STAR" for s in smells) else 0) - (10 if any(s["code"] == "NO_FILTER" for s in smells) else 0) - min(join_count * 4, 16) - min(max(0, conditions - 3) * 2, 12))
    errors = [d for d in diagnostics if d.severity == "error"]
    score = 0 if errors else score
    tree = _parse_tree(stmt)
    return {
        "ok": not errors,
        "input_sql": sql,
        "tokens": [asdict(t) for t in tokens if t.kind != "EOF"],
        "parse_tree": tree,
        "ast": _ast_dict(stmt),
        "formatted_sql": format_sql(stmt),
        "diagnostics": [asdict(d) for d in diagnostics],
        "code_smells": smells,
        "recommendations": recommendations,
        "quality_score": {"score": score, "label": _score_label(score), "basis": "Rule-based educational estimate; not a performance prediction."},
        "complexity": complexity,
        "report": _report_text(diagnostics, smells, score, complexity, recommendations),
    }


def _ast_dict(stmt: SelectStatement) -> dict[str, Any]:
    return {"type": "SelectStatement", "distinct": stmt.distinct,
            "columns": [{"expression": expr_dict(i.expression), "alias": i.alias} for i in stmt.columns],
            "from": asdict(stmt.from_table),
            "joins": [{"join_type": j.join_type, "table": asdict(j.table), "condition": expr_dict(j.condition) if j.condition else None} for j in stmt.joins],
            "where": expr_dict(stmt.where) if stmt.where else None,
            "group_by": [expr_dict(e) for e in stmt.group_by], "having": expr_dict(stmt.having) if stmt.having else None,
            "order_by": [{"expression": expr_dict(i.expression), "direction": i.direction} for i in stmt.order_by], "limit": stmt.limit}


def _format_other(stmt: OtherStatement) -> str:
    """Readable token-based formatting for non-SELECT statements."""
    values = [t.value for t in stmt.tokens]
    output = [stmt.statement_type]
    no_space_before = {",", ")", ".", ";"}
    no_space_after = {"(", "."}
    previous = stmt.statement_type
    for value in values:
        upper = value.upper()
        if upper in {"VALUES", "SET", "WHERE", "FROM", "INTO", "TABLE", "ADD"}:
            output.append("\n" + upper)
        elif value not in no_space_before and previous not in no_space_after:
            output.append(" " + value)
        else:
            output.append(value)
        previous = value
    return "".join(output).strip() + ";"


def _parse_tree(stmt: SelectStatement) -> dict[str, Any]:
    # A compact concrete-structure view; the AST above is the normalized tree.
    ast = _ast_dict(stmt)
    return {"node": "Query", "children": [
        {"node": "SELECT", "children": ast["columns"]},
        {"node": "FROM", "children": [ast["from"]]},
        {"node": "JOIN", "children": ast["joins"]},
        *([{"node": "WHERE", "children": [ast["where"]]}] if ast["where"] else []),
        *([{ "node": "GROUP BY", "children": ast["group_by"]}] if ast["group_by"] else []),
        *([{ "node": "HAVING", "children": [ast["having"]]}] if ast["having"] else []),
        *([{ "node": "ORDER BY", "children": ast["order_by"]}] if ast["order_by"] else []),
        *([{ "node": "LIMIT", "children": [stmt.limit]}] if stmt.limit is not None else []),
    ]}


def _score_label(score: int) -> str:
    return "excellent" if score >= 90 else "good" if score >= 75 else "fair" if score >= 50 else "needs review"


def _report_text(diagnostics: list[Diagnostic], smells: list[dict[str, str]], score: int, complexity: dict[str, Any], recommendations: list[dict[str, str]]) -> str:
    lines = [f"Query Forge Review — score {score}/100 ({_score_label(score)})", f"Complexity: {complexity['level']} | tables: {complexity['tables']} | joins: {complexity['joins']} | conditions: {complexity['conditions']} | nested queries: {complexity['nested_queries']}"]
    lines += [f"{d.severity.upper()} [{d.phase}]: {d.message}" for d in diagnostics]
    lines += [f"{s['severity'].upper()} [{s['code']}]: {s['message']}" for s in smells]
    lines += [f"SUGGESTION [{r['rule']}]: {r['message']}" for r in recommendations]
    if len(lines) == 2:
        lines.append("No issues found by the configured checks.")
    lines.append("Rule-based analysis only; validate performance with your database and execution plan.")
    return "\n".join(lines)


def _error_report(sql: str, tokens: list[Token], diagnostics: list[Diagnostic]) -> dict[str, Any]:
    return {"ok": False, "input_sql": sql, "tokens": [asdict(t) for t in tokens if t.kind != "EOF"], "parse_tree": None, "ast": None,
            "formatted_sql": None, "diagnostics": [asdict(d) for d in diagnostics], "code_smells": [], "recommendations": [],
            "quality_score": {"score": 0, "label": "needs review", "basis": "Analysis stopped after an earlier phase error."}, "complexity": None,
            "report": "\n".join(f"{d.severity.upper()} [{d.phase}]: {d.message}" for d in diagnostics)}


DEFAULT_SCHEMA = {
    "Student": ["student_id", "name", "age", "department_id"],
    "Department": ["department_id", "department_name"],
    "Enrollment": ["student_id", "course_id", "grade"],
    "Course": ["course_id", "course_name", "credits"],
}


def load_schema(path: str | None) -> dict[str, list[str]]:
    if not path:
        return DEFAULT_SCHEMA
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, dict) or any(not isinstance(cols, list) for cols in raw.values()):
        raise ValueError("Schema JSON must map table names to arrays of column names.")
    return raw


def main(argv: list[str] | None = None) -> int:
    cli = argparse.ArgumentParser(description="Analyze a SELECT query with Query Forge.")
    source = cli.add_mutually_exclusive_group(required=True)
    source.add_argument("--sql", help="SQL query to analyze")
    source.add_argument("--file", help="Read SQL from a file (use - for stdin)")
    cli.add_argument("--schema", help="JSON object mapping table names to column arrays")
    cli.add_argument("--json", action="store_true", help="Print the complete machine-readable report")
    args = cli.parse_args(argv)
    try:
        sql = args.sql if args.sql is not None else (sys.stdin.read() if args.file == "-" else open(args.file, encoding="utf-8").read())
        result = analyze_query(sql, load_schema(args.schema))
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"Query Forge: {error}", file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["report"])
        if result["formatted_sql"]:
            print("\nFormatted SQL:\n" + result["formatted_sql"])
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
