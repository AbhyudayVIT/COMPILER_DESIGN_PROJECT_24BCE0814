#!/usr/bin/env python3
"""Small local browser UI for Query Forge; uses only the Python standard library."""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from urllib.parse import urlparse

from query_forge import analyze_query, load_schema


# Resolve next to this script so the browser UI uses the same editable schema
# file regardless of the directory from which the server was started.
SCHEMA = load_schema(str(Path(__file__).resolve().with_name("schema.json")))


PAGE = r'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Query Forge</title><style>
:root{color-scheme:dark;--bg:#101820;--panel:#182630;--line:#30434f;--text:#e7f0f4;--muted:#9fb2bd;--accent:#79d6bd;--warn:#ffd27a;--bad:#ff8d8d}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.5 system-ui,sans-serif}header{padding:22px max(24px,calc((100vw - 1180px)/2));border-bottom:1px solid var(--line)}h1{margin:0;font-size:24px}header p{margin:4px 0 0;color:var(--muted)}main{max-width:1180px;margin:24px auto;padding:0 20px}.editor,.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}textarea{width:100%;height:150px;resize:vertical;background:#0d151b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:14px;font:14px/1.6 ui-monospace,monospace}button{background:var(--accent);border:0;border-radius:7px;padding:10px 17px;font-weight:700;cursor:pointer;margin-top:12px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.card{min-width:0;margin:0}h2{font-size:16px;margin:0 0 12px}.muted{color:var(--muted)}pre{white-space:pre-wrap;overflow-wrap:anywhere;margin:0;font:13px/1.55 ui-monospace,monospace}.pill{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:3px 9px;margin:0 6px 7px 0;color:var(--muted)}.issue{padding:8px 10px;border-left:3px solid var(--warn);background:#202d35;margin:8px 0}.error{border-color:var(--bad)}.score{font-size:28px;color:var(--accent);font-weight:700}details{margin-top:8px}summary{cursor:pointer;color:var(--accent)}#status{color:var(--muted);margin-left:12px}@media(max-width:720px){.grid{grid-template-columns:1fr}}
</style></head><body><header><h1>Query Forge</h1><p>Explore how a SELECT query moves through a compiler and rule-based review.</p></header>
<main><section class="editor"><h2>SQL query</h2><textarea id="sql" spellcheck="false">select * from Student where age > 20;</textarea><br><button id="run">Analyze query</button><span id="status"></span></section>
<section class="grid" id="results" hidden>
<article class="card"><h2>Review</h2><div id="score" class="score"></div><pre id="report"></pre></article>
<article class="card"><h2>Formatted SQL</h2><pre id="formatted"></pre></article>
<article class="card"><h2>Compiler stages</h2><div id="stages"></div></article>
<article class="card"><h2>Code smells and suggestions</h2><div id="smells"></div></article>
<article class="card"><h2>Abstract syntax tree</h2><pre id="ast"></pre></article>
<article class="card"><h2>Parse tree</h2><pre id="tree"></pre></article>
<article class="card"><h2>Diagnostics</h2><div id="diagnostics"></div></article>
<article class="card"><h2>Complexity</h2><pre id="complexity"></pre></article>
</section></main><script>
const $=id=>document.getElementById(id);const pretty=x=>JSON.stringify(x,null,2);
function list(target,items,empty){$(target).innerHTML=items.length?items.map(x=>`<div class="issue ${x.severity==='error'?'error':''}"><b>${x.severity.toUpperCase()}</b> ${x.message}</div>`).join(''):`<span class="muted">${empty}</span>`}
async function analyze(){ $('status').textContent='Analyzing…';$('results').hidden=true;try{const r=await fetch('/analyze',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({sql:$('sql').value})});const d=await r.json();$('results').hidden=false;$('score').textContent=`${d.quality_score.score}/100 · ${d.quality_score.label}`;$('report').textContent=d.report;$('formatted').textContent=d.formatted_sql||'Formatting is unavailable until parsing succeeds.';$('ast').textContent=d.ast?pretty(d.ast):'AST unavailable: an earlier phase reported an error.';$('tree').textContent=d.parse_tree?pretty(d.parse_tree):'Parse tree unavailable: an earlier phase reported an error.';$('complexity').textContent=d.complexity?pretty(d.complexity):'Complexity unavailable: an earlier phase reported an error.';list('diagnostics',d.diagnostics,'No errors detected.');list('smells',d.code_smells,'No code smells detected.');const stages=[['Lexical analysis',`${d.tokens.length} tokens`,true],['Syntax analysis',d.ast?'SELECT grammar accepted':'Stopped on syntax error',!!d.ast],['AST generation',d.ast?'AST generated':'Unavailable',!!d.ast],['Semantic analysis',d.diagnostics.some(x=>x.phase==='semantic'&&x.severity==='error')?'Schema errors found':'Schema checks complete',!d.diagnostics.some(x=>x.phase==='semantic'&&x.severity==='error')],['Formatting',d.formatted_sql?'SQL formatted':'Unavailable',!!d.formatted_sql],['Review and scoring',`${d.code_smells.length} smell(s); score ${d.quality_score.score}`,d.ok]];$('stages').innerHTML=stages.map((s,i)=>`<span class="pill">${i+1}. ${s[0]}</span> <span class="muted">${s[1]}</span><br>`).join('');$('status').textContent=d.ok?'Analysis complete':'Analysis completed with errors.'}catch(e){$('status').textContent='Could not reach the local analyzer.'}}
$('run').addEventListener('click',analyze);analyze();
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if urlparse(self.path).path != "/":
            self.send_error(404)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if urlparse(self.path).path != "/analyze":
            self.send_error(404)
            return
        try:
            length = min(int(self.headers.get("Content-Length", "0")), 1_000_000)
            payload = json.loads(self.rfile.read(length))
            sql = payload.get("sql", "")
            if not isinstance(sql, str):
                raise ValueError("SQL must be a string.")
            report = analyze_query(sql, SCHEMA)
            body = json.dumps(report).encode()
            self.send_response(200)
        except (ValueError, json.JSONDecodeError) as exc:
            body = json.dumps({"error": str(exc)}).encode()
            self.send_response(400)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print("[Query Forge] " + fmt % args)


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", 8000), Handler)
    print("Query Forge running at http://127.0.0.1:8000 — press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
