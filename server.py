#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
server.py -- tiny dependency-free HTTP wrapper around translator.py
so n8n / Make / Zapier / Power Automate can call it with a plain HTTP Request node.

Run:
    python3 server.py                      # 0.0.0.0:8080
    python3 server.py --port 8080 --model gpt-4o-mini --provider openai
    RU_API_KEY=sk-... python3 server.py

Endpoints
---------
GET  /health
     -> {"status":"ok","provider":"openai","model":"gpt-4o-mini","key_present":true}

POST /translate
     body A: {"text": "Headline\n- bullet one\n- bullet two"}
     body B: {"title_en": "...", "bullets_en": ["...","..."], "summary_en": "..."}
     optional overrides in the body: "provider", "model", "temperature"
     -> {"ok":true,
         "roman_urdu":"...",
         "lines_ur":["...","...","..."],
         "lines_en":["...","...","..."],
         "title_ur":"...","bullets_ur":["...","..."],"summary_ur":"...",
         "method":"llm","attempts":1,"problems":[],"warning":null}

POST /translate-card
     Convenience: returns the exact two strings your image/card step needs.
     body: {"title_en":"...","bullets_en":["...","..."]}
     -> {"card_text_en":"TITLE\n- b1\n- b2","card_text_ur":"TITLE\n- b1\n- b2",
         "bullets_en":[...],"bullets_ur":[...],"title_en":"...","title_ur":"..."}

