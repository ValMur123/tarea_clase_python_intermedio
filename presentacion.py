"""
Genera docs/index.html (presentación) y README.md a partir de outputs/resultados.json.
Lo llama pipeline.py al final; también puedes correrlo solo:  python presentacion.py
"""
from __future__ import annotations

import html
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent
OUT = RAIZ / "outputs"
DOCS = RAIZ / "docs"


def fmt(x, dec=0) -> str:
    return f"{x:,.{dec}f}".replace(",", " ")


def e(x) -> str:
    return html.escape(str(x))


def tit(x) -> str:
    return str(x).title()


def cargar() -> dict:
    return json.loads((OUT / "resultados.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------
CSS = """
:root{--osc:#1c1b19;--crema:#f6f1ea;--blanco:#fff;--rojo:#b5441b;--azul:#2f5d80;--gris:#5b5a57;--borde:#e8e1d6;--texto:#1c1b19}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--crema:#24221f;--blanco:#2d2b28;--texto:#f3eee7;--gris:#bdb7ae;--borde:#3a3733}}
*{box-sizing:border-box}html{scroll-snap-type:y proximity}
body{margin:0;background:var(--crema);color:var(--texto);font-family:"Public Sans",system-ui,sans-serif;line-height:1.5}
h1,h2,h3{font-family:"Oswald",sans-serif;font-weight:600;line-height:1.05;margin:0 0 .5em}
section{min-height:100vh;padding:clamp(28px,6vw,80px) clamp(16px,6vw,90px);scroll-snap-align:start;display:flex;flex-direction:column;justify-content:center}
.oscura{background:var(--osc);color:#f6f1ea}.oscura .gris{color:#c9c3ba}
.etiqueta{font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:.85rem;color:var(--rojo);margin-bottom:.8em}
.oscura .etiqueta{color:#f08a4b}
h1{font-size:clamp(2.6rem,8vw,6rem)}h2{font-size:clamp(1.8rem,4.5vw,3.2rem)}
.sub{font-size:clamp(1.05rem,2.2vw,1.6rem);max-width:60ch;color:#ddd6cc}
.gris{color:var(--gris)}.pie{margin-top:2.5em;font-size:.85rem;color:var(--gris)}
.grid{display:grid;gap:18px;grid-template-columns:repeat(auto-fit,minmax(240px,1fr))}
.card{background:var(--blanco);border:1px solid var(--borde);border-radius:10px;padding:22px}
.card .num{font-family:"Oswald",sans-serif;font-size:clamp(2.4rem,6vw,3.8rem);color:var(--rojo);line-height:1}
.card h3{font-size:1.35rem;color:var(--rojo);font-weight:500}
.card.azul{border-top:4px solid var(--azul)}.card.rojo{border-top:4px solid var(--rojo)}
.card.azul h3{color:var(--azul)}
table{border-collapse:collapse;width:100%;background:var(--blanco);font-size:.95rem}
th,td{border:1px solid var(--borde);padding:8px 12px;text-align:left}th{background:rgba(0,0,0,.05)}
td.n{text-align:right;font-variant-numeric:tabular-nums}
img{max-width:100%;height:auto;border-radius:8px;border:1px solid var(--borde);background:#fff}
iframe{width:100%;height:70vh;border:1px solid var(--borde);border-radius:8px;background:#ddd}
.hallazgo{display:flex;gap:16px;align-items:flex-start;margin:0 0 18px}
.hallazgo b{font-family:"Oswald",sans-serif;font-size:2.2rem;color:var(--rojo);line-height:1}
.dos{display:grid;gap:24px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));align-items:start}
ul{padding-left:1.2em}li{margin:.35em 0}
@media print{section{min-height:auto;page-break-after:always}}
"""


def construir_html(r: dict) -> str:
    inc, esc, area, cob, p = r["incendio"], r["escenas"], r["area"], r["cobertura"], r["peru_7d"]
    lugar = f"{tit(inc['distrito'])}, {tit(inc['provincia'])} ({tit(inc['departamento'])})"
    top_cob = cob["clases"][0] if cob["clases"] else {"clase": "—", "pct": 0}
    sev = {s["severidad"]: s["ha"] for s in area["por_severidad"]}
    alta = sev.get("Moderada-alta", 0) + sev.get("Alta", 0)
    cfg = r["config"]

    filas_dist = "".join(
        f"<tr><td>{e(tit(d['distrito']))}</td><td>{e(tit(d['provincia']))}</td><td class='n'>{fmt(d['ha_quemadas'])}</td></tr>"
        for d in area["por_distrito"])
    filas_sev = "".join(f"<tr><td>{e(s['severidad'])}</td><td class='n'>{fmt(s['ha'])}</td></tr>"
                        for s in area["por_severidad"])
    hallazgos = "".join(f"<div class='hallazgo'><b>{i}</b><p>{e(h)}</p></div>"
                        for i, h in enumerate(r["hallazgos"][:3], 1))
    cob_img = ("<img src='img/cobertura.png' alt='Hectáreas quemadas por cobertura del suelo'>"
               if cob["clases"] else "<p>No hubo datos de WorldCover para la zona.</p>")

    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Incendio de {e(tit(inc['distrito']))}</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Oswald:wght@500;600&family=Public+Sans:wght@400;700&display=swap" rel="stylesheet">
<style>{CSS}</style></head><body>

<section class="oscura">
  <div class="etiqueta">Ciencia de datos con Python · tarea de incendios · corte {e(r['corte'])}</div>
  <h1>¿Cuánto se quemó en {e(tit(inc['distrito']))} y qué se perdió?</h1>
  <p class="sub">Un incendio activo de {e(tit(inc['departamento']))} medido desde el espacio con Sentinel-2 (dNBR),
  focos VIIRS de la NASA y la cobertura del suelo de la ESA. Todo con datos públicos y en CPU.</p>
</section>

<section>
  <div class="etiqueta">La respuesta corta</div>
  <h2>{e(lugar)}</h2>
  <div class="grid">
    <div class="card"><div class="num">{fmt(area['ha_sembrado'])} ha</div>
      <p>quemadas entre el {e(esc['antes']['fecha'])} y el {e(esc['despues']['fecha'])} (dNBR ≥ {cfg['umbral_dnbr']}).</p></div>
    <div class="card"><div class="num">{top_cob['pct']:.0f} %</div>
      <p>del área quemada era <b>{e(top_cob['clase'].lower())}</b> según ESA WorldCover {e(cob['anio'])}.</p></div>
    <div class="card"><div class="num">{fmt(alta)} ha</div>
      <p>con severidad moderada-alta o alta: donde la vegetación tardará más en recuperarse.</p></div>
  </div>
  <p class="pie">{inc['focos']} focos VIIRS del {e(inc['inicio'])} al {e(inc['ultimo'])} · #{inc['ranking']} de {p['incendios']} incendios del Perú en 7 días</p>
</section>

<section>
  <div class="etiqueta">El mapa</div>
  <h2>Lo marrón es lo que se quemó</h2>
  <img src="img/mapa_dnbr.png" alt="Falso color Sentinel-2 y severidad dNBR del incendio">
  <p class="pie">Izquierda: Sentinel-2 en falso color (SWIR-NIR-rojo). Derecha: severidad dNBR con focos VIIRS (rojo) y límites distritales.</p>
</section>

<section>
  <div class="etiqueta">Explóralo</div>
  <h2>Mapa interactivo</h2>
  <iframe src="mapa.html" title="Mapa interactivo del área quemada" loading="lazy"></iframe>
  <p class="pie">Activa o apaga capas: satélite, distritos, área quemada y focos (pasa el cursor para ver fecha y satélite).</p>
</section>

<section>
  <div class="etiqueta">Tres hallazgos con números</div>
  <h2>Lo que encontré</h2>
  {hallazgos}
</section>

<section>
  <div class="etiqueta">¿Qué se quemó?</div>
  <h2>Cobertura y severidad</h2>
  <div class="dos">
    <div>{cob_img}</div>
    <div>
      <table><tr><th>Severidad (USGS)</th><th>ha</th></tr>{filas_sev}</table>
      <p></p>
      <table><tr><th>Distrito</th><th>Provincia</th><th>ha quemadas</th></tr>{filas_dist}</table>
    </div>
  </div>
</section>

<section>
  <div class="etiqueta">Contexto nacional</div>
  <h2>{fmt(p['focos'])} focos en 7 días, {p['incendios']} incendios</h2>
  <img src="img/top_incendios.png" alt="Los 10 incendios con más focos en el Perú">
  <p class="pie">Focos VIIRS (S-NPP, NOAA-20 y NOAA-21) del {e(p['desde'])} al {e(p['hasta'])}; {p['incendios_mas_100ha']} incendios tienen huella mayor a 100 ha.</p>
</section>

<section class="oscura">
  <div class="etiqueta">Cómo lo hice</div>
  <h2>Mis decisiones de método</h2>
  <div class="grid">
    <div class="card" style="background:#2a2926;border-color:#3a3733"><h3>Fechas</h3>
      <p class="gris">Antes: {e(esc['antes']['fecha'])} · Después: {e(esc['despues']['fecha'])}. De {esc['candidatas']} fechas candidatas, elegí la más reciente con pocas nubes en cada lado.</p></div>
    <div class="card" style="background:#2a2926;border-color:#3a3733"><h3>Nubes</h3>
      <p class="gris">{esc['antes']['nubes']:.1f}% y {esc['despues']['nubes']:.1f}% en la zona del incendio. Enmascaré nubes y sombras con la capa SCL; quedó válido el {area['pct_pixeles_validos']:.0f}% de la zona.</p></div>
    <div class="card" style="background:#2a2926;border-color:#3a3733"><h3>Umbral</h3>
      <p class="gris">dNBR ≥ {cfg['umbral_dnbr']} = quemado. Solo cuento parches a ≤ {cfg['buffer_semilla_m']} m de un foco VIIRS. Sin ese filtro saldrían {fmt(area['ha_solo_umbral'])} ha.</p></div>
    <div class="card" style="background:#2a2926;border-color:#3a3733"><h3>Zona</h3>
      <p class="gris">Focos a ≤ 1 km y ≤ 48 h forman un incendio. Analicé 3 km alrededor de sus focos, a {cfg['resolucion_m']} m por píxel, en UTM (no en grados).</p></div>
  </div>
</section>

<section>
  <div class="etiqueta">Limitaciones</div>
  <h2>Lo que este número no dice</h2>
  <ul>
    <li><b>Un foco de calor no es un incendio:</b> VIIRS ve píxeles de 375 m calientes; puede ser una quema agrícola. Por eso agrupé focos y descarté grupos de menos de 3.</li>
    <li><b>Las nubes y el humo esconden el fuego:</b> los píxeles nublados en cualquiera de las dos fechas no se cuentan, así que el área puede estar subestimada.</li>
    <li><b>El incendio puede seguir activo:</b> lo que ardió después del {e(esc['despues']['fecha'])} no está en la cifra.</li>
    <li><b>El umbral cambia el resultado:</b> 0,27 es un estándar del USGS, no una verdad; en pastizales de puna la señal es más débil.</li>
    <li><b>WorldCover es de {e(cob['anio'])}:</b> la cobertura pudo cambiar desde entonces.</li>
    <li>Herramienta académica: no reemplaza los reportes del COEN, INDECI, SERFOR ni SERNANP.</li>
  </ul>
  <p class="pie">Datos: NASA FIRMS (VIIRS) · Sentinel-2 L2A y ESA WorldCover vía Microsoft Planetary Computer · límites INEI (peru-geojson). Python: geopandas, rasterio, rasterstats, folium.</p>
</section>
</body></html>"""


# ---------------------------------------------------------------------
# README
# ---------------------------------------------------------------------
def construir_readme(r: dict) -> str:
    inc, esc, area, cob, p, cfg = r["incendio"], r["escenas"], r["area"], r["cobertura"], r["peru_7d"], r["config"]
    lugar = f"{tit(inc['distrito'])} ({tit(inc['provincia'])}, {tit(inc['departamento'])})"
    hall = "\n".join(f"{i}. {h}" for i, h in enumerate(r["hallazgos"][:3], 1))
    extra = "\n".join(f"- {h}" for h in r["hallazgos"][3:])
    dist = "\n".join(f"| {tit(d['distrito'])} | {tit(d['provincia'])} | {fmt(d['ha_quemadas'])} |" for d in area["por_distrito"])
    sev = "\n".join(f"| {s['severidad']} | {fmt(s['ha'])} |" for s in area["por_severidad"])
    cobt = "\n".join(f"| {c['clase']} | {fmt(c['ha'])} | {c['pct']:.1f}% |" for c in cob["clases"])
    return f"""# Incendios en el Perú vistos desde el espacio: {tit(inc['distrito'])}

**Retos A + C** · Ciencia de datos con Python · corte de datos: {r['corte']}

Presentación: abre [`docs/index.html`](docs/index.html) (o el sitio de GitHub Pages si está activado) · Mapa interactivo: [`docs/mapa.html`](docs/mapa.html)

## La pregunta

¿Cuántas hectáreas quemó el incendio de **{lugar}** y **qué tipo de cobertura** se perdió: bosque, pastizal o cultivo?

## Tres hallazgos con números

{hall}

{extra}

## Mapa

![Mapa del incendio: falso color y severidad dNBR](docs/img/mapa_dnbr.png)

![Hectáreas quemadas por cobertura](docs/img/cobertura.png)

| Severidad (dNBR, USGS) | ha |
|---|---:|
{sev}

| Distrito | Provincia | ha quemadas |
|---|---|---:|
{dist}

| Cobertura (WorldCover {cob['anio']}) | ha | % |
|---|---:|---:|
{cobt}

## Datos y fechas

| Dato | Fuente | Tipo | Fechas usadas |
|---|---|---|---|
| Focos de calor | NASA FIRMS, VIIRS S-NPP / NOAA-20 / NOAA-21, «South America 7d» | Puntos (CSV) | {p['desde']} – {p['hasta']} |
| Imágenes | Sentinel-2 L2A, Microsoft Planetary Computer (`sentinel-2-l2a`) | Raster 10–20 m | antes {esc['antes']['fecha']} ({esc['antes']['nubes']:.1f}% nubes) · después {esc['despues']['fecha']} ({esc['despues']['nubes']:.1f}% nubes) |
| Cobertura del suelo | ESA WorldCover (`esa-worldcover`) | Raster 10 m | {cob['anio']} |
| Límites distritales | INEI vía GitHub `juaneladio/peru-geojson` | Polígonos | — |

## Método

1. **Focos:** descargo los focos VIIRS de 7 días, me quedo con los que caen en distritos del Perú y descarto los de confianza baja ({fmt(p['focos'])} focos).
2. **Incendios:** focos a ≤ 1 km y ≤ 48 h entre sí forman un mismo incendio; descarto grupos de menos de 3 focos (posibles fuentes fijas). Resultan {p['incendios']} incendios. Elegí el #{inc['ranking']} por número de focos: {inc['focos']} focos del {inc['inicio']} al {inc['ultimo']}.
3. **Zona:** 3 km alrededor de los focos, en UTM (EPSG:327xx) a {cfg['resolucion_m']} m por píxel. Las áreas no se calculan en grados (EPSG:4326).
4. **Imágenes:** reviso {esc['candidatas']} fechas de Sentinel-2 y mido las nubes **en la zona del incendio** (capa SCL), no en toda la escena. Elijo la imagen más reciente antes del primer foco con ≤ {cfg['max_nubes_pre']}% nubes y la más reciente después con ≤ {cfg['max_nubes_post']}%. La tabla completa está en `outputs/escenas_sentinel2.csv`.
5. **dNBR:** NBR = (B08 − B12)/(B08 + B12); dNBR = NBR antes − NBR después. Enmascaro nubes, sombras y agua con SCL en ambas fechas (queda válido el {area['pct_pixeles_validos']:.0f}% de la zona).
6. **Área quemada:** dNBR ≥ {cfg['umbral_dnbr']}, contando solo los parches conectados a ≤ {cfg['buffer_semilla_m']} m de un foco VIIRS. Sin ese filtro salen {fmt(area['ha_solo_umbral'])} ha en lugar de {fmt(area['ha_sembrado'])} ha.
7. **Por distrito:** `rasterstats.zonal_stats` sobre el raster de quemado, con los distritos reproyectados al mismo CRS.
8. **¿Qué se quemó?:** cruzo los píxeles quemados con WorldCover (vecino más cercano a 20 m).

## Limitaciones

- **Un foco de calor no es un incendio.** VIIRS detecta píxeles calientes de 375 m, que también pueden ser quemas agrícolas.
- **Las nubes y el humo esconden el fuego.** Los píxeles nublados en cualquiera de las dos fechas no se cuentan, así que el área puede estar **subestimada**.
- **El incendio puede seguir activo.** Lo que ardió después del {esc['despues']['fecha']} no está incluido.
- **El umbral de 0,27 es una convención del USGS.** En pastizales de altura la señal es más débil y hay quemas que no lo superan.
- WorldCover es de {cob['anio']}, así que la cobertura pudo cambiar desde entonces.
- Es una herramienta académica y no reemplaza los reportes del COEN, INDECI, SERFOR ni SERNANP.

## Cómo reproducirlo

```bash
python -m venv .venv
.venv\\Scripts\\activate          # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
python pipeline.py               # ~5-15 min según la conexión
```

Para analizar otro incendio, cambia `DISTRITO_OBJETIVO` al inicio de `pipeline.py` (p. ej. `"RIO TAMBO"`). La lista de incendios activos está en `outputs/incendios_7dias.csv`.

## Estructura

```
pipeline.py        # todo el análisis (pasos 1-8)
presentacion.py    # genera docs/index.html y este README desde outputs/resultados.json
outputs/           # tablas CSV, dnbr.tif, quemado.tif, resultados.json
docs/              # presentación HTML, mapa interactivo e imágenes
```
"""


def main():
    r = cargar()
    DOCS.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(construir_html(r), encoding="utf-8")
    (RAIZ / "README.md").write_text(construir_readme(r), encoding="utf-8")
    print("  docs/index.html y README.md generados.")


if __name__ == "__main__":
    main()
