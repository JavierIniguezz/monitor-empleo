#!/usr/bin/env python3
"""
Monitor de Ofertas de Empleo – Javier Íñiguez
==============================================
Corre diariamente vía GitHub Actions y genera index.html.

Para añadir o quitar fuentes → edita config.yaml (no toques este archivo)
Para ajustar palabras clave  → edita la sección 'keywords' en config.yaml
"""

import yaml
import feedparser
import requests
from bs4 import BeautifulSoup
from datetime import datetime
import hashlib
import re

# ── CONFIGURACIÓN ─────────────────────────────────────────────────────────────

def load_config(path="config.yaml"):
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

# ── FILTROS ───────────────────────────────────────────────────────────────────

def matches(text, keywords):
    t = text.lower()
    return any(kw.lower() in t for kw in keywords)

def excluded(text, terms):
    t = text.lower()
    return any(ex.lower() in t for ex in terms)

def categorize(text, config):
    """Periodismo tiene prioridad absoluta sobre Comunicación."""
    if matches(text, config["keywords"]["periodismo"]):
        return "Periodismo"
    if matches(text, config["keywords"]["comunicacion"]):
        return "Comunicación"
    return None

def make_id(title):
    """
    ID único basado solo en el título (normalizado).
    Usamos solo el título — no la empresa — porque el mismo puesto
    puede aparecer en varias secciones de UNjobs con distintos nombres
    de fuente, y queremos eliminarlo como duplicado.
    """
    key = re.sub(r'\W+', '', title.lower())
    return hashlib.md5(key.encode()).hexdigest()[:8]

def make_job(title, empresa, link, resumen, categoria, fuente, fecha=""):
    return {
        "id":        make_id(title),
        "titulo":    title,
        "empresa":   empresa,
        "link":      link,
        "resumen":   resumen[:220] if resumen else "",
        "categoria": categoria,
        "fuente":    fuente,
        "fecha":     fecha,
    }

# ── SCRAPERS ──────────────────────────────────────────────────────────────────

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

def scrape_rss(source, config):
    """
    Lee un feed RSS/Atom. El más fiable — los feeds están diseñados
    para ser leídos por máquinas.
    """
    jobs = []
    try:
        feed = feedparser.parse(source["feed"])
        for entry in feed.entries[:30]:
            title   = entry.get("title", "").strip()
            link    = entry.get("link", "").strip()
            raw_sum = entry.get("summary", "")
            summary = BeautifulSoup(raw_sum, "html.parser").get_text()[:300]
            date    = entry.get("published", "")[:10] if entry.get("published") else ""
            full    = f"{title} {summary}"

            if excluded(full, config["exclusiones"]): continue
            cat = categorize(full, config)
            if not cat: continue

            jobs.append(make_job(title, source["name"], link, summary, cat, source["name"], date))

    except Exception as e:
        print(f"    ⚠ RSS error ({source['name']}): {e}")
    return jobs


def scrape_unjobs(source, config):
    """Scraper específico para unjobs.org."""
    jobs = []
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")

        for item in soup.select("article, .job, .vacancy, li.job-item, div[class*='vacancy']")[:40]:
            a = item.select_one("a[href]")
            if not a: continue
            title = a.get_text(strip=True)
            if len(title) < 8: continue

            link = a["href"]
            if not link.startswith("http"):
                link = "https://unjobs.org" + link

            org_el  = item.select_one(".org, .organization, [class*='org']")
            empresa = org_el.get_text(strip=True) if org_el else ""
            full    = f"{title} {empresa}"

            if excluded(full, config["exclusiones"]): continue
            cat = categorize(full, config)
            if not cat: cat = "Comunicación"

            jobs.append(make_job(title, empresa or source["name"], link, "", cat, source["name"]))

    except Exception as e:
        print(f"    ⚠ UNjobs error ({source['name']}): {e}")
    return jobs


def scrape_impactpool(source, config):
    """Scraper para impactpool.org."""
    jobs = []
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")

        for item in soup.select(".job-item, article, .vacancy-item, [class*='job-card']")[:40]:
            a = item.select_one("a[href]")
            if not a: continue
            title = a.get_text(strip=True)
            if len(title) < 8: continue

            link = a["href"]
            if not link.startswith("http"):
                link = "https://www.impactpool.org" + link

            org_el  = item.select_one(".organization, .org-name, [class*='organ']")
            empresa = org_el.get_text(strip=True) if org_el else ""
            full    = f"{title} {empresa}"

            if excluded(full, config["exclusiones"]): continue
            cat = categorize(full, config)
            if not cat: continue

            jobs.append(make_job(title, empresa or source["name"], link, "", cat, source["name"]))

    except Exception as e:
        print(f"    ⚠ Impactpool error: {e}")
    return jobs


