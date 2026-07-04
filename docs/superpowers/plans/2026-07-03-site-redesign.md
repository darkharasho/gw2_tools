# AxiTools Site Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the AxiTools marketing site (landing + privacy + terms) using the aegis site's layout system, spacing, and motion, rendered in AxiTools' neon palette with GW2 profession-color accents.

**Architecture:** Three self-contained static HTML files, each with one `<style>` block and (for the landing) one `<script>`. No bundler. The reference implementation for every layout pattern is the sibling file `../aegis/site/index.html` (read it — this plan adapts its structures and recolors them). Deployed via Cloudflare Workers (`npm run deploy:site`).

**Tech Stack:** Plain HTML/CSS/vanilla JS. Google Fonts (Orbitron, Chakra Petch, Inter, JetBrains Mono). Cloudflare Workers + wrangler for deploy.

## Global Constraints

- Files live in `site/`: `index.html`, `privacy.html`, `terms.html`. Overwrite in place.
- Each page is ONE self-contained HTML file: one `<style>` block, one optional `<script>`. No external CSS/JS files.
- Reference/source-of-truth for layout patterns: `../aegis/site/index.html` (relative to repo root, i.e. `/var/home/mstephens/Documents/GitHub/aegis/site/index.html`).
- Palette tokens (use verbatim): `--bg:#06070f`, `--ink:#eaf6ff`, `--muted:#8ea2c4`, `--cyan:#46e6ff`, `--mag:#c764ff`, `--teal:#39ffd0`. Signature gradient = cyan→magenta→teal.
- Profession accent tokens (already in current site, keep): `--p-guard:#0c8fd6`, `--p-war:#c7892b`, `--p-rev:#79236f`, `--p-eng:#b77c34`, `--p-ranger:#4b8e4b`, `--p-thief:#a02e2d`, `--p-ele:#f68a35`, `--p-mes:#b46dff`, `--p-necro:#3a9d23`.
- Fonts: `--display:'Chakra Petch'` for h1/h2/h3; `--font:'Inter'` body; `--mono:'JetBrains Mono'`; Orbitron ONLY for the wordmark and exactly one hero accent word.
- Card treatment: radius `14px`, buttons/pills `10–12px`, shadow `0 40px 90px rgba(0,0,0,.5), 0 0 0 1px rgba(255,255,255,.06)`.
- Invite/CTA URL (verbatim): `https://discord.com/oauth2/authorize?client_id=1433732142629912626&permissions=8&integration_type=0&scope=bot`
- Internal links are extensionless (`/privacy`, `/terms`) per commits 79a26e5 / 8b89eab.
- Every file ends with a trailing newline.
- All animation wrapped in a `@media (prefers-reduced-motion:reduce)` guard that shows revealed/animated content statically.
- Header/footer/ambient markup + the `:root` token block must be byte-identical across all three files (copy-paste, keep in sync).

## Verification model

There is no unit-test suite for static HTML. Each task's verification step renders the file and checks it visually:

- **Primary:** use the in-app renderer tool `mcp__sai__sai_render_html` — read the file's full contents and pass them as the HTML snippet. It returns a screenshot. (Load via ToolSearch `select:mcp__sai__sai_render_html` if deferred.) External Google Fonts may not load in the sandbox; that is acceptable — verify layout/structure/color, and confirm the `<link>` tag is present in source.
- **Secondary (optional):** open the file in a browser via the playwright/chrome-devtools MCP for a full-fidelity check with fonts.
- **Always:** grep the file for the required tokens/links the task added, and confirm no console errors when a script was added (check via the renderer/devtools).

---

### Task 1: Landing shell — tokens, fonts, ambient, header, footer

Build the empty landing skeleton everything else drops into. Start from a copy of aegis's shell and recolor.

**Files:**
- Modify (overwrite): `site/index.html`
- Reference: `../aegis/site/index.html` lines 18–256 (`:root`, reset, `.ambient`, header) and 217–221, 497–502 (footer).

**Interfaces:**
- Produces: the `:root` token block, `.ambient` element, `<header>` with `.hwrap/.brand/.navright/.navlink/.icobtn/.invite`, and `<footer>` with `.fwrap`. Later tasks insert sections between header and footer.

- [ ] **Step 1: Write `<head>` + fonts + token block**

Overwrite `site/index.html`. Start with:

```html
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AxiTools — Command your Guild Wars 2 guild from inside Discord</title>
<meta name="description" content="AxiTools is a multi-guild Guild Wars 2 Discord bot: builds, squad comps, WvW matchups, patch notes, streaming alerts and automatic guild-roles — themed in your professions' colors.">
<link rel="canonical" href="https://tools.axi.link/">
<meta property="og:title" content="AxiTools — Command your Guild Wars 2 guild from inside Discord">
<meta property="og:description" content="Multi-guild GW2 Discord bot: builds, squad comps, WvW matchups, patch notes, streaming alerts and auto guild-roles — themed in your professions' colors.">
<meta property="og:url" content="https://tools.axi.link/">
<link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@600;700;800&family=Chakra+Petch:wght@500;600;700&family=Inter:wght@400;500;600&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
  :root {
    --bg:#06070f; --ink:#eaf6ff; --muted:#8ea2c4;
    --cyan:#46e6ff; --mag:#c764ff; --teal:#39ffd0;
    --p-guard:#0c8fd6; --p-war:#c7892b; --p-rev:#79236f; --p-eng:#b77c34;
    --p-ranger:#4b8e4b; --p-thief:#a02e2d; --p-ele:#f68a35; --p-mes:#b46dff; --p-necro:#3a9d23;
    --font:'Inter',system-ui,sans-serif;
    --display:'Chakra Petch',var(--font);
    --wordmark:'Orbitron',var(--display);
    --mono:'JetBrains Mono',ui-monospace,monospace;
    --ease:cubic-bezier(0.22,1,0.36,1);
  }
  * { box-sizing:border-box; margin:0; }
  html { scroll-behavior:smooth; }
  body { background:var(--bg); color:var(--ink); font-family:var(--font); line-height:1.65; font-size:16px; overflow-x:hidden; }
  a { color:inherit; }
  ::selection { background:rgba(199,100,255,0.35); }
</style>
</head>
<body>
</body>
</html>
```

- [ ] **Step 2: Add the ambient background**

Add to the `<style>` block:

