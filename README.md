# Query Forge

An educational, standard-library-only Python SQL analyzer. Query Forge tokenizes and parses a supported `SELECT` subset, builds a parse tree and AST, checks names against a schema, formats the query, reports code smells, and calculates rule-based quality and complexity estimates. It does not execute SQL or promise that suggestions improve runtime performance.

## Run

```bash
python query_forge.py --sql 'select * from Student where age > 20;'
```

Print all pipeline output as JSON:

```bash
python query_forge.py --sql 'SELECT s.name FROM Student s WHERE s.age > 20' --json
```

Read a query from a file or standard input:

```bash
python query_forge.py --file query.sql --schema schema.json
cat query.sql | python query_forge.py --file -
```

Start the interactive browser view:

```bash
python query_forge_web.py
```

Then open `http://127.0.0.1:8000` in a browser. The page shows compiler stages, tokens, the parse tree and AST, semantic diagnostics, formatted SQL, code smells, and the review metrics.

The schema is a JSON object mapping table names to column-name arrays. The CLI reads [`schema.json`](schema.json) when you pass `--schema schema.json`; if `--schema` is omitted, it uses the built-in Student, Department, Enrollment, and Course schema. The browser UI loads `schema.json` beside `query_forge_web.py` automatically when the server starts, so restart the server after changing that file.

## Supported SQL subset

`SELECT` statements may include `DISTINCT`, expressions and aliases, a `FROM` table, `INNER`/`LEFT`/`RIGHT`/`FULL`/`CROSS JOIN`, `WHERE`, `GROUP BY`, `HAVING`, `ORDER BY`, and integer `LIMIT`/`OFFSET`. Expressions support arithmetic, comparisons, `AND`/`OR`/`NOT`, `IN`, `BETWEEN`, `LIKE`, `IS [NOT] NULL`, literals, parentheses, and function calls. Subqueries and other SQL statements are intentionally outside this first educational implementation.

Python callers can import `analyze_query(sql, schema=None)` from `query_forge.py`; it returns a JSON-serializable dictionary with tokens, tree views, diagnostics, formatted SQL, suggestions, and metrics.