def scrape_html(source, config):
    """
    Scraper HTML genérico. Usa los selectores CSS definidos en config.yaml.
    Si los selectores fallan (la web cambió su estructura), devuelve 0
    resultados sin romper el script.
    """
    jobs = []
    try:
        r = requests.get(source["url"], headers=HEADERS, timeout=20)
        soup = BeautifulSoup(r.text, "html.parser")
        sel  = source.get("selectors", {})

        item_sel = sel.get("item", "article, li, .job, .vacancy, .opening, [class*='job-item']")
        items    = soup.select(item_sel)

        for item in items[:30]:
            title_sel = sel.get("title", "h2 a, h3 a, h4 a, a[class*='title'], a")
            title_el  = item.select_one(title_sel)
            if not title_el: continue

            title = title_el.get_text(strip=True)
            if len(title) < 8: continue

            link = title_el.get("href", "")
            if not link:
                link_el = item.select_one("a[href]")
                link    = link_el["href"] if link_el else ""

            base = source.get("base_url", "")
            if link and not link.startswith("http") and base:
                link = base.rstrip("/") + "/" + link.lstrip("/")

            desc_sel = sel.get("desc", "p, .description, .summary, .excerpt")
            desc_el  = item.select_one(desc_sel)
            desc     = desc_el.get_text(strip=True)[:220] if desc_el else ""
            full     = f"{title} {desc} {source['name']}"

            if excluded(full, config["exclusiones"]): continue
            cat = categorize(full, config)
            if not cat: continue

            jobs.append(make_job(title, source["name"], link, desc, cat, source["name"]))

    except Exception as e:
        print(f"    ⚠ HTML error ({source['name']}): {e}")
    return jobs


# ── DISPATCHER ────────────────────────────────────────────────────────────────

SCRAPERS = {
    "rss":        scrape_rss,
    "unjobs":     scrape_unjobs,
    "impactpool": scrape_impactpool,
    "html":       scrape_html,
}

def fetch_all(config):
    all_jobs = []
    seen_ids = set()

    for section_name, sources in config["sources"].items():
        print(f"\n── {section_name} ──")
        for source in sources:

            if not source.get("activo", True):
                print(f"  ⏭  {source['name']} (desactivado)")
                continue

            print(f"  → {source['name']}...", end=" ", flush=True)
            scraper = SCRAPERS.get(source.get("type", "html"), scrape_html)
            jobs    = scraper(source, config)

            # Deduplicar por ID de título
            nuevos = [j for j in jobs if j["id"] not in seen_ids]
            seen_ids.update(j["id"] for j in nuevos)
            all_jobs.extend(nuevos)

            print(f"{len(nuevos)} oferta(s)")

    return all_jobs


# ── GENERACIÓN HTML ───────────────────────────────────────────────────────────