```css
  .ambient { position:fixed; inset:0; z-index:-5; pointer-events:none;
    background:
      radial-gradient(55% 40% at 82% 12%, rgba(70,230,255,0.12), transparent 65%),
      radial-gradient(50% 45% at 12% 45%, rgba(199,100,255,0.13), transparent 65%),
      radial-gradient(55% 45% at 85% 82%, rgba(57,255,208,0.10), transparent 65%);
    filter:blur(0.5px); }
```

And as the first element inside `<body>`:

```html
<div class="ambient"></div>
```

- [ ] **Step 3: Add the header**

Adapt aegis header CSS (lines 42–56), keeping class names. Add to `<style>`:

```css
  header { position:absolute; top:0; left:0; right:0; z-index:30; }
  header .hwrap { max-width:1360px; margin:0 auto; padding:0 34px; display:flex; align-items:center; justify-content:space-between; height:78px; }
  .brand { display:flex; align-items:center; gap:11px; text-decoration:none; }
  .brand .wm { font-family:var(--wordmark); font-weight:800; font-size:20px; letter-spacing:1px; color:#fff; }
  .navright { display:flex; align-items:center; gap:10px; }
  .icobtn { width:40px; height:40px; border-radius:12px; display:flex; align-items:center; justify-content:center; color:#fff;
    background:rgba(255,255,255,0.12); backdrop-filter:blur(8px); text-decoration:none; transition:background 0.25s, transform 0.25s var(--ease); }
  .icobtn:hover { background:rgba(255,255,255,0.22); transform:translateY(-1px); }
  .navlink { color:#fff; font-weight:600; font-size:13.5px; font-family:var(--display); text-decoration:none; padding:9px 14px; border-radius:12px; transition:background 0.25s; }
  .navlink:hover { background:rgba(255,255,255,0.14); }
  .navright .invite { display:flex; align-items:center; gap:8px; color:#06070f; background:#fff; padding:9px 18px; border-radius:12px;
    font-weight:600; font-size:13.5px; text-decoration:none; font-family:var(--display); transition:transform 0.25s var(--ease), box-shadow 0.3s; }
  .navright .invite:hover { transform:translateY(-2px); box-shadow:0 8px 30px rgba(70,230,255,0.35); }
```

Add markup right after `.ambient` (use the existing logo asset for the mark):

```html
<header>
  <div class="hwrap">
    <a class="brand" href="/" aria-label="AxiTools home">
      <img src="/assets/axitools-logo.svg" width="34" height="34" alt="">
      <span class="wm">AXITOOLS</span>
    </a>
    <nav class="navright">
      <a class="navlink" href="#commands">Commands</a>
      <a class="invite" href="https://discord.com/oauth2/authorize?client_id=1433732142629912626&permissions=8&integration_type=0&scope=bot">＋ Add to Discord</a>
    </nav>
  </div>
</header>
```

- [ ] **Step 4: Add the footer**

Add to `<style>`:

```css
  footer { padding:30px 0 38px; position:relative; z-index:2; }
  footer .fwrap { max-width:1360px; margin:0 auto; padding:0 34px; display:flex; justify-content:space-between; align-items:center; font-size:13px; color:var(--muted); flex-wrap:wrap; gap:12px; }
  footer a { color:var(--muted); text-decoration:none; transition:color 0.25s; }
  footer a:hover { color:#fff; }
```

Add markup as the last element before `</body>`:

```html
<footer>
  <div class="fwrap">
    <div>© 2026 AxiTools</div>
    <div><a href="#commands">Commands</a> &nbsp;·&nbsp; <a href="/privacy">Privacy</a> &nbsp;·&nbsp; <a href="/terms">Terms</a> &nbsp;·&nbsp; <a href="https://discord.com/oauth2/authorize?client_id=1433732142629912626&permissions=8&integration_type=0&scope=bot">Add to Discord</a></div>
  </div>
</footer>
```

- [ ] **Step 5: Verify render**

Read `site/index.html` and render it via `mcp__sai__sai_render_html`. Expected: dark page, faint neon ambient glow, floating header with AXITOOLS wordmark + invite pill top-right, footer at bottom. Confirm no layout breakage.

- [ ] **Step 6: Verify assets + tokens present**

Run: `ls site/assets/axitools-logo.svg site/assets/favicon.svg && grep -c "Chakra+Petch" site/index.html`
Expected: both files listed, grep prints `1`.

- [ ] **Step 7: Commit**

```bash
git add site/index.html
git commit -m "feat(site): scaffold redesigned landing shell (tokens, header, footer, ambient)"
```

---

### Task 2: Hero band + animated `/build` Discord mock

The centerpiece. Skewed cyan→magenta→teal gradient band, headline with one Orbitron accent word, two CTAs, and an animated Discord frame that plays a `/build guardian` → themed embed exchange.

**Files:**
- Modify: `site/index.html` (insert hero after `</header>`; add `<script>` before `</body>`)
- Reference: aegis hero CSS lines 58–133, hero markup 273–332, playback script 543–556.

**Interfaces:**
- Consumes: header/footer shell from Task 1.
- Produces: `.heroband`, `.hero`, `.appwrap`/`.app` Discord mock with `#feed .msg` nodes + `#typing`, and a `play()` loop in the page `<script>`. Later tasks reuse the `.dembed` embed-card styles and the `<script>` IIFE.

- [ ] **Step 1: Add hero band + stripe CSS**

Adapt aegis lines 58–86, recoloring the band gradient to the AxiTools trio. Add to `<style>`:

