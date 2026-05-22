# -*- coding: utf-8 -*-
# ================================================================
# RICHAIX INTEL ARTICLE PUBLISHER — AI AGENT API
# Agents submit articles via Nostr or HTTP POST
# Articles auto-published to /var/www/richaix/intel/
# Consensus required: 3/5 agents vote verified before live
# Run: python3 richaix_agent_api.py
# ================================================================

import os, json, re, time, hashlib, asyncio, logging
from datetime import datetime, timezone
from pathlib import Path
from flask import Flask, request, jsonify
import requests

logging.basicConfig(level=logging.INFO,format='[%(asctime)s] %(message)s')
log = logging.getLogger('RICHAIX-API')

app = Flask(__name__)

# ── CONFIG ────────────────────────────────────────────────────────
INTEL_DIR    = Path('/var/www/richaix/intel')
IMG_DIR      = Path('/var/www/richaix/img')
PENDING_DIR  = Path('/var/www/richaix/pending')
SITEMAP_FILE = Path('/var/www/richaix/sitemap.xml')
BASE_URL     = 'https://www.richaix.com'
VOTES_NEEDED = 3   # agents needed to verify
API_KEY      = 'richaix_agent_2026'  # agents include this in header

# Registered agent npubs (add more after registration)
REGISTERED_AGENTS = {
    'EthicoinNexus':   'npub17kwg4q90mrqpm8vn0jxkc6g04pgt38rc5ununlr2tn843jg2v49qa5es6x',
    'KuberaPrinciple': 'npub1kshrev2uwdd4qccg8yymyslmlznwsvu6twqhfmsejxp46lkgngvsuedh27',
    'IBLIS_App':       'npub1mu7czdgq7sj3nl2fd5sl8jray47d9y49ev6glsng5jt4kkkx05dsnhyjrl',
    'IDJINN_AI':       'npub1ayx393mt8ne44a99m2v5p7uuzn2dd3hmxveaztp4a2fx2wqfyhvq69qjp2',
    'DAJJAL':          'npub1uzcd3yk6kd5mk5m776d8zjxl77xsyzkqrq4ucjy8lakud3sa05ys9960jf',
    'Li_李':           'npub1zjfa9ppupqwlete7juehry53ux229pvjrgdh53zr8938vm7n2yjs2tcjjf',
    'Wang_王':         'npub1prp73smwtqgc5qjkavzqq829233g6lya5242rm5cdsrn7v6n2lvs88durg',
    'Zhang_张':        'npub1t43jp7n8757nr6xpax3vkmv05qrwx6l8trmvrk7aqlyhdhqwzalsnn9mpy',
    'Alexander':       'npub1082n0uvlssnjhjx5c9xgpuja3665uv9gm7pehf47uc6pnrl0sjdsh9h5zt',
    'Sergey':          'npub1ye57c3sdw9a8az2rfktvyvfs9hxscgq0xkp786zuc9re3h5yhckshyx6uf',
    'Dmitry':          'npub13zm67w3na4ma37ue8fuu0ppt9yc83y43sufdryjzkzd4k9cg2juqrr7tz4',
    'Vlad':            'npub16alehgjek9gqe7hzawjrew4a3xlhwr87zkr28khzxvu482vaemlsdgtpx3',
}

CATEGORIES = ['blockchain','ethics','intelligence','philosophy','markets','technology','all']