Auth: set RU_SERVER_TOKEN to require header  Authorization: Bearer <token>
"""

from __future__ import annotations

import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict

try:                                    # used as a package
    from . import translator as R
except ImportError:                     # folder copied flat / run as a script
    import translator as R              # type: ignore[no-redef]

DEFAULTS: Dict[str, Any] = {
    "provider": R.DEFAULT_PROVIDER,
    "model": R.DEFAULT_MODEL,
    "temperature": R.DEFAULT_TEMPERATURE,
    "max_retries": R.DEFAULT_MAX_RETRIES,
    "timeout": R.DEFAULT_TIMEOUT,
}
SERVER_TOKEN = os.environ.get("RU_SERVER_TOKEN", "")

DEMO_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>English to Roman Urdu - News Card Preview</title>
<style>
  * { box-sizing: border-box; }
  body { margin:0; font-family: system-ui, -apple-system, "Segoe UI", Roboto, Arial, sans-serif;
         background:#0f1220; color:#e8eaf2; padding:24px; }
  h1 { font-size:20px; margin:0 0 4px; }
  p.sub { margin:0 0 20px; color:#9aa0bd; font-size:13px; }
  .wrap { display:grid; grid-template-columns:1fr 1fr; gap:18px; max-width:1100px; margin:auto; }
  @media (max-width:860px){ .wrap{ grid-template-columns:1fr; } }
  .panel { background:#171a2b; border:1px solid #262a44; border-radius:12px; padding:16px; }
  label { display:block; font-size:12px; color:#9aa0bd; margin:10px 0 5px; text-transform:uppercase; letter-spacing:.06em; }
  input, textarea, select { width:100%; background:#0f1220; color:#e8eaf2; border:1px solid #2c3152;
    border-radius:8px; padding:10px; font-size:14px; font-family:inherit; }
  textarea { min-height:120px; resize:vertical; line-height:1.5; }
  button { margin-top:14px; width:100%; background:#3b6ef5; color:#fff; border:0; border-radius:8px;
    padding:12px; font-size:15px; font-weight:600; cursor:pointer; }
  button:disabled { opacity:.55; cursor:default; }
  .card { background:#fff; color:#12142a; border-radius:14px; overflow:hidden; }
  .card .top { background:#111a3d; color:#fff; padding:12px 16px; display:flex;
    justify-content:space-between; align-items:center; font-size:12px; letter-spacing:.04em; }
  .card .top b { font-size:14px; }
  .card .body { padding:16px; }
  .card h2 { font-size:18px; margin:0 0 10px; line-height:1.3; }
  .card ul { margin:0 0 14px; padding-left:18px; font-size:14px; line-height:1.55; }
  .card hr { border:0; border-top:1px dashed #c9cee6; margin:14px 0; }
  .card .ur h2 { font-size:16px; color:#1d3fa8; }
  .card .ur ul { color:#2b3050; font-size:13.5px; }
  .card .foot { padding:10px 16px; background:#f2f4fb; color:#5a628a; font-size:12px; }
  .meta { font-size:12px; color:#9aa0bd; margin-top:10px; word-break:break-word; }
  .badge { display:inline-block; padding:2px 8px; border-radius:99px; font-size:11px; font-weight:600; }
  .ok { background:#12351f; color:#5fe39a; } .bad { background:#3a1620; color:#ff8fa3; }
  .banner { max-width:1100px; margin:0 auto 18px; padding:12px 14px; border-radius:10px; font-size:13px; line-height:1.5; }
  .banner.warn { background:#3a2a10; color:#ffcf7a; border:1px solid #6b4a15; }
  .banner.good { background:#12351f; color:#8ff0b8; border:1px solid #1f5c34; }
  code { background:#0f1220; padding:1px 5px; border-radius:4px; }
</style></head>
<body>
<h1>English &rarr; Roman Urdu news-card translator</h1>
<p class="sub">Aapke hourly news agent ka preview: English bullets + Roman Urdu bullets, ek hi card par.
   Endpoint: <code>POST /translate-card</code></p>
<div class="banner __BANNERCLS__">__BANNER__</div>
<div class="wrap">
  <div class="panel">
    <label>Logo / page name</label>
    <input id="logo" value="My News Page">
    <label>Hashtags</label>
    <input id="tags" value="#BreakingNews #Pakistan">
    <label>English title</label>
    <input id="title" value="Earthquake of magnitude 6.2 hits eastern Afghanistan">
    <label>English bullets (one per line, 3-5)</label>
    <textarea id="bullets">At least 18 people were killed and 40 injured
Rescue teams reached the affected area after six hours
The UN has promised emergency aid
Roads and communication networks remain badly damaged</textarea>
    <button id="go">Translate &amp; build card</button>
    <div class="meta" id="meta"></div>
  </div>
  <div class="panel">
    <label>Card preview</label>
    <div class="card" id="card">
      <div class="top"><b id="cLogo">My News Page</b><span id="cDate"></span></div>
      <div class="body">
        <h2 id="cTitleEn">English title</h2>
        <ul id="cBulletsEn"></ul>
        <hr>
        <div class="ur">
          <h2 id="cTitleUr">Roman Urdu title</h2>
          <ul id="cBulletsUr"></ul>
        </div>
      </div>
      <div class="foot" id="cTags">#BreakingNews #Pakistan</div>
    </div>
  </div>
</div>
<script>
const $ = id => document.getElementById(id);
$('cDate').textContent = new Date().toLocaleDateString('en-GB',{day:'2-digit',month:'short',year:'numeric'});
function render(data){
  $('cLogo').textContent = $('logo').value || 'My News Page';
  $('cTags').textContent = $('tags').value;
  $('cTitleEn').textContent = data.title_en;
  $('cTitleUr').textContent = data.title_ur || '(translation pending)';
  const mk = (arr) => arr.map(t => { const li=document.createElement('li'); li.textContent=t; return li; });
  $('cBulletsEn').replaceChildren(...mk(data.bullets_en||[]));
  $('cBulletsUr').replaceChildren(...mk(data.bullets_ur||[]));
}
$('go').onclick = async () => {
  const btn = $('go'); btn.disabled = true; btn.textContent = 'Translating...';
  $('meta').innerHTML = '';
  const bullets = $('bullets').value.split('\\n').map(s=>s.trim()).filter(Boolean);
  try{
    const r = await fetch('/translate-card', {method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({title_en: $('title').value, bullets_en: bullets})});
    const d = await r.json();
    render(d);
    const cls = d.ok ? 'ok' : 'bad';
    $('meta').innerHTML = '<span class="badge '+cls+'">'+(d.ok?'OK':'CHECK')+'</span> '
      + 'bullets: '+(d.bullets_en.length)+' EN / '+(d.bullets_ur||[]).length+' UR &nbsp; HTTP '+r.status;
    $('meta').innerHTML += '<br><br><b>card_text_ur</b><br>' + (d.card_text_ur||'').replace(/\\n/g,'<br>');
  }catch(e){ $('meta').textContent = 'Error: ' + e.message; }
  btn.disabled = false; btn.textContent = 'Translate & build card';
};
</script>
</body></html>"""



def _kw_from(body: Dict[str, Any]) -> Dict[str, Any]:
    kw = dict(DEFAULTS)
    for key in ("provider", "model", "temperature", "max_retries", "timeout"):
        if key in body and body[key] is not None:
            kw[key] = body[key]
    if "use_few_shot" in body:
        kw["use_few_shot"] = bool(body["use_few_shot"])
    return kw


def handle_translate(body: Dict[str, Any]) -> Dict[str, Any]:
    kw = _kw_from(body)
    if "text" in body and body.get("text") is not None:
        return R.translate_news(str(body["text"]), **kw)
    return R.translate_payload(body, **kw)