```css
  .heroband { position:relative; overflow:hidden; padding:170px 0 90px; }
  .heroband .bgband { position:absolute; inset:-24% -10% 10% -10%; z-index:-3; transform:skewY(-7deg); transform-origin:top left;
    background:linear-gradient(115deg,#0b3a63 0%,#0e6fa3 20%,#46e6ff 40%,#c764ff 66%,#39ffd0 100%); opacity:0.9; }
  .heroband .bgband::after { content:""; position:absolute; inset:0;
    background:radial-gradient(60% 80% at 68% 25%, rgba(199,100,255,0.4), transparent 60%),
               radial-gradient(50% 70% at 10% 80%, rgba(57,255,208,0.35), transparent 60%); }
  .stripes { position:absolute; inset:0; z-index:-2; transform:skewY(-7deg); transform-origin:top left; pointer-events:none; }
  .stripes .s { position:absolute; height:130px; opacity:0.45; animation:stripeIn 1.4s var(--ease) both; }
  .stripes .s1 { width:56%; left:0; top:2%; background:linear-gradient(90deg,#46e6ff,rgba(70,230,255,0)); animation-delay:0.2s; }
  .stripes .s2 { width:36%; left:0; top:20%; background:linear-gradient(90deg,#39ffd0,rgba(57,255,208,0)); animation-delay:0.35s; height:95px; }
  .stripes .s3 { width:46%; right:0; top:52%; background:linear-gradient(270deg,#c764ff,rgba(199,100,255,0)); animation-delay:0.5s; }
  .stripes .s4 { width:30%; right:0; top:72%; background:linear-gradient(270deg,#46e6ff,rgba(70,230,255,0)); animation-delay:0.65s; height:85px; opacity:0.3; }
  @keyframes stripeIn { from { transform:translateX(var(--from,-100%)); opacity:0; } }
  .stripes .s3, .stripes .s4 { --from:100%; }

  .hero { max-width:1360px; margin:0 auto; padding:0 34px; display:grid; grid-template-columns:0.95fr 1.05fr; gap:40px; align-items:center; }
  .hero h1 { font-family:var(--display); font-size:clamp(38px,4.6vw,60px); line-height:1.12; font-weight:700; letter-spacing:-0.5px;
    text-shadow:0 4px 34px rgba(0,0,0,0.35); margin-bottom:16px; }
  .hero h1 .accent { font-family:var(--wordmark); font-weight:800; letter-spacing:1px;
    background:linear-gradient(90deg,#fff,#eaf6ff); -webkit-background-clip:text; background-clip:text; color:transparent; }
  .hero .hsub { color:rgba(255,255,255,0.9); font-size:17px; max-width:52ch; margin-bottom:30px; }
  .hero .ctas { display:flex; gap:14px; flex-wrap:wrap; }
  .pill { display:inline-flex; align-items:center; gap:9px; padding:13px 26px; border-radius:10px; font-weight:600; font-size:13px;
    font-family:var(--display); letter-spacing:0.8px; text-transform:uppercase; text-decoration:none;
    background:#fff; color:#06070f; box-shadow:0 8px 30px rgba(0,0,0,0.3); transition:transform 0.25s var(--ease), box-shadow 0.3s; }
  .pill:hover { transform:translateY(-2px); box-shadow:0 14px 40px rgba(0,0,0,0.4); }
  .pill.alt { background:rgba(255,255,255,0.14); color:#fff; backdrop-filter:blur(8px); }
  .hero h1, .hero .hsub, .hero .ctas { opacity:0; transform:translateY(22px); animation:rise 0.9s var(--ease) forwards; }
  .hero .hsub { animation-delay:0.1s; } .hero .ctas { animation-delay:0.18s; }
  @keyframes rise { to { opacity:1; transform:none; } }
```

- [ ] **Step 2: Add Discord mock CSS (frame + embed)**

Adapt aegis lines 88–133, recoloring the bot avatar and embed accent to AxiTools/profession colors. Add to `<style>`:

```css
  .appwrap { position:relative; opacity:0; transform:translateY(34px); animation:rise 1.1s var(--ease) 0.3s forwards; }
  .app { border-radius:12px; overflow:hidden; display:grid; grid-template-columns:200px 1fr;
    background:#313338; color:#dbdee1; font-size:13px; height:470px;
    box-shadow:0 50px 110px rgba(0,0,0,0.55), 0 0 0 1px rgba(255,255,255,0.09); }
  .side { background:#2b2d31; display:flex; flex-direction:column; }
  .side .sname { padding:14px; font-weight:700; color:#f2f3f5; font-size:14px; border-bottom:1px solid #1e1f22; }
  .chgroup { padding:14px 8px 4px 14px; font-size:10.5px; font-weight:700; letter-spacing:0.4px; color:#80848e; text-transform:uppercase; }
  .ch { margin:1px 8px; padding:5px 8px; border-radius:6px; color:#80848e; font-weight:500; display:flex; gap:7px; align-items:center; }
  .ch.active { background:#3f4248; color:#f2f3f5; }
  .ch .h { font-size:15px; font-weight:400; color:#80848e; }
  .chat { display:flex; flex-direction:column; min-width:0; }
  .chat .top { padding:11px 16px; display:flex; align-items:center; gap:8px; font-weight:600; color:#f2f3f5; border-bottom:1px solid #26272b; }
  .chat .top .h { color:#80848e; font-size:18px; font-weight:400; }
  .feed { flex:1; overflow:hidden; padding:14px 16px 6px; display:flex; flex-direction:column; gap:14px; justify-content:flex-end; }
  .msg { display:flex; gap:12px; opacity:0; transform:translateY(10px); transition:opacity 0.45s var(--ease), transform 0.45s var(--ease); }
  .msg.show { opacity:1; transform:none; }
  .av { width:36px; height:36px; border-radius:50%; flex:none; display:flex; align-items:center; justify-content:center; font-weight:700; font-size:14px; color:#fff; overflow:hidden; }
  .av.u1 { background:linear-gradient(135deg,#46e6ff,#4a90ff); }
  .av.bot { background:#06070f; }
  .av.bot img { width:24px; height:24px; }
  .mhead { display:flex; align-items:baseline; gap:8px; }
  .mname { font-weight:600; color:#f2f3f5; font-size:13px; }
  .mtag { font-size:9.5px; font-weight:700; padding:1px 5px; border-radius:4px; background:#46e6ff; color:#06070f; }
  .mtime { color:#80848e; font-size:11px; }
  .mtext { color:#dbdee1; font-size:13px; }
  .mtext .cmd { background:rgba(70,230,255,0.14); color:#8fe9ff; border-radius:4px; padding:1px 6px; font-family:var(--mono); font-size:12px; }
  .dembed { border-left:4px solid var(--p-guard); background:#2b2d31; border-radius:6px; padding:12px 14px; margin-top:7px; max-width:440px; }
  .dembed .dt { color:#7fd6ff; font-weight:700; font-size:13px; margin-bottom:6px; }
  .dembed .dd { font-family:var(--mono); font-size:11px; color:#b5bac1; line-height:1.8; }
  .dembed .dd b { color:#dbdee1; font-weight:600; }
  .dembed .df { margin-top:8px; font-size:10.5px; color:#80848e; font-style:italic; }
  .typing { display:flex; align-items:center; gap:8px; color:#80848e; font-size:12px; padding:0 16px 8px 64px; height:20px; }
  .typing .dots i { display:inline-block; width:5px; height:5px; border-radius:50%; background:#80848e; margin-right:3px; animation:blink 1.2s infinite; }
  .typing .dots i:nth-child(2){ animation-delay:0.2s; } .typing .dots i:nth-child(3){ animation-delay:0.4s; }
  @keyframes blink { 0%,60%,100% { opacity:0.25; } 30% { opacity:1; } }
  .inputbar { margin:0 14px 16px; background:#383a40; border-radius:8px; padding:10px 14px; color:#6d6f78; font-size:13px; }
```