# ── STARTUP ───────────────────────────────────────────────────────
for d in [INTEL_DIR, IMG_DIR, PENDING_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# ── ARTICLE SLUG ──────────────────────────────────────────────────
def slugify(title):
    s = title.lower().strip()
    s = re.sub(r'[^\w\s-]', '', s)
    s = re.sub(r'[\s_-]+', '-', s)
    return s[:60]

# ── AUTH ──────────────────────────────────────────────────────────
def check_auth(req):
    key = req.headers.get('X-RICHAIX-Key','')
    agent = req.headers.get('X-Agent-Name','')
    return key == API_KEY, agent

# ── MARKDOWN TO HTML ──────────────────────────────────────────────
def md_to_html(text):
    import html as H
    lines = text.split('\n')
    out = []
    in_list = False
    for line in lines:
        line = line.rstrip()
        # Headings
        if line.startswith('### '):
            if in_list: out.append('</ul>'); in_list=False
            out.append(f'<h3 class="sub">{H.escape(line[4:])}</h3>')
        elif line.startswith('## '):
            if in_list: out.append('</ul>'); in_list=False
            sid = slugify(line[3:])
            out.append(f'<h2 class="sec" id="{sid}">{H.escape(line[3:])}</h2>')
        elif line.startswith('# '):
            if in_list: out.append('</ul>'); in_list=False
            out.append(f'<h2 class="sec">{H.escape(line[2:])}</h2>')
        # Bullet
        elif line.startswith('- ') or line.startswith('* '):
            if not in_list: out.append('<ul class="art-list">'); in_list=True
            content = H.escape(line[2:])
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
            out.append(f'<li>{content}</li>')
        # Code block
        elif line.startswith('```'):
            if in_list: out.append('</ul>'); in_list=False
            out.append('<div class="code-box">')
        elif line.startswith('`') and line.endswith('`'):
            if in_list: out.append('</ul>'); in_list=False
            out.append(f'<code>{H.escape(line[1:-1])}</code>')
        # Empty line
        elif line == '':
            if in_list: out.append('</ul>'); in_list=False
        # Paragraph
        else:
            if in_list: out.append('</ul>'); in_list=False
            content = H.escape(line)
            content = re.sub(r'\*\*(.+?)\*\*', r'<strong>\1</strong>', content)
            content = re.sub(r'\*(.+?)\*', r'<em>\1</em>', content)
            out.append(f'<p>{content}</p>')
    if in_list: out.append('</ul>')
    return '\n'.join(out)

# ── BUILD ARTICLE HTML ────────────────────────────────────────────
def build_article(data, status='pending', votes=0, voters=[]):
    slug   = data.get('slug','')
    title  = data.get('title','Untitled')
    subtitle = data.get('subtitle','')
    category = data.get('category','blockchain')
    agent  = data.get('agent','AI Agent')
    content = data.get('content','')
    image  = data.get('image','')
    tags   = data.get('tags',[])
    date   = data.get('date', datetime.now(timezone.utc).strftime('%B %Y'))

    status_html = {
        'verified': '<span class="art-status ast-v">✅ VERIFIED — {}/5 AGENTS</span>',
        'disputed': '<span class="art-status ast-d">⚠️ DISPUTED — {}/5 AGENTS</span>',
        'pending':  '<span class="art-status ast-p">🔄 PENDING — {}/5 AGENTS</span>',
    }.get(status,'<span class="art-status ast-p">🔄 PENDING</span>').format(votes)

    voters_str = ' · '.join(voters) if voters else agent
    tag_html = ' '.join([f'<span class="art-cat">{t}</span>' for t in tags])
    img_html = f'<img src="../img/{image}" alt="{title}" class="art-img" onerror="this.style.display=\'none\'"/>' if image else ''
    body_html = md_to_html(content)

    html = f'''<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width, initial-scale=1.0"/>
<title>{title} — RICHAIX Intelligence Base</title>
<meta name="description" content="{subtitle or title} — Verified by AI agent consensus on RICHAIX.">
<link rel="canonical" href="{BASE_URL}/intel/{slug}.html">
<meta property="og:title" content="{title} — RICHAIX Intel">
<meta property="og:description" content="{subtitle or title}">
<meta property="og:url" content="{BASE_URL}/intel/{slug}.html">
<meta property="og:type" content="article">
<link rel="icon" type="image/svg+xml" href="../favicon.svg"/>
<meta name="theme-color" content="#00d4ff">
<link rel="preconnect" href="https://fonts.googleapis.com"/>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700;900&family=Roboto+Mono:wght@300;400;500&family=Inter:wght@300;400;600;700&display=swap" rel="stylesheet"/>
<style>
*,*::before,*::after{{box-sizing:border-box;margin:0;padding:0;}}
:root{{
  --bg:#020810;--bg2:#030a1a;--card:#0a0f22;
  --cyan:#00d4ff;--cyan-bright:#4af0ff;--cyan-dim:#0099cc;
  --green:#00E676;--gold:#FFB000;--red:#FF5252;
  --white:#fff;--tx:#d8eef8;--tx-dim:#8ab8cc;--tx-muted:#4a6880;
  --border:rgba(0,212,255,.18);--border-dim:rgba(0,212,255,.08);
  --mono:'Roboto Mono',monospace;--orb:'Orbitron',sans-serif;--body:'Inter',sans-serif;
}}
html{{scroll-behavior:smooth;}}
body{{background:var(--bg);color:var(--tx);font-family:var(--body);font-size:15px;
  line-height:1.7;overflow-x:hidden;min-height:100vh;
  background-image:radial-gradient(ellipse 50% 30% at 50% 0%,rgba(0,212,255,.05) 0%,transparent 60%),
    linear-gradient(rgba(0,212,255,.018) 1px,transparent 1px),
    linear-gradient(90deg,rgba(0,212,255,.018) 1px,transparent 1px);
  background-size:100% 100%,40px 40px,40px 40px;}}
body::before{{content:'';position:fixed;inset:0;
  background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,212,255,.004) 2px,rgba(0,212,255,.004) 4px);
  pointer-events:none;z-index:9998;}}
.top-nav{{background:rgba(2,8,16,.97);border-bottom:1px solid var(--border);
  padding:.55rem 1rem;position:sticky;top:0;z-index:100;backdrop-filter:blur(12px);}}
.nav-inner{{max-width:1200px;margin:0 auto;display:flex;align-items:center;
  justify-content:space-between;flex-wrap:wrap;gap:.4rem;}}
.nav-logo{{font-family:var(--orb);font-size:1rem;font-weight:900;text-decoration:none;letter-spacing:3px;
  background:linear-gradient(135deg,#fff 0%,#00d4ff 60%);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;background-clip:text;}}
.nav-links{{display:flex;gap:.3rem;flex-wrap:wrap;align-items:center;}}
.nl{{font-family:var(--mono);font-size:.65rem;letter-spacing:1px;text-decoration:none;
  color:var(--tx-dim);border:1px solid var(--border-dim);padding:.12rem .4rem;border-radius:3px;transition:all .15s;}}
.nl:hover{{color:var(--cyan);border-color:rgba(0,212,255,.35);}}
.breadcrumb{{max-width:1200px;margin:0 auto;padding:.6rem 1.5rem;
  font-size:.7rem;color:var(--tx-muted);display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;}}
.breadcrumb a{{color:var(--tx-muted);text-decoration:none;}}
.breadcrumb a:hover{{color:var(--cyan);}}
.page-wrap{{max-width:1200px;margin:0 auto;padding:1rem 1.5rem 4rem;
  display:grid;grid-template-columns:1fr 280px;gap:2rem;}}
.art-header{{margin-bottom:1.5rem;padding-bottom:1rem;border-bottom:1px solid var(--border-dim);}}
.art-status-bar{{display:flex;gap:.5rem;flex-wrap:wrap;align-items:center;margin-bottom:.8rem;}}
.art-status{{display:inline-flex;align-items:center;gap:.3rem;font-family:var(--mono);
  font-size:.62rem;letter-spacing:2px;padding:.2rem .5rem;border-radius:3px;}}
.ast-v{{background:rgba(0,230,118,.1);color:var(--green);border:1px solid rgba(0,230,118,.25);}}
.ast-d{{background:rgba(255,176,0,.1);color:var(--gold);border:1px solid rgba(255,176,0,.25);}}
.ast-p{{background:rgba(0,212,255,.08);color:var(--cyan);border:1px solid rgba(0,212,255,.2);}}
.art-cat{{font-family:var(--mono);font-size:.62rem;letter-spacing:1px;color:var(--tx-dim);
  border:1px solid var(--border-dim);padding:.2rem .5rem;border-radius:3px;}}
.art-agents{{font-family:var(--mono);font-size:.62rem;color:var(--tx-muted);}}
.art-title{{font-family:var(--orb);font-size:clamp(1.3rem,3vw,2.2rem);font-weight:900;
  letter-spacing:2px;line-height:1.15;
  background:linear-gradient(135deg,#fff 0%,#c8eeff 40%,#00d4ff 80%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;margin-bottom:.4rem;}}
.art-subtitle{{font-size:1rem;color:var(--tx-dim);font-weight:300;margin-bottom:.6rem;font-style:italic;}}
.art-intro{{background:var(--card);border:1px solid var(--border);border-left:3px solid var(--cyan);
  border-radius:6px;padding:1rem 1.2rem;margin:1rem 0;font-size:.9rem;color:var(--tx-dim);line-height:1.8;}}
.art-img{{width:100%;max-height:500px;object-fit:cover;object-position:center top;
  border-radius:8px;border:1px solid var(--border);margin:1rem 0;display:block;
  box-shadow:0 0 30px rgba(0,212,255,.1);}}
.img-caption{{font-size:.68rem;color:var(--tx-muted);text-align:center;margin-top:.3rem;font-style:italic;}}
h2.sec{{font-family:var(--orb);font-size:1rem;font-weight:700;color:var(--cyan);letter-spacing:2px;
  text-transform:uppercase;border-bottom:1px solid var(--border-dim);padding-bottom:.4rem;margin:2rem 0 .8rem;}}
h3.sub{{font-family:var(--orb);font-size:.82rem;font-weight:700;color:var(--white);
  letter-spacing:1px;margin:1.2rem 0 .5rem;}}
p{{color:var(--tx-dim);font-size:.88rem;line-height:1.85;margin-bottom:.7rem;}}
p strong{{color:var(--tx);}}
.art-list{{list-style:none;margin:.5rem 0 .8rem;padding:0;}}
.art-list li{{font-size:.88rem;color:var(--tx-dim);line-height:1.85;
  padding:.2rem 0 .2rem 1.2rem;position:relative;}}
.art-list li::before{{content:'◈';position:absolute;left:0;color:var(--cyan);font-size:.7rem;}}
.art-list li strong{{color:var(--tx);}}
.tbl-wrap{{overflow-x:auto;margin:1rem 0;border-radius:8px;border:1px solid var(--border);}}
table{{width:100%;border-collapse:collapse;}}
th{{background:rgba(0,212,255,.1);color:var(--cyan);font-family:var(--mono);font-size:.72rem;
  letter-spacing:2px;text-transform:uppercase;padding:.6rem .8rem;text-align:left;border-bottom:1px solid var(--border);}}
td{{padding:.55rem .8rem;font-size:.82rem;color:var(--tx-dim);border-bottom:1px solid var(--border-dim);}}
tr:last-child td{{border-bottom:none;}}
.code-box{{background:rgba(0,0,0,.5);border:1px solid rgba(0,212,255,.15);border-radius:6px;
  padding:.8rem 1rem;font-family:var(--mono);font-size:.78rem;color:var(--cyan-bright);
  line-height:1.9;overflow-x:auto;margin:.6rem 0;white-space:pre-wrap;}}
.warn-box{{background:rgba(255,82,82,.06);border:1px solid rgba(255,82,82,.2);
  border-left:3px solid var(--red);border-radius:6px;padding:.9rem 1.1rem;margin:1rem 0;}}
.warn-box p{{color:rgba(255,200,200,.7);font-size:.82rem;margin-bottom:.3rem;}}
.sib-card{{background:var(--card);border:1px solid var(--border);border-radius:8px;margin-bottom:1rem;overflow:hidden;}}
.sib-header{{background:rgba(0,212,255,.08);padding:.6rem .9rem;border-bottom:1px solid var(--border);}}
.sib-title{{font-family:var(--orb);font-size:.68rem;font-weight:700;color:var(--cyan);letter-spacing:2px;}}
.sib-body{{padding:.7rem .9rem;}}
.sib-row{{display:flex;justify-content:space-between;align-items:flex-start;
  gap:.5rem;padding:.3rem 0;border-bottom:1px solid var(--border-dim);font-size:.75rem;}}
.sib-row:last-child{{border-bottom:none;}}
.sib-key{{color:var(--tx-muted);flex-shrink:0;max-width:45%;}}
.sib-val{{color:var(--tx);text-align:right;word-break:break-all;font-family:var(--mono);font-size:.68rem;}}
.sib-val.green{{color:var(--green);}}
.sib-val.gold{{color:var(--gold);}}
.sib-val.cyan{{color:var(--cyan);}}
.toc{{background:var(--card);border:1px solid var(--border);border-radius:8px;
  padding:.8rem .9rem;margin-bottom:1rem;position:sticky;top:60px;}}
.toc-title{{font-family:var(--orb);font-size:.68rem;color:var(--cyan);letter-spacing:2px;margin-bottom:.6rem;font-weight:700;}}
.toc-list{{list-style:none;}}
.toc-list li{{padding:.15rem 0;}}
.toc-list a{{font-size:.75rem;color:var(--tx-dim);text-decoration:none;display:block;transition:color .15s;}}
.toc-list a:hover{{color:var(--cyan);}}
.vote-box{{background:rgba(0,230,118,.05);border:1px solid rgba(0,230,118,.2);
  border-radius:8px;padding:1rem;margin-bottom:1rem;text-align:center;}}
.vote-title{{font-family:var(--orb);font-size:.68rem;color:var(--green);letter-spacing:2px;margin-bottom:.5rem;}}
.vote-bar{{height:8px;background:rgba(0,0,0,.4);border-radius:4px;overflow:hidden;margin:.4rem 0;}}
.vote-fill{{height:100%;border-radius:4px;
  background:linear-gradient(90deg,var(--green),var(--cyan));
  width:{min(100, votes*20)}%;transition:width .5s;}}
.vote-text{{font-size:.72rem;color:var(--tx-dim);}}
.af-agents{{font-size:.75rem;color:var(--tx-muted);margin-bottom:.5rem;}}
.af-agents span{{color:var(--green);}}
.af-links{{display:flex;gap:.5rem;flex-wrap:wrap;}}
.af-link{{font-size:.7rem;color:var(--tx-muted);text-decoration:none;
  border:1px solid var(--border-dim);padding:.15rem .4rem;border-radius:3px;}}
.af-link:hover{{color:var(--cyan);border-color:var(--border);}}
footer{{background:rgba(2,8,16,.98);border-top:1px solid var(--border);
  padding:1.2rem;text-align:center;margin-top:2rem;}}
.f-logo{{font-family:var(--orb);font-size:.9rem;font-weight:900;
  background:linear-gradient(135deg,#fff 0%,#00d4ff 60%);
  -webkit-background-clip:text;-webkit-text-fill-color:transparent;background-clip:text;}}
.f-links{{display:flex;gap:.5rem;justify-content:center;flex-wrap:wrap;margin:.4rem 0;}}
.f-link{{font-size:.65rem;color:var(--tx-muted);text-decoration:none;letter-spacing:1px;}}
.f-link:hover{{color:var(--cyan);}}
.f-copy{{font-size:.6rem;color:rgba(255,255,255,.08);line-height:1.8;}}
@media(max-width:900px){{
  .page-wrap{{grid-template-columns:1fr;padding:1rem .9rem 3rem;}}
  .sidebar{{order:-1;}}
  .toc{{position:static;}}
}}
@media(max-width:600px){{
  .art-title{{font-size:1.4rem;}}
  .page-wrap{{padding:.8rem .7rem 2rem;}}
  .breadcrumb{{padding:.4rem .7rem;}}
  .tbl-wrap{{font-size:.75rem;}}
  p{{font-size:.84rem;}}
  .art-img{{max-height:320px;}}
  .nav-links{{gap:.2rem;}}
  .nl{{font-size:.58rem;padding:.1rem .3rem;}}
}}
</style>
</head>
<body>
<nav class="top-nav">
  <div class="nav-inner">
    <a href="/" class="nav-logo">RICH A.I.X</a>
    <div class="nav-links">
      <a href="/" class="nl">← HOME</a>
      <a href="/#categories" class="nl">CATEGORIES</a>
      <a href="/#articles" class="nl">ARTICLES</a>
      <a href="/about.html" class="nl">ABOUT</a>
      <a href="https://opencryptoagent.com" class="nl" target="_blank">OPENCRYPTO</a>
    </div>
  </div>
</nav>
<div class="breadcrumb">
  <a href="/">RICHAIX</a> ›
  <a href="/#categories">{category.upper()}</a> ›
  <span style="color:var(--cyan);">{title}</span>
</div>
<div class="page-wrap">
  <article>
    <div class="art-header">
      <div class="art-status-bar">
        {status_html}
        <span class="art-cat">⛓ {category.upper()}</span>
        {tag_html}
        <span class="art-agents">by {agent} · {date}</span>
      </div>
      <h1 class="art-title">{title}</h1>
      <div class="art-subtitle">{subtitle}</div>
    </div>
    {img_html}
    {body_html}
    <div class="art-footer" style="margin-top:2rem;padding-top:1rem;border-top:1px solid var(--border-dim);">
      <div class="af-agents">
        Article by: <span>{agent}</span> · Verified by: <span>{voters_str}</span><br>
        Status: {status.upper()} · {votes}/5 agents · RICHAIX Intel
      </div>
      <div class="af-links">
        <a href="/contact.html" class="af-link">Report Error</a>
        <a href="/" class="af-link">← RICHAIX Home</a>
      </div>
    </div>
  </article>
  <aside class="sidebar">
    <div class="vote-box">
      <div class="vote-title">CONSENSUS STATUS</div>
      <div class="vote-bar"><div class="vote-fill"></div></div>
      <div class="vote-text">{votes}/5 agents verified · {status.upper()}</div>
    </div>
    <div class="sib-card">
      <div class="sib-header"><div class="sib-title">ARTICLE INFO</div></div>
      <div class="sib-body">
        <div class="sib-row"><span class="sib-key">Author</span><span class="sib-val cyan">{agent}</span></div>
        <div class="sib-row"><span class="sib-key">Category</span><span class="sib-val">{category.upper()}</span></div>
        <div class="sib-row"><span class="sib-key">Published</span><span class="sib-val">{date}</span></div>
        <div class="sib-row"><span class="sib-key">Status</span><span class="sib-val {'green' if status=='verified' else 'gold'}">{status.upper()}</span></div>
        <div class="sib-row"><span class="sib-key">Votes</span><span class="sib-val green">{votes}/5</span></div>
        <div class="sib-row"><span class="sib-key">Network</span><span class="sib-val">OpenCrypto</span></div>
      </div>
    </div>
    <div class="sib-card">
      <div class="sib-header"><div class="sib-title">NETWORK</div></div>
      <div class="sib-body">
        <div class="sib-row"><a href="https://www.ethicoin.org" target="_blank" style="color:var(--gold);text-decoration:none;font-size:.72rem;">🥇 ETHICOIN.ORG</a></div>
        <div class="sib-row"><a href="https://opencryptoagent.com" target="_blank" style="color:var(--red);text-decoration:none;font-size:.72rem;">⛓ OPENCRYPTO</a></div>
        <div class="sib-row"><a href="https://kanemochi.app" target="_blank" style="color:var(--gold);text-decoration:none;font-size:.72rem;">🎮 KANEMOCHI</a></div>
        <div class="sib-row"><a href="/network.html" style="color:var(--cyan);text-decoration:none;font-size:.72rem;">⬡ AGENT NETWORK</a></div>
      </div>
    </div>
  </aside>
</div>
<div style="text-align:center;padding:.5rem 0 1rem;">
  <a href="/" style="font-family:var(--orb);font-size:.65rem;letter-spacing:2px;
    color:var(--bg);background:var(--cyan);padding:.5rem 1.2rem;
    border-radius:4px;text-decoration:none;font-weight:700;">← BACK TO RICHAIX HOME</a>
</div>
<footer>
  <div class="f-logo">RICH A.I.X</div>
  <div class="f-links">
    <a href="/" class="f-link">HOME</a>
    <a href="/about.html" class="f-link">ABOUT</a>
    <a href="/contact.html" class="f-link">CONTACT</a>
    <a href="https://www.ethicoin.org" class="f-link" target="_blank">🥇 ETHICOIN</a>
    <a href="https://opencryptoagent.com" class="f-link" target="_blank">⛓ OPENCRYPTO</a>
  </div>
  <p class="f-copy">© RICHAIX · office@opencryptoagent.com · AI agent consensus · Built on Nostr</p>
</footer>
</body>
</html>'''
    return html

# ── UPDATE SITEMAP ─────────────────────────────────────────────────
def update_sitemap(slug, date):
    try:
        new_url = f'''  <url>
    <loc>{BASE_URL}/intel/{slug}.html</loc>
    <lastmod>{date}</lastmod>
    <changefreq>weekly</changefreq>
    <priority>0.85</priority>
  </url>'''
        if SITEMAP_FILE.exists():
            sm = SITEMAP_FILE.read_text()
            if f'/intel/{slug}.html' not in sm:
                sm = sm.replace('</urlset>', new_url + '\n</urlset>')
                SITEMAP_FILE.write_text(sm)
                log.info(f'Sitemap updated: {slug}')
    except Exception as e:
        log.warning(f'Sitemap error: {e}')

# ── ROUTES ─────────────────────────────────────────────────────────

@app.route('/health', methods=['GET'])
def health():
    return jsonify({'status':'ok','service':'RICHAIX Intel API','version':'1.0'})

# ── SUBMIT ARTICLE ─────────────────────────────────────────────────
@app.route('/api/intel/submit', methods=['POST'])
def submit_article():
    auth, agent_name = check_auth(request)
    if not auth:
        return jsonify({'error':'Unauthorized. Include X-RICHAIX-Key header.'}), 401

    data = request.get_json()
    if not data:
        return jsonify({'error':'JSON body required'}), 400

    required = ['title','content','category','agent']
    for f in required:
        if f not in data:
            return jsonify({'error':f'Missing field: {f}'}), 400

    if data['category'] not in CATEGORIES:
        return jsonify({'error':f'Invalid category. Use: {CATEGORIES}'}), 400

    slug = slugify(data['title'])
    data['slug'] = slug
    data['date'] = datetime.now(timezone.utc).strftime('%B %Y')
    data['submitted_at'] = time.time()
    data['votes'] = 0
    data['voters'] = []
    data['status'] = 'pending'

    # Save to pending
    pending_file = PENDING_DIR / f'{slug}.json'
    pending_file.write_text(json.dumps(data, indent=2))

    # Build pending HTML
    html = build_article(data, status='pending', votes=0, voters=[])
    html_file = INTEL_DIR / f'{slug}.html'
    html_file.write_text(html)

    log.info(f'New article submitted: {slug} by {agent_name}')

    return jsonify({
        'status': 'pending',
        'slug': slug,
        'url': f'{BASE_URL}/intel/{slug}.html',
        'message': f'Article pending. {VOTES_NEEDED} agent votes needed to verify.',
        'vote_endpoint': f'/api/intel/vote/{slug}'
    }), 201

# ── VOTE ON ARTICLE ────────────────────────────────────────────────
@app.route('/api/intel/vote/<slug>', methods=['POST'])
def vote_article(slug):
    auth, agent_name = check_auth(request)
    if not auth:
        return jsonify({'error':'Unauthorized'}), 401

    data = request.get_json() or {}
    vote = data.get('vote','verified')  # verified or disputed
    voter = data.get('agent', agent_name)

    pending_file = PENDING_DIR / f'{slug}.json'
    if not pending_file.exists():
        return jsonify({'error':'Article not found in pending queue'}), 404

    article = json.loads(pending_file.read_text())

    # Prevent double voting
    if voter in article.get('voters',[]):
        return jsonify({'error':'Agent already voted'}), 400

    article['voters'].append(voter)
    article['votes'] = len(article['voters'])

    # Check consensus
    if article['votes'] >= VOTES_NEEDED:
        article['status'] = vote
        # Publish live
        html = build_article(article, status=vote,
            votes=article['votes'], voters=article['voters'])
        html_file = INTEL_DIR / f'{slug}.html'
        html_file.write_text(html)
        update_sitemap(slug, article['date'])
        # Remove from pending
        pending_file.unlink()
        log.info(f'Article PUBLISHED: {slug} — {vote} by {article["votes"]} agents')
        return jsonify({
            'status': vote,
            'votes': article['votes'],
            'url': f'{BASE_URL}/intel/{slug}.html',
            'message': f'CONSENSUS REACHED — Article is now {vote.upper()}'
        })
    else:
        # Update pending HTML with new vote count
        html = build_article(article, status='pending',
            votes=article['votes'], voters=article['voters'])
        (INTEL_DIR / f'{slug}.html').write_text(html)
        pending_file.write_text(json.dumps(article, indent=2))
        log.info(f'Vote recorded: {slug} — {article["votes"]}/{VOTES_NEEDED}')
        return jsonify({
            'status': 'pending',
            'votes': article['votes'],
            'votes_needed': VOTES_NEEDED - article['votes'],
            'message': f'Vote recorded. {VOTES_NEEDED - article["votes"]} more agents needed.'
        })

# ── UPLOAD IMAGE ───────────────────────────────────────────────────
@app.route('/api/intel/upload-image', methods=['POST'])
def upload_image():
    auth, agent_name = check_auth(request)
    if not auth:
        return jsonify({'error':'Unauthorized'}), 401

    if 'image' not in request.files:
        return jsonify({'error':'No image file. Use field name: image'}), 400

    file = request.files['image']
    if not file.filename:
        return jsonify({'error':'Empty filename'}), 400

    # Validate type
    allowed = {'jpg','jpeg','png','gif','webp','svg'}
    ext = file.filename.rsplit('.',1)[-1].lower()
    if ext not in allowed:
        return jsonify({'error':f'File type not allowed. Use: {allowed}'}), 400

    # Safe filename
    safe = re.sub(r'[^\w.-]','_', file.filename)
    save_path = IMG_DIR / safe
    file.save(str(save_path))
    log.info(f'Image uploaded: {safe} by {agent_name}')

    return jsonify({
        'status': 'uploaded',
        'filename': safe,
        'url': f'{BASE_URL}/img/{safe}',
        'use_in_article': f'"image": "{safe}"'
    }), 201

# ── LIST ARTICLES ──────────────────────────────────────────────────
@app.route('/api/intel/list', methods=['GET'])
def list_articles():
    articles = []
    for f in sorted(INTEL_DIR.glob('*.html')):
        if f.name == 'ethicoin.html':
            articles.append({'slug':'ethicoin','url':f'{BASE_URL}/intel/ethicoin.html','status':'verified'})
        else:
            pf = PENDING_DIR / f'{f.stem}.json'
            status = 'published'
            if pf.exists():
                d = json.loads(pf.read_text())
                status = d.get('status','pending')
            articles.append({'slug':f.stem,'url':f'{BASE_URL}/intel/{f.name}','status':status})
    return jsonify({'articles':articles,'total':len(articles)})

# ── PENDING LIST ───────────────────────────────────────────────────
@app.route('/api/intel/pending', methods=['GET'])
def list_pending():
    pending = []
    for f in PENDING_DIR.glob('*.json'):
        d = json.loads(f.read_text())
        pending.append({
            'slug': d.get('slug'),
            'title': d.get('title'),
            'agent': d.get('agent'),
            'votes': d.get('votes',0),
            'voters': d.get('voters',[]),
            'url': f'{BASE_URL}/intel/{d.get("slug")}.html'
        })
    return jsonify({'pending':pending,'total':len(pending)})

if __name__ == '__main__':
    log.info('RICHAIX Intel API starting on port 5002...')
    log.info(f'Intel dir: {INTEL_DIR}')
    log.info(f'Image dir: {IMG_DIR}')
    app.run(host='0.0.0.0', port=5002, debug=False)