def generate_html(jobs, config):
    now        = datetime.now().strftime("%A %d de %B de %Y · %H:%M h")
    periodismo = sorted([j for j in jobs if j["categoria"] == "Periodismo"],  key=lambda x: x["empresa"])
    comms      = sorted([j for j in jobs if j["categoria"] == "Comunicación"], key=lambda x: x["empresa"])
    manuales   = config.get("revisar_manualmente", [])

    def card(j):
        fecha_html = f' · {j["fecha"]}' if j.get("fecha") else ""
        desc_html  = f'<p class="desc">{j["resumen"]}…</p>' if j.get("resumen") else ""
        return f"""
        <article class="card">
          <div class="card-top">
            <span class="company">{j['empresa']}</span>
            <span class="meta">vía {j['fuente']}{fecha_html}</span>
          </div>
          <a href="{j['link']}" target="_blank" rel="noopener" class="job-link">{j['titulo']}</a>
          {desc_html}
        </article>"""

    def section(emoji, title, items, color):
        if not items: return ""
        cards = "\n".join(card(j) for j in items)
        return f"""
        <section class="group">
          <header class="group-head" style="border-color:{color}">
            <span>{emoji}&nbsp; {title}</span>
            <span class="badge">{len(items)}</span>
          </header>
          {cards}
        </section>"""

    manual_links = "".join(
        f'<li><a href="{m["url"]}" target="_blank">{m["nombre"]}</a></li>'
        for m in manuales
    )
    manual_html = f"""
        <section class="group manual">
          <header class="group-head" style="border-color:#f59e0b">
            <span>🔍&nbsp; Revisar manualmente</span>
            <span class="badge" style="background:#fef3c7;color:#92400e">{len(manuales)}</span>
          </header>
          <div class="manual-body">
            <p>Estas fuentes no se pueden monitorizar automáticamente
               (muros de pago o sistemas con JavaScript). Ábrelas cuando
               busques ofertas.</p>
            <ul>{manual_links}</ul>
          </div>
        </section>""" if manuales else ""

    empty_html = (
        '<p class="empty">No se encontraron ofertas hoy. '
        'Puede que algunas fuentes hayan cambiado su estructura — '
        'abre un hilo con Claude para revisar los selectores.</p>'
    ) if not jobs else ""

    fuentes_usadas = ", ".join(sorted(set(j["fuente"] for j in jobs)))

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>Monitor de Empleo · Javier Íñiguez</title>
  <style>
    *{{box-sizing:border-box;margin:0;padding:0}}
    body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;
          background:#f1f5f9;color:#0f172a;line-height:1.5}}
    header.main{{background:#0f172a;color:#fff;padding:24px 28px}}
    header.main h1{{font-size:19px;font-weight:800;letter-spacing:-.02em}}
    header.main p{{color:#64748b;font-size:12px;margin-top:4px}}
    .stats{{display:flex;gap:12px;margin-top:16px;flex-wrap:wrap}}
    .stat{{background:rgba(255,255,255,.07);border-radius:8px;padding:8px 16px;text-align:center}}
    .stat b{{display:block;font-size:22px;font-weight:800}}
    .stat small{{font-size:10px;color:#94a3b8;font-weight:600;text-transform:uppercase}}
    main{{max-width:820px;margin:0 auto;padding:22px 16px}}
    .group{{margin-bottom:28px;border-radius:10px;overflow:hidden;
            box-shadow:0 1px 3px rgba(0,0,0,.07)}}
    .group-head{{display:flex;justify-content:space-between;align-items:center;
                 padding:11px 16px;background:#fff;
                 border-left:4px solid;font-size:11px;font-weight:800;
                 text-transform:uppercase;letter-spacing:.08em}}
    .badge{{background:#e2e8f0;border-radius:10px;padding:2px 10px;
            font-size:11px;font-weight:700}}
    .card{{background:#fff;padding:14px 16px;border-bottom:1px solid #f1f5f9}}
    .card:last-child{{border-bottom:none}}
    .card-top{{display:flex;justify-content:space-between;margin-bottom:3px}}
    .company{{font-weight:700;font-size:12px}}
    .meta{{font-size:11px;color:#94a3b8}}
    .job-link{{display:block;font-size:14px;font-weight:600;
               color:#1d4ed8;text-decoration:none;margin-bottom:4px}}
    .job-link:hover{{text-decoration:underline}}
    .desc{{font-size:12px;color:#64748b;line-height:1.55}}
    .manual-body{{background:#fff;padding:14px 16px}}
    .manual-body p{{font-size:12px;color:#64748b;margin-bottom:10px}}
    .manual-body ul{{list-style:none;display:flex;flex-wrap:wrap;gap:8px}}
    .manual-body a{{font-size:12px;color:#1d4ed8;background:#eff6ff;
                    padding:5px 12px;border-radius:20px;text-decoration:none;
                    font-weight:600}}
    .manual-body a:hover{{background:#dbeafe}}
    .empty{{text-align:center;padding:48px;color:#94a3b8;font-size:13px}}
    footer{{text-align:center;padding:20px;color:#94a3b8;font-size:11px}}
    footer a{{color:#94a3b8}}
  </style>
</head>
<body>
<header class="main">
  <h1>🌍 Monitor de Empleo · Javier Íñiguez</h1>
  <p>Actualizado: {now}</p>
  <div class="stats">
    <div class="stat"><b>{len(jobs)}</b><small>Total</small></div>
    <div class="stat"><b style="color:#60a5fa">{len(periodismo)}</b><small>Periodismo</small></div>
    <div class="stat"><b style="color:#a78bfa">{len(comms)}</b><small>Comunicación</small></div>
    <div class="stat"><b style="color:#fbbf24">{len(manuales)}</b><small>Manual</small></div>
  </div>
</header>
<main>
  {empty_html}
  {section("📰", "Periodismo", periodismo, "#2563eb")}
  {section("📢", "Comunicación", comms, "#7c3aed")}
  {manual_html}
</main>
<footer>
  Fuentes: {fuentes_usadas} ·
  <a href="https://github.com/JavierIniguezz/monitor-empleo">Ver código en GitHub</a>
</footer>
</body>
</html>"""


# ── MAIN ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("🔍 Iniciando monitor de empleo...\n")
    config = load_config()
    jobs   = fetch_all(config)
    html   = generate_html(jobs, config)

    with open("index.html", "w", encoding="utf-8") as f:
        f.write(html)

    p = len([j for j in jobs if j["categoria"] == "Periodismo"])
    c = len([j for j in jobs if j["categoria"] == "Comunicación"])
    print(f"\n✅ Listo. {len(jobs)} ofertas encontradas → index.html generado")
    print(f"   📰 Periodismo:   {p}")
    print(f"   📢 Comunicación: {c}")