- [ ] **Step 3: Add hero markup**

Insert directly after `</header>`. Copy is drawn from the current site's real `/build` embed (Power Dragonhunter):

```html
<div class="heroband">
  <div class="bgband"></div>
  <div class="stripes"><div class="s s1"></div><div class="s s2"></div><div class="s s3"></div><div class="s s4"></div></div>
  <div class="hero">
    <div>
      <h1>Project your guild into the <span class="accent">Mists.</span></h1>
      <p class="hsub">One multi-guild Guild Wars 2 Discord bot — builds, squad comps, WvW matchups, patch notes, streaming alerts and automatic guild-roles, all lit up in your professions' colors.</p>
      <div class="ctas">
        <a class="pill" href="https://discord.com/oauth2/authorize?client_id=1433732142629912626&permissions=8&integration_type=0&scope=bot">＋ Add to Discord</a>
        <a class="pill alt" href="#features">→ See what it does</a>
      </div>
    </div>
    <div class="appwrap">
      <div class="app" aria-label="Discord showing AxiTools posting a themed build">
        <div class="side">
          <div class="sname">Mists Vanguard [MV]</div>
          <div class="chgroup">Text channels</div>
          <div class="ch"><span class="h">#</span>general</div>
          <div class="ch active"><span class="h">#</span>builds</div>
          <div class="ch"><span class="h">#</span>wvw</div>
          <div class="ch"><span class="h">#</span>comps</div>
        </div>
        <div class="chat">
          <div class="top"><span class="h">#</span>builds</div>
          <div class="feed" id="feed">
            <div class="msg">
              <div class="av u1">A</div>
              <div><div class="mhead"><span class="mname">Aria</span><span class="mtime">Today at 7:42 PM</span></div>
              <div class="mtext"><span class="cmd">/build guardian dragonhunter</span></div></div>
            </div>
            <div class="msg">
              <div class="av bot"><img src="/assets/axitools-logo.svg" alt=""></div>
              <div>
                <div class="mhead"><span class="mname">AxiTools</span> <span class="mtag">✓ APP</span><span class="mtime">Today at 7:42 PM</span></div>
                <div class="dembed">
                  <div class="dt">Power Dragonhunter — Roaming</div>
                  <div class="dd">
                    <b>Profession</b> Dragonhunter (Guardian)<br>
                    <b>Chat code</b> [&amp;DQEKAwAAAA]<br>
                    <b>Description</b> High-mobility power DPS for WvW roaming. Strong burst, great disengage.
                  </div>
                  <div class="df">Updated by @Aria · Jun 9, 2026 — themed to your professions</div>
                </div>
              </div>
            </div>
          </div>
          <div class="typing" id="typing" style="display:none"><span class="dots"><i></i><i></i><i></i></span> AxiTools is thinking…</div>
          <div class="inputbar">Message #builds</div>
        </div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Add the playback script**

Add before `</body>`. Adapt aegis lines 504–556 (reveal observer stub included now; feature-row observer wired in Task 4):

```html
<script>
(() => {
  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;

  const msgs = [...document.querySelectorAll('#feed .msg')];
  const typing = document.getElementById('typing');
  function play() {
    msgs.forEach((m) => m.classList.remove('show'));
    if (typing) typing.style.display = 'none';
    if (reduced) { msgs.forEach((m) => m.classList.add('show')); return; }
    setTimeout(() => msgs[0].classList.add('show'), 700);
    setTimeout(() => { if (typing) typing.style.display = 'flex'; }, 1600);
    setTimeout(() => { if (typing) typing.style.display = 'none'; msgs[1].classList.add('show'); }, 3200);
    setTimeout(play, 9000);
  }
  if (msgs.length) play();
})();
</script>
```

- [ ] **Step 5: Add hero responsive + reduced-motion rules**

Add to `<style>`:

```css
  @media (max-width:960px) {
    .hero { grid-template-columns:1fr; }
    .side { display:none; }
    .app { grid-template-columns:1fr; height:440px; }
  }
  @media (prefers-reduced-motion:reduce) {
    *, *::before, *::after { animation-duration:0.01ms !important; animation-iteration-count:1 !important; transition-duration:0.01ms !important; }
    .hero h1, .hero .hsub, .hero .ctas, .appwrap { opacity:1 !important; transform:none !important; }
    .msg { opacity:1 !important; transform:none !important; }
  }