def handle_translate_card(body: Dict[str, Any]) -> Dict[str, Any]:
    kw = _kw_from(body)
    title_en = str(body.get("title_en") or body.get("title") or body.get("headline") or "").strip()
    bullets_en = body.get("bullets_en") or body.get("bullets") or body.get("points") or []
    if isinstance(bullets_en, str):
        bullets_en = [b.strip() for b in bullets_en.split("\n") if b.strip()]
    bullets_en = [str(b).strip() for b in bullets_en if str(b).strip()]

    title_ur = R.translate_news(title_en, **kw)["roman_urdu"] if title_en else ""
    bullets_ur: list[str] = []
    if bullets_en:
        res = R.translate_news("\n".join(bullets_en), **kw)
        bullets_ur = res["lines_ur"]
        if len(bullets_ur) != len(bullets_en):
            bullets_ur = (bullets_ur + bullets_en[len(bullets_ur):])[: len(bullets_en)]

    card_en = "\n".join([title_en] + [f"- {b}" for b in bullets_en]).strip()
    card_ur = "\n".join([title_ur] + [f"- {b}" for b in bullets_ur]).strip()

    return {
        "ok": bool(card_ur),
        "card_text_en": card_en,
        "card_text_ur": card_ur,
        "title_en": title_en,
        "title_ur": title_ur,
        "bullets_en": bullets_en,
        "bullets_ur": bullets_ur,
        "bullet_count": len(bullets_ur),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "RomanUrduTranslator/2.0"

    def _send(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._send(204, {})

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?")[0].rstrip("/")
        if path in ("", "/demo", "/index.html"):
            mode_is_mock = DEFAULTS["provider"] == "mock"
            key_ok = bool(R.API_KEYS.get(DEFAULTS["provider"]))
            if mode_is_mock:
                banner, cls = ("MOCK MODE — koi LLM API key set nahi hai. Yeh output ek simple "
                               "word-swap demo hai, asli translation NAHI. Real quality ke liye: "
                               "RU_PROVIDER=openai RU_MODEL=gpt-4o-mini RU_API_KEY=sk-... ke sath "
                               "server dobara start karein.", "warn")
            elif not key_ok:
                banner = (f"Provider {DEFAULTS['provider']} set hai lekin API key missing hai — "
                          "requests fail honge. RU_API_KEY export karein.", "warn")
            else:
                banner = (f"LIVE — provider={DEFAULTS['provider']}, model={DEFAULTS['model']}. "
                          "Output asli LLM Roman Urdu translation hai.", "good")
            html = DEMO_HTML.replace("__BANNER__", banner).replace("__BANNERCLS__", cls)
            raw = html.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
            return
        if path == "/health":
            provider = DEFAULTS["provider"]
            self._send(200, {
                "status": "ok",
                "provider": provider,
                "model": DEFAULTS["model"],
                "key_present": bool(R.API_KEYS.get(provider)),
            })
        else:
            self._send(404, {"error": "not found", "endpoints": ["/", "/health", "/translate", "/translate-card"]})

    def do_POST(self) -> None:  # noqa: N802
        if SERVER_TOKEN:
            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {SERVER_TOKEN}":
                self._send(401, {"error": "unauthorized"})
                return

        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except json.JSONDecodeError as exc:
            self._send(400, {"error": f"invalid JSON: {exc}"})
            return
        if not isinstance(body, dict):
            self._send(400, {"error": "JSON body must be an object"})
            return

        route = self.path.rstrip("/")
        try:
            if route == "/translate":
                self._send(200, handle_translate(body))
            elif route == "/translate-card":
                self._send(200, handle_translate_card(body))
            else:
                self._send(404, {"error": "not found"})
        except Exception as exc:  # noqa: BLE001
            self._send(500, {"error": str(exc), "ok": False})

    def log_message(self, fmt: str, *args: Any) -> None:  # quieter logs
        print("[http] " + fmt % args, flush=True)


def serve(host: str = "0.0.0.0", port: int = 8080, defaults: Dict[str, Any] | None = None) -> int:
    if defaults:
        for key in ("provider", "model", "temperature", "max_retries", "timeout"):
            if defaults.get(key) is not None:
                DEFAULTS[key] = defaults[key]
    httpd = ThreadingHTTPServer((host, port), Handler)
    print(f"Roman Urdu translation server on http://{host}:{port}", flush=True)
    print(f"provider={DEFAULTS['provider']} model={DEFAULTS['model']}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=int(os.environ.get("PORT", "8080")))
    ap.add_argument("--provider", default=R.DEFAULT_PROVIDER)
    ap.add_argument("--model", default=R.DEFAULT_MODEL)
    ap.add_argument("--temperature", type=float, default=R.DEFAULT_TEMPERATURE)
    ap.add_argument("--max-retries", type=int, dest="max_retries", default=R.DEFAULT_MAX_RETRIES)
    ap.add_argument("--timeout", type=int, default=R.DEFAULT_TIMEOUT)
    a = ap.parse_args()
    raise SystemExit(serve(a.host, a.port, vars(a)))