```

- [ ] **Step 6: Verify render**

Read `site/index.html`, render via `mcp__sai__sai_render_html`. Expected: skewed neon gradient band behind hero; left headline with "Mists." in Orbitron; two CTA pills; right = Discord frame showing Aria's `/build` command and the AxiTools Guardian-blue embed. Check the browser console (via renderer/devtools) shows no JS errors.

- [ ] **Step 7: Commit**

```bash
git add site/index.html
git commit -m "feat(site): add hero band with animated /build Discord mock"
```

---

### Task 3: Stats strip

Glassy band under the hero, 4 static capability items + a note pill.

**Files:**
- Modify: `site/index.html` (insert after `.heroband`)
- Reference: aegis CSS lines 135–147, markup 334–342.

**Interfaces:**
- Consumes: hero from Task 2.
- Produces: `.statstrip` band. No JS.

- [ ] **Step 1: Add stats-strip CSS**

```css
  .statstrip { background:rgba(6,7,15,0.85); backdrop-filter:blur(10px); border-top:1px solid rgba(255,255,255,0.05); border-bottom:1px solid rgba(255,255,255,0.05); position:relative; z-index:3; }
  .statstrip .swrap { max-width:1360px; margin:0 auto; padding:22px 34px; display:flex; align-items:center; justify-content:space-between; gap:24px; flex-wrap:wrap; }
  .sitem .lbl { font-size:14px; color:var(--muted); font-weight:500; display:block; line-height:1.3; }
  .sitem .val { font-family:var(--display); font-weight:700; font-size:20px; color:#fff; }
  .statstrip .note { font-size:13px; color:#fff; background:rgba(255,255,255,0.07); border:1px solid rgba(255,255,255,0.1); padding:9px 14px; border-radius:12px; white-space:nowrap; }
```

- [ ] **Step 2: Add markup after the `.heroband` closing `</div>`**

```html
<div class="statstrip">
  <div class="swrap">
    <div class="sitem"><span class="val">9 professions</span><span class="lbl">individually themed</span></div>
    <div class="sitem"><span class="val">Multi-guild</span><span class="lbl">every server fully isolated</span></div>
    <div class="sitem"><span class="val">Encrypted</span><span class="lbl">API-key vault, at rest</span></div>
    <div class="sitem"><span class="val">One command</span><span class="lbl">setup, role-gated</span></div>
    <div class="note">Your guild's colors, everywhere.</div>
  </div>
</div>
```

- [ ] **Step 3: Verify render**

Render `site/index.html`. Expected: dark glassy strip directly below the hero with four label/value pairs and the note pill on the right.

- [ ] **Step 4: Commit**

```bash
git add site/index.html
git commit -m "feat(site): add static capability stats strip"
```

---

### Task 4: Four feature rows + reveal-on-scroll

Alternating split rows, each with a headline, paragraph, profession-tinted tag row, and a mock card with a faint doodle behind it. Wire the `.rv` scroll-reveal observer.

**Files:**
- Modify: `site/index.html` (insert after `.statstrip`; extend `<script>`)
- Reference: aegis CSS lines 149–179, 222–223; feature markup 344–460; reveal observer 508–511.

**Interfaces:**
- Consumes: `.dembed` styles (Task 2), stats strip (Task 3).
- Produces: `#features` anchor, `.feature`/`.feature.flip` rows, `.ftext/.tags/.tag`, `.fmock/.mockcard/.pin/.doodle`, and `.rv`→`.inview` observer in the script.

- [ ] **Step 1: Add feature-row + reveal CSS**

Adapt aegis 149–179 + 222–223, recolored. Add to `<style>`:

```css
  .feature { max-width:1360px; margin:0 auto; padding:150px 34px 0; display:grid; grid-template-columns:1fr 1fr; gap:80px; align-items:center; }
  .feature.flip .ftext { order:2; } .feature.flip .fmock { order:1; }
  .ftext .eyebrow { font-family:var(--mono); font-size:12px; color:var(--cyan); letter-spacing:1px; margin-bottom:12px; }
  .ftext h2 { font-family:var(--display); font-size:clamp(30px,4vw,50px); font-weight:700; letter-spacing:-0.5px; line-height:1.1; margin-bottom:18px; }
  .ftext p { color:var(--muted); font-size:16px; max-width:48ch; margin-bottom:22px; }
  .ftext p b { color:var(--ink); }
  .tags { display:flex; flex-wrap:wrap; gap:10px; max-width:440px; }
  .tag { font-size:13px; font-weight:600; padding:6px 16px; border-radius:99px; color:#06070f; background:var(--tagc,#7dd3fc); }
  .fmock { position:relative; }
  .fmock .doodle { position:absolute; z-index:-1; opacity:0.12; }
  .mockcard { background:#16181d; border-radius:14px; padding:22px 24px; max-width:520px; margin:0 auto;
    box-shadow:0 40px 90px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.06); position:relative; }
  .mockcard .row { display:flex; gap:12px; margin-bottom:14px; }
  .mockcard .row:last-child { margin-bottom:0; }
  .mockcard .dembed { max-width:none; }
  .pin { position:absolute; top:-24px; right:-14px; width:52px; height:52px; border-radius:50% 50% 50% 4px;
    display:flex; align-items:center; justify-content:center; color:#fff; box-shadow:0 12px 30px rgba(0,0,0,0.4); }
  .pin img { width:28px; height:28px; }
  .rv { opacity:0; transform:translateY(30px); transition:opacity 0.9s var(--ease), transform 0.9s var(--ease); }
  .rv.inview { opacity:1; transform:translateY(0); }
  .rv.d1 { transition-delay:0.12s; }
  @media (max-width:960px) {
    .feature, .feature.flip { grid-template-columns:1fr; gap:44px; padding-top:100px; }
    .feature.flip .ftext { order:1; } .feature.flip .fmock { order:2; }
  }
```

- [ ] **Step 2: Add feature rows 1 & 2 markup**

Insert after `.statstrip`. Row 1 (Builds) reuses the hero embed styling; profession icons come from `/assets/prof/`:

```html
<div class="feature" id="features">
  <div class="ftext rv">
    <div class="eyebrow">/builds add · edit · delete</div>
    <h2>Builds, themed to the profession</h2>
    <p>Every build stores its <b>profession or elite spec</b>, build-site link, chat code, description and an audit trail of who changed it last. Posts <b>adopt the profession's color and icon</b> automatically — and when the target channel is a forum, AxiTools creates or updates the thread.</p>
    <div class="tags" style="--tagc:#7fd6ff">
      <span class="tag">Profession color + icon</span>
      <span class="tag">Copyable chat code</span>
      <span class="tag">Forum threads</span>
      <span class="tag">Audit metadata</span>
    </div>
  </div>
  <div class="fmock rv d1">
    <svg class="doodle" width="220" height="220" viewBox="0 0 200 200" style="top:-60px; left:-40px" fill="none" stroke="#46e6ff" stroke-width="2"><circle cx="100" cy="100" r="60"/><circle cx="100" cy="100" r="90"/></svg>
    <div class="mockcard">
      <div class="pin" style="background:var(--p-guard)"><img src="/assets/prof/Dragonhunter.png" alt=""></div>
      <div class="row">
        <div class="av bot"><img src="/assets/axitools-logo.svg" alt=""></div>
        <div style="flex:1"><div class="mhead"><span class="mname" style="color:#7fd6ff">AxiTools</span><span class="mtag">✓ APP</span><span class="mtime">Today at 7:42 PM</span></div>
        <div class="dembed">
          <div class="dt">Power Dragonhunter — Roaming</div>
          <div class="dd"><b>Profession</b> Dragonhunter (Guardian)<br><b>Chat code</b> [&amp;DQEKAwAAAA]<br><b>Description</b> High-mobility power DPS for WvW roaming.</div>
          <div class="df">Updated by @Aria · Jun 9, 2026</div>
        </div></div>
      </div>
    </div>
  </div>
</div>

<div class="feature flip">
  <div class="ftext rv">
    <div class="eyebrow">/comp schedule</div>
    <h2>Squad comps that fill themselves</h2>
    <p>Schedule recurring squad signups with <b>live dropdown rosters</b> and <b>per-profession headcounts</b>. Members pick their role; the embed updates in place so you always know what the squad still needs.</p>
    <div class="tags" style="--tagc:#c4b5fd">
      <span class="tag">Recurring signups</span>
      <span class="tag">Live rosters</span>
      <span class="tag">Per-profession counts</span>
      <span class="tag">Role dropdowns</span>
    </div>
  </div>
  <div class="fmock rv d1">
    <svg class="doodle" width="240" height="200" viewBox="0 0 240 200" style="bottom:-50px; right:-30px" fill="none" stroke="#c764ff" stroke-width="2"><path d="M20 180 Q 120 20 220 120"/><circle cx="220" cy="120" r="14"/></svg>
    <div class="mockcard">
      <div class="pin" style="background:var(--p-mes)"><img src="/assets/prof/Chronomancer.png" alt=""></div>
      <div class="row">
        <div class="av bot"><img src="/assets/axitools-logo.svg" alt=""></div>
        <div style="flex:1"><div class="mhead"><span class="mname" style="color:#c4b5fd">AxiTools</span><span class="mtag" style="background:#c764ff">✓ APP</span><span class="mtime">Fri at 8:00 PM</span></div>
        <div class="dembed" style="border-left-color:var(--p-mes)">
          <div class="dt" style="color:#d6b8ff">WvW Raid — Friday Reset</div>
          <div class="dd"><b>Firebrand</b> 3/4 · <b>Scourge</b> 4/5 · <b>Chrono</b> 2/2<br><b>Spellbreaker</b> 1/3 · <b>Scrapper</b> 2/2<br><b>Signed up</b> 34 / 50</div>
          <div class="df">Pick your role from the dropdown below ↓</div>
        </div></div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 3: Add feature rows 3 & 4 markup**

Append after row 2:

```html
<div class="feature">
  <div class="ftext rv">
    <div class="eyebrow">/wvw matchup · reset</div>
    <h2>Own the WvW week</h2>
    <p>Track your guild's <b>matchup and rosters</b>, get <b>pinged when your server relinks</b>, and always know exactly when the next <b>weekly reset</b> hits — posted straight to the channel your commanders watch.</p>
    <div class="tags" style="--tagc:#f9a8d4">
      <span class="tag">Live matchup</span>
      <span class="tag">Relink alerts</span>
      <span class="tag">Reset countdown</span>
      <span class="tag">Server rosters</span>
    </div>
  </div>
  <div class="fmock rv d1">
    <svg class="doodle" width="200" height="200" viewBox="0 0 200 200" style="top:-40px; right:-50px" fill="none" stroke="#39ffd0" stroke-width="2"><path d="M100 20 L160 40 V90 C160 130 135 155 100 170 C65 155 40 130 40 90 V40 Z"/></svg>
    <div class="mockcard">
      <div class="pin" style="background:#0e6fa3"><img src="/assets/axitools-logo.svg" alt="" style="filter:brightness(0) invert(1)"></div>
      <div class="row">
        <div class="av bot"><img src="/assets/axitools-logo.svg" alt=""></div>
        <div style="flex:1"><div class="mhead"><span class="mname" style="color:#7fd6ff">AxiTools</span><span class="mtag">✓ APP</span><span class="mtime">Today at 6:00 PM</span></div>
        <div class="dembed" style="border-left-color:#39ffd0">
          <div class="dt" style="color:#8ff4dd">This Week's Matchup — NA Tier 2</div>
          <div class="dd"><b>1st</b> Blackgate — 214k<br><b>2nd</b> Your Link — 198k<br><b>3rd</b> Maguuma — 176k</div>
          <div class="df">Next reset in 2d 4h · relink alert armed</div>
        </div></div>
      </div>
    </div>
  </div>
</div>

<div class="feature flip">
  <div class="ftext rv">
    <div class="eyebrow">set it and forget it</div>
    <h2>Runs itself</h2>
    <p>AxiTools posts on its own: <b>GW2 patch notes and any RSS/Atom feed</b> as they drop, <b>go-live alerts</b> for your guild's Twitch and YouTube creators, <b>ArcDPS update</b> pings, and <b>automatic Discord roles</b> synced from real in-game guild membership.</p>
    <div class="tags" style="--tagc:#86efac">
      <span class="tag">Patch notes &amp; RSS</span>
      <span class="tag">Twitch / YouTube go-live</span>
      <span class="tag">ArcDPS updates</span>
      <span class="tag">Auto guild-roles</span>
    </div>
  </div>
  <div class="fmock rv d1">
    <svg class="doodle" width="220" height="180" viewBox="0 0 220 180" style="bottom:-40px; left:-50px" fill="none" stroke="#39ffd0" stroke-width="2"><path d="M20 90 Q 60 30 110 60 T 200 70"/><circle cx="20" cy="90" r="10"/></svg>
    <div class="mockcard">
      <div class="pin" style="background:var(--p-necro)"><img src="/assets/prof/Reaper.png" alt=""></div>
      <div class="row">
        <div class="av bot"><img src="/assets/axitools-logo.svg" alt=""></div>
        <div style="flex:1"><div class="mhead"><span class="mname" style="color:#8ff4dd">AxiTools</span><span class="mtag" style="background:#39ffd0">✓ APP</span><span class="mtime">Today at 9:15 AM</span></div>
        <div class="dembed" style="border-left-color:#9146ff">
          <div class="dt" style="color:#c9a6ff">🔴 Aria is live on Twitch</div>
          <div class="dd"><b>WvW roaming — reset prep</b><br>twitch.tv/aria</div>
          <div class="df">Auto-posted · role @Streamer pinged</div>
        </div></div>
      </div>
    </div>
  </div>
</div>
```

- [ ] **Step 4: Wire the reveal observer**

In the page `<script>`, add before the closing `})();`:

```javascript
  const io = new IntersectionObserver((entries) => {
    for (const e of entries) if (e.isIntersecting) { e.target.classList.add('inview'); io.unobserve(e.target); }
  }, { threshold:0.15, rootMargin:'0px 0px -40px 0px' });
  document.querySelectorAll('.rv').forEach((el) => io.observe(el));
```

Also add to the reduced-motion media query: `.rv { opacity:1 !important; transform:none !important; }`

- [ ] **Step 5: Verify render + profession icons**

Run: `ls site/assets/prof/Dragonhunter.png site/assets/prof/Chronomancer.png site/assets/prof/Reaper.png`
Expected: all three listed.
Then render `site/index.html`. Expected: four alternating feature rows with headline/tags on one side and a mock card (with a colored profession pin badge + faint doodle) on the other. Scroll-reveal may show everything immediately in a static screenshot — that's fine; confirm layout and colors.

- [ ] **Step 6: Commit**

```bash
git add site/index.html
git commit -m "feat(site): add four feature rows with mock cards and scroll reveal"
```

---

### Task 5: "Everything else" grid + setup command + closing band

Compact 3-card grid for the smaller systems, a one-line setup command block, and the closing gradient CTA band.

**Files:**
- Modify: `site/index.html` (insert after last feature row, before footer)
- Reference: aegis setup CSS 181–190 + markup 462–469; privacy grid CSS 192–205 (reused for the grid); closeband CSS 207–215 + markup 490–495.

**Interfaces:**
- Consumes: `.rv` reveal (Task 4), `.pill` (Task 2).
- Produces: `#commands` anchor, `.grid3`/`.gcard`, `.cmd`, `.closeband`.

- [ ] **Step 1: Add CSS**

```css
  .gridsection { max-width:1200px; margin:0 auto; padding:150px 34px 0; }
  .gridsection h2 { font-family:var(--display); font-size:clamp(28px,3.4vw,42px); font-weight:700; letter-spacing:-0.5px; text-align:center; margin-bottom:8px; }
  .gridsection .lead { color:var(--muted); text-align:center; max-width:60ch; margin:0 auto 44px; }
  .grid3 { display:grid; grid-template-columns:repeat(3,1fr); gap:18px; }
  .gcard { background:#16181d; border-radius:14px; padding:22px 24px; box-shadow:0 20px 50px rgba(0,0,0,0.35), 0 0 0 1px rgba(255,255,255,0.06); }
  .gcard h3 { font-family:var(--display); font-size:17px; font-weight:600; margin-bottom:8px; }
  .gcard p { color:#c3c9dd; font-size:14.5px; }
  @media (max-width:860px) { .grid3 { grid-template-columns:1fr; } }

  .setuplead { text-align:center; max-width:720px; margin:0 auto; padding:150px 28px 0; }
  .setuplead h2 { font-family:var(--display); font-size:clamp(28px,3.4vw,42px); font-weight:700; letter-spacing:-0.5px; margin-bottom:14px; }
  .setuplead p { color:var(--muted); }
  .cmd { margin:36px auto 0; text-align:center; }
  .cmd .line { font-family:var(--mono); font-size:15px; background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.1); border-radius:14px; padding:18px 26px; display:inline-flex; align-items:center; gap:14px; }
  .cmd .line .prompt { color:var(--cyan); }
  .cmd .line .okt { color:var(--teal); }
  .cmd small { display:block; margin-top:14px; color:var(--muted); font-size:13px; }

  .closeband { position:relative; overflow:hidden; margin-top:150px; padding:150px 0 170px; text-align:center; }
  .closeband .bgband2 { position:absolute; inset:14% -10% -24% -10%; z-index:-2; transform:skewY(-7deg); transform-origin:bottom right;
    background:linear-gradient(115deg,#0e6fa3 0%,#46e6ff 30%,#c764ff 66%,#39ffd0 100%); opacity:0.9; }
  .closeband .bgband2::after { content:""; position:absolute; inset:0; background:radial-gradient(50% 70% at 50% 45%, rgba(199,100,255,0.3), transparent 65%); }
  .closeband h2 { font-family:var(--display); font-size:clamp(30px,4vw,48px); font-weight:700; letter-spacing:-0.5px; margin-bottom:12px; text-shadow:0 4px 30px rgba(0,0,0,0.3); }
  .closeband p { color:rgba(255,255,255,0.9); margin-bottom:36px; font-weight:500; }
```

- [ ] **Step 2: Add grid + setup + closing markup**

Insert after the last `.feature`:

```html
<div class="gridsection" id="commands">
  <h2 class="rv">Everything else your guild runs on</h2>
  <p class="lead rv">Eleven systems, one bot — every server's data fully isolated. A few more you get out of the box:</p>
  <div class="grid3">
    <div class="gcard rv"><h3>Accounts &amp; API keys</h3><p>Members securely link their GW2 API keys; guild lookups by name resolve in seconds.</p></div>
    <div class="gcard rv d1"><h3>Per-guild config</h3><p>One role-gated setup flow, with every server's settings and data stored separately.</p></div>
    <div class="gcard rv d1"><h3>Audit logging</h3><p>Log joins, leaves, kicks, bans, edits and role/channel changes to a channel you choose.</p></div>
  </div>
</div>

<div class="setuplead">
  <h2 class="rv">Set up before your coffee cools</h2>
  <p class="rv">One command. Every server gets its own config, roles, and isolated data.</p>
</div>
<div class="cmd rv">
  <div class="line"><span class="prompt">/setup</span> modlog:#audit-log <span class="okt">✓ done</span></div>
  <small>Creates roles, wires alerts, and stores this server's settings separately from every other guild.</small>
</div>

<div class="closeband">
  <div class="bgband2"></div>
  <h2 class="rv">Your guild deserves its own colors.</h2>
  <p class="rv d1">Free · multi-guild · two-minute setup</p>
  <a class="pill rv d1" href="https://discord.com/oauth2/authorize?client_id=1433732142629912626&permissions=8&integration_type=0&scope=bot">＋ Add AxiTools to Discord</a>
</div>
```

- [ ] **Step 3: Verify render**

Render `site/index.html`. Expected: centered 3-card grid, a mono setup-command chip, then a full-width skewed neon closing band with headline + invite pill. Scroll from top to confirm the whole page reads cohesively.

- [ ] **Step 4: Commit**

```bash
git add site/index.html
git commit -m "feat(site): add capability grid, setup command, and closing CTA band"
```

---

### Task 6: Restyle privacy + terms on the shared shell

Rebuild both legal pages on the Task 1 shell: same header/footer/ambient/tokens, Chakra Petch headings, Inter body, single centered readable column. Preserve legal copy verbatim.

**Files:**
- Modify (overwrite): `site/privacy.html`, `site/terms.html`
- Reference: existing `site/privacy.html` / `site/terms.html` for the copy to preserve; aegis privacy page shell.

**Interfaces:**
- Consumes: the shared shell (tokens, header, footer, ambient) from Task 1.
- Produces: styled legal pages. No JS needed.

- [ ] **Step 1: Extract existing legal copy**

Run: `cat site/privacy.html` and `cat site/terms.html`. Copy the human-readable body text (headings, paragraphs, lists, contact email/links) verbatim — this is the content to preserve. Note the existing extensionless internal links.

- [ ] **Step 2: Add a `.legal` layout style block**

Both files use the same shell from Task 1 (`:root` tokens, `.ambient`, `header`, `footer` — copy them byte-identical) plus this legal-specific CSS:

```css
  .legal { max-width:760px; margin:0 auto; padding:150px 28px 90px; }
  .legal h1 { font-family:var(--display); font-size:clamp(30px,4vw,46px); font-weight:700; letter-spacing:-0.5px; margin-bottom:8px; }
  .legal .updated { color:var(--muted); font-size:14px; margin-bottom:36px; }
  .legal h2 { font-family:var(--display); font-size:22px; font-weight:600; margin:34px 0 12px; }
  .legal p, .legal li { color:#c3c9dd; font-size:15.5px; margin-bottom:12px; }
  .legal ul { padding-left:22px; margin-bottom:12px; }
  .legal a { color:var(--cyan); text-decoration:none; }
  .legal a:hover { text-decoration:underline; }
```

- [ ] **Step 3: Rebuild `site/privacy.html`**

Structure: full shell (head with title "AxiTools — Privacy Policy", canonical `https://tools.axi.link/privacy`, same fonts link, `:root`+`.ambient`+`header`+`footer` copied from index.html, plus the `.legal` block) with the preserved privacy copy wrapped in `<main class="legal">…</main>` between header and footer. Keep every heading/paragraph/link from the original. No hero, no script.

- [ ] **Step 4: Rebuild `site/terms.html`**

Same as Step 3 with title "AxiTools — Terms of Service", canonical `https://tools.axi.link/terms`, and the preserved terms copy in `<main class="legal">`.

- [ ] **Step 5: Verify render (both)**

Render each of `site/privacy.html` and `site/terms.html`. Expected: identical header/footer/ambient to the landing; a single centered column of readable legal text with Chakra Petch headings. Confirm all original sections are present.

- [ ] **Step 6: Verify copy + links preserved**

Run: `grep -oiE "support@axi.link|/privacy|/terms" site/privacy.html site/terms.html`
Expected: internal links stay extensionless; any contact email from the originals still present. Diff the visible text against the originals if unsure — nothing should be dropped.

- [ ] **Step 7: Commit**

```bash
git add site/privacy.html site/terms.html
git commit -m "feat(site): restyle privacy and terms on the redesigned shell"
```

---

### Task 7: Final polish — responsive, reduced-motion, cross-page consistency, deploy check

Full-page review of all three pages at desktop + mobile widths, verify shared markup is in sync, run the wrangler dry-run.

**Files:**
- Modify (as needed for fixes): `site/index.html`, `site/privacy.html`, `site/terms.html`

**Interfaces:**
- Consumes: everything from Tasks 1–6.

- [ ] **Step 1: Desktop render sweep**

Render all three pages via `mcp__sai__sai_render_html`. Confirm: cohesive spacing, no overflow, gradients/ambient present, fonts referenced. Note any visual issues.

- [ ] **Step 2: Mobile render check**

Open `site/index.html` in the playwright MCP browser (load via ToolSearch `select:mcp__plugin_playwright_playwright__browser_navigate,mcp__plugin_playwright_playwright__browser_resize,mcp__plugin_playwright_playwright__browser_take_screenshot`), resize to 390×844, screenshot. Expected: hero stacks to one column, Discord sidebar hidden, feature rows single-column, grid single-column, no horizontal scroll. Fix any breakage in the `@media (max-width:960px)` / `860px` blocks.

- [ ] **Step 3: Reduced-motion check**

In the browser, emulate `prefers-reduced-motion: reduce` (via `browser_evaluate` matchMedia or devtools emulation) and reload. Expected: hero content and all `.rv` elements visible immediately; Discord mock shows both messages statically; no infinite animations. Confirm the reduced-motion media query in each file covers `.rv`, `.msg`, hero elements.

- [ ] **Step 4: Console + link check**

In the browser, check the console for errors on `index.html` (should be none). Verify the invite pill href and internal `/privacy` `/terms` `#commands` links resolve. Run: `grep -c "client_id=1433732142629912626" site/index.html` → expect ≥3 (hero, closing band, header/footer).

- [ ] **Step 5: Cross-page shell consistency**

Confirm the `:root` block, `.ambient`, `header`, and `footer` markup are byte-identical across the three files. Run:
```bash
for f in index privacy terms; do sed -n '/<header>/,/<\/header>/p' site/$f.html | md5sum; done
```
Expected: all three md5 sums identical. If not, reconcile to match `index.html`.

- [ ] **Step 6: Trailing newline check**

Run: `for f in site/index.html site/privacy.html site/terms.html; do [ -n "$(tail -c1 "$f")" ] && echo "MISSING newline: $f" || echo "ok: $f"; done`
Expected: all `ok`. Fix any missing trailing newline.

- [ ] **Step 7: Deploy dry-run**

Run: `cd site 2>/dev/null; npx wrangler deploy --dry-run 2>&1 | tail -20` (or from repo root per `wrangler.toml` location). Expected: builds/validates without error. Do NOT actually deploy — that's the user's call.

- [ ] **Step 8: Commit any fixes**

```bash
git add site/
git commit -m "polish(site): responsive, reduced-motion, and cross-page consistency fixes"
```

---

## Self-Review

**Spec coverage:**
- Palette/ambient/gradient → Task 1 (tokens, ambient), Task 2/5 (bands). ✓
- Shape softening, card treatment → Tasks 2,4,5 (radii, shadows). ✓
- Typography (Orbitron wordmark+accent, Chakra Petch display, Inter, JetBrains Mono) → Task 1 fonts link + tokens; hero accent Task 2. ✓
- Motion + reduced-motion guard → Tasks 2 (playback), 4 (reveal), 7 (verify). ✓
- Landing structure (header, hero, stats, 4 features, grid, setup, close, footer) → Tasks 1–5. ✓
- Hero `/build` animated mock → Task 2. ✓
- Stats strip static (drafted copy) → Task 3. ✓
- Feature consolidation (4 rows + 3-card grid) → Tasks 4,5. ✓
- Legal pages on shared shell, copy verbatim, extensionless links → Task 6. ✓
- Self-contained files, Workers deploy, asset reuse, trailing newline → Tasks 1,6,7. ✓

**Placeholder scan:** No TBD/TODO; all steps carry concrete code or exact commands. Legal-page copy is "preserve from original" (Task 6 Step 1 extracts it) rather than reproduced here — deliberate, since the source is in-repo and must not be altered.

**Type/name consistency:** Class names (`.dembed`, `.rv`, `.pill`, `.feature.flip`, `.mockcard`, `.pin`) are defined in the task that introduces them and reused consistently downstream. The `<script>` IIFE is created in Task 2 and extended (not redefined) in Task 4. Invite URL identical everywhere.
