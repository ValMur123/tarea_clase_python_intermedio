"""
Incendios en el Perú vistos desde el espacio
Reto A (área quemada con dNBR) + Reto C (¿qué se quemó? con WorldCover)

Uso (desde la terminal de VS Code, con el entorno activado):
    python pipeline.py

Qué hace, paso a paso:
  1. Descarga los focos VIIRS de los últimos 7 días (NASA FIRMS) y los límites distritales.
  2. Limpia los focos (solo Perú, sin confianza baja) y los agrupa en incendios
     (focos a <= 1 km y <= 48 h = un mismo incendio).
  3. Elige un incendio (el más grande, o el del distrito que pongas en DISTRITO_OBJETIVO).
  4. Busca imágenes Sentinel-2 L2A en Planetary Computer, MUESTRA fechas y % de nubes
     en la zona del incendio y elige una imagen antes y otra después.
  5. Calcula NBR y dNBR, enmascara nubes con la capa SCL y mide hectáreas
     (umbral dNBR >= 0,27, "sembrado" con los focos VIIRS) por severidad y por distrito.
  6. Cruza el área quemada con ESA WorldCover: bosque, pastizal, cultivo...
  7. Exporta mapas, tablas, resultados.json, la presentación HTML y el README.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import unicodedata
from pathlib import Path

import numpy as np
import pandas as pd
import geopandas as gpd
import requests
import rasterio
from rasterio import features
from rasterio.enums import Resampling
from rasterio.transform import from_origin
from rasterio.vrt import WarpedVRT
from rasterstats import zonal_stats
from scipy import ndimage
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components
from scipy.spatial import cKDTree
import pystac_client
import planetary_computer
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.colors import BoundaryNorm, ListedColormap  # noqa: E402
from matplotlib.patches import Patch  # noqa: E402

# =====================================================================
# CONFIGURACIÓN: estas son TUS decisiones de método. Repórtalas siempre.
# =====================================================================
DISTRITO_OBJETIVO = None  # p. ej. "RIO TAMBO", "MANANTAY", "CHICHAS". None = el incendio con más focos
UMBRAL_DNBR = 0.27        # dNBR >= 0,27 = quemado (severidad moderada-baja o más, USGS)
MAX_NUBES_PRE = 20        # % máximo de nubes en la zona para la imagen ANTES
MAX_NUBES_POST = 30       # % máximo de nubes en la zona para la imagen DESPUÉS
MIN_COBERTURA = 90        # % mínimo de la zona que debe cubrir la imagen
DIAS_ANTES = 60           # cuántos días antes del primer foco buscar la imagen "antes"
RES = 20                  # metros por píxel (resolución de la banda SWIR B12)
BUFFER_AOI_M = 3000       # margen alrededor de los focos para la zona de análisis
BUFFER_SEMILLA_M = 750    # un parche quemado cuenta si está a <= 750 m de un foco VIIRS
MIN_FOCOS_INCENDIO = 3    # descarta "incendios" de 1-2 focos (posibles fuentes fijas)

RAIZ = Path(__file__).resolve().parent
DATA = RAIZ / "data"
OUT = RAIZ / "outputs"
DOCS = RAIZ / "docs"
for carpeta in (DATA, OUT, DOCS / "img"):
    carpeta.mkdir(parents=True, exist_ok=True)

FIRMS = {
    "S-NPP": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/suomi-npp-viirs-c2/csv/SUOMI_VIIRS_C2_South_America_7d.csv",
    "NOAA-20": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-20-viirs-c2/csv/J1_VIIRS_C2_South_America_7d.csv",
    "NOAA-21": "https://firms.modaps.eosdis.nasa.gov/data/active_fire/noaa-21-viirs-c2/csv/J2_VIIRS_C2_South_America_7d.csv",
}
DISTRITOS_URL = "https://raw.githubusercontent.com/juaneladio/peru-geojson/master/peru_distrital_simple.geojson"
STAC_URL = "https://planetarycomputer.microsoft.com/api/stac/v1"

SCL_VALIDO = [2, 4, 5, 7]     # sombra oscura, vegetación, suelo desnudo, sin clasificar
SCL_NUBE = [3, 8, 9, 10]      # sombra de nube, nube media, nube alta, cirros

SEVERIDAD = [  # Key & Benson (USGS)
    ("No quemado", -np.inf, 0.10),
    ("Baja", 0.10, 0.27),
    ("Moderada-baja", 0.27, 0.44),
    ("Moderada-alta", 0.44, 0.66),
    ("Alta", 0.66, np.inf),
]
WORLDCOVER = {
    10: "Bosque", 20: "Matorral", 30: "Pastizal", 40: "Cultivo", 50: "Urbano",
    60: "Suelo desnudo", 70: "Nieve/hielo", 80: "Agua", 90: "Humedal",
    95: "Manglar", 100: "Musgo/liquen",
}

GDAL_ENV = dict(
    GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
    CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.tiff",
    GDAL_HTTP_MAX_RETRY="5",
    GDAL_HTTP_RETRY_DELAY="2",
)


# ---------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------
def paso(n, texto):
    print(f"\n{'=' * 70}\n[{n}] {texto}\n{'=' * 70}", flush=True)


def normalizar(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto))
    return "".join(c for c in texto if not unicodedata.combining(c)).upper().strip()


def fmt(x, dec=0) -> str:
    """1786.4 -> '1 786' (como en las diapositivas del profesor)."""
    return f"{x:,.{dec}f}".replace(",", " ")


def descargar(url: str, destino: Path) -> Path:
    r = requests.get(url, timeout=180)
    r.raise_for_status()
    destino.write_bytes(r.content)
    return destino


def buscar_col(df, *candidatos):
    cols = {c.lower(): c for c in df.columns}
    for cand in candidatos:
        if cand.lower() in cols:
            return cols[cand.lower()]
    return None


def epsg_utm(lon: float, lat: float) -> int:
    zona = int((lon + 180) // 6) + 1
    return (32700 if lat < 0 else 32600) + zona


# ---------------------------------------------------------------------
# 1. datos vectoriales
# ---------------------------------------------------------------------
def cargar_distritos() -> gpd.GeoDataFrame:
    archivo = DATA / "peru_distrital_simple.geojson"
    if not archivo.exists():
        descargar(DISTRITOS_URL, archivo)
    d = gpd.read_file(archivo)
    if d.crs is None:
        d = d.set_crs(4326)
    d = d.to_crs(4326)
    mapa = {
        "distrito": buscar_col(d, "NOMBDIST", "DISTRITO", "NOMB_DIST"),
        "provincia": buscar_col(d, "NOMBPROV", "PROVINCIA", "NOMB_PROV"),
        "departamento": buscar_col(d, "NOMBDEP", "DEPARTAMEN", "DEPARTAMENTO", "NOMB_DEP"),
        "ubigeo": buscar_col(d, "IDDIST", "UBIGEO", "IDDISTRITO"),
    }
    for nuevo, viejo in mapa.items():
        d[nuevo] = d[viejo].astype(str) if viejo else "?"
    return d[["ubigeo", "departamento", "provincia", "distrito", "geometry"]]


def cargar_focos(distritos: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    hoy = dt.date.today().isoformat()
    tablas = []
    for sat, url in FIRMS.items():
        archivo = DATA / f"firms_{sat}_7d_{hoy}.csv"
        try:
            if not archivo.exists():
                descargar(url, archivo)
            t = pd.read_csv(archivo)
            t["fuente"] = sat
            tablas.append(t)
            print(f"  {sat:8s}: {len(t):6d} focos en Sudamérica")
        except Exception as e:  # noqa: BLE001
            print(f"  {sat:8s}: no se pudo descargar ({e}). Sigo con los demás.")
    if not tablas:
        sys.exit("No se pudo descargar ningún archivo de FIRMS. Revisa tu conexión.")
    f = pd.concat(tablas, ignore_index=True)

    # limpieza 1: caja del Perú (rápido) y confianza
    f = f[f.latitude.between(-18.6, 0.1) & f.longitude.between(-81.5, -68.5)]
    n0 = len(f)
    f = f[f["confidence"].astype(str).str.lower().str[0] != "l"]
    print(f"  Se descartan {n0 - len(f)} focos de confianza baja")

    f["fecha_hora"] = pd.to_datetime(
        f["acq_date"].astype(str) + " " + f["acq_time"].astype(int).astype(str).str.zfill(4),
        format="%Y-%m-%d %H%M", utc=True,
    )
    g = gpd.GeoDataFrame(f, geometry=gpd.points_from_xy(f.longitude, f.latitude), crs=4326)
    # limpieza 2: solo los que caen dentro de un distrito del Perú
    g = gpd.sjoin(g, distritos, how="inner", predicate="within").drop(columns="index_right")
    print(f"  Focos dentro del Perú (confianza nominal/alta): {len(g)}")
    return g.reset_index(drop=True)


def agrupar_incendios(focos: gpd.GeoDataFrame, dist_m=1000, horas=48) -> np.ndarray:
    """Focos a <= dist_m y <= horas entre sí pertenecen al mismo incendio (componentes conexos)."""
    utm = focos.to_crs(32718)
    xy = np.c_[utm.geometry.x.values, utm.geometry.y.values]
    t = (focos["fecha_hora"] - focos["fecha_hora"].min()).dt.total_seconds().values / 3600
    return etiquetar(xy, t, dist_m, horas)


def etiquetar(xy: np.ndarray, t_horas: np.ndarray, dist_m: float, horas: float) -> np.ndarray:
    n = len(xy)
    pares = cKDTree(xy).query_pairs(dist_m, output_type="ndarray")
    if len(pares):
        pares = pares[np.abs(t_horas[pares[:, 0]] - t_horas[pares[:, 1]]) <= horas]
    grafo = coo_matrix((np.ones(len(pares)), (pares[:, 0], pares[:, 1])), shape=(n, n))
    _, etiquetas = connected_components(grafo, directed=False)
    return etiquetas


def resumir_incendios(focos: gpd.GeoDataFrame) -> pd.DataFrame:
    utm = focos.to_crs(32718)
    filas = []
    for k, grupo in focos.groupby("incendio"):
        huella = utm.loc[grupo.index].geometry.buffer(375).union_all()  # píxel VIIRS ~375 m
        filas.append({
            "incendio": k,
            "focos": len(grupo),
            "frp_total_MW": round(grupo["frp"].sum(), 1),
            "inicio": grupo["fecha_hora"].min(),
            "ultimo": grupo["fecha_hora"].max(),
            "huella_ha": round(huella.area / 1e4),
            "distrito": grupo["distrito"].mode().iat[0],
            "provincia": grupo["provincia"].mode().iat[0],
            "departamento": grupo["departamento"].mode().iat[0],
            "lon": grupo.longitude.mean(),
            "lat": grupo.latitude.mean(),
        })
    r = pd.DataFrame(filas)
    r = r[r.focos >= MIN_FOCOS_INCENDIO].sort_values("focos", ascending=False).reset_index(drop=True)
    r["ranking"] = np.arange(1, len(r) + 1)
    return r


# ---------------------------------------------------------------------
# 2. rejilla común y lectura de rasters remotos
# ---------------------------------------------------------------------
class Rejilla:
    def __init__(self, geom_utm, epsg: int, res: int):
        minx, miny, maxx, maxy = geom_utm.bounds
        minx, miny = np.floor(minx / res) * res, np.floor(miny / res) * res
        maxx, maxy = np.ceil(maxx / res) * res, np.ceil(maxy / res) * res
        self.crs = f"EPSG:{epsg}"
        self.epsg = epsg
        self.res = res
        self.ancho = int((maxx - minx) / res)
        self.alto = int((maxy - miny) / res)
        self.transform = from_origin(minx, maxy, res, res)
        self.bounds = (minx, miny, maxx, maxy)
        self.ha_pixel = res * res / 1e4

    def leer(self, items, asset: str, resampling=Resampling.bilinear, escala=None) -> np.ndarray:
        """Mosaico de uno o varios items STAC (misma fecha) reproyectados a esta rejilla."""
        salida = np.full((self.alto, self.ancho), np.nan, dtype="float32")
        with rasterio.Env(**GDAL_ENV):
            for it in items:
                with rasterio.open(it.assets[asset].href) as src:
                    nodata = src.nodata if src.nodata is not None else 0
                    with WarpedVRT(src, crs=self.crs, transform=self.transform,
                                   width=self.ancho, height=self.alto,
                                   resampling=resampling, src_nodata=nodata, nodata=nodata) as vrt:
                        a = vrt.read(1).astype("float32")
                a[a == nodata] = np.nan
                if escala is not None:
                    a = escala(it, a)
                salida = np.where(np.isnan(salida), a, salida)
        return salida

    def guardar(self, arr, ruta: Path, dtype="float32", nodata=np.nan):
        with rasterio.open(ruta, "w", driver="GTiff", height=self.alto, width=self.ancho, count=1,
                           dtype=dtype, crs=self.crs, transform=self.transform, nodata=nodata,
                           compress="deflate") as dst:
            dst.write(arr.astype(dtype), 1)


def reflectancia(item, a):
    """Sentinel-2 L2A: desde la línea base 04.00 (2022) hay un offset de -1000."""
    base = str(item.properties.get("s2:processing_baseline", "00.00"))
    offset = 1000 if base >= "04.00" else 0
    return np.clip((a - offset) / 10000.0, 0, None)


def nbr(b08, b12):
    with np.errstate(invalid="ignore", divide="ignore"):
        return (b08 - b12) / (b08 + b12)


def clasificar_severidad(dnbr: np.ndarray) -> np.ndarray:
    clases = np.full(dnbr.shape, 255, dtype="uint8")
    for i, (_, lo, hi) in enumerate(SEVERIDAD):
        clases[(dnbr >= lo) & (dnbr < hi)] = i
    return clases


def sembrar(quemado: np.ndarray, semillas: np.ndarray) -> np.ndarray:
    """Conserva solo los parches quemados (8-vecinos) que tocan un foco VIIRS."""
    etiquetas, _ = ndimage.label(quemado, structure=np.ones((3, 3)))
    validas = np.unique(etiquetas[semillas & quemado])
    validas = validas[validas > 0]
    return np.isin(etiquetas, validas)


# ---------------------------------------------------------------------
# programa principal
# ---------------------------------------------------------------------
def main():
    corte = dt.datetime.now().astimezone()
    resultados = {"corte": corte.strftime("%d/%m/%Y %H:%M"), "config": {
        "umbral_dnbr": UMBRAL_DNBR, "max_nubes_pre": MAX_NUBES_PRE, "max_nubes_post": MAX_NUBES_POST,
        "resolucion_m": RES, "buffer_semilla_m": BUFFER_SEMILLA_M, "dias_antes": DIAS_ANTES,
    }}

    paso(1, "Descargar focos VIIRS (7 días) y límites distritales")
    distritos = cargar_distritos()
    focos = cargar_focos(distritos)

    paso(2, "Agrupar focos en incendios (<= 1 km y <= 48 h)")
    focos["incendio"] = agrupar_incendios(focos)
    incendios = resumir_incendios(focos)
    incendios.to_csv(OUT / "incendios_7dias.csv", index=False)
    print(f"  {len(incendios)} incendios con >= {MIN_FOCOS_INCENDIO} focos. Top 10:")
    print(incendios.head(10)[["ranking", "distrito", "departamento", "focos", "huella_ha",
                              "inicio", "ultimo"]].to_string(index=False))
    resultados["peru_7d"] = {
        "focos": int(len(focos)),
        "incendios": int(len(incendios)),
        "incendios_mas_100ha": int((incendios.huella_ha > 100).sum()),
        "desde": focos["fecha_hora"].min().strftime("%d/%m/%Y"),
        "hasta": focos["fecha_hora"].max().strftime("%d/%m/%Y"),
    }
    resultados["top_incendios"] = [
        {"ranking": int(f.ranking), "distrito": str(f.distrito), "provincia": str(f.provincia),
         "departamento": str(f.departamento), "focos": int(f.focos), "huella_ha": int(f.huella_ha),
         "inicio": f.inicio.strftime("%d/%m"), "ultimo": f.ultimo.strftime("%d/%m")}
        for f in incendios.head(10).itertuples()
    ]

    paso(3, "Elegir el incendio")
    if DISTRITO_OBJETIVO:
        cand = incendios[incendios.distrito.map(normalizar).str.contains(normalizar(DISTRITO_OBJETIVO))]
        if cand.empty:
            sys.exit(f"No hay incendios con >= {MIN_FOCOS_INCENDIO} focos en '{DISTRITO_OBJETIVO}' "
                     "en los últimos 7 días. Mira outputs/incendios_7dias.csv y elige otro.")
        elegido = cand.iloc[0]
    else:
        elegido = incendios.iloc[0]
    fz = focos[focos.incendio == elegido.incendio]
    print(f"  Incendio elegido: {elegido.distrito} ({elegido.provincia}, {elegido.departamento})")
    print(f"  {elegido.focos} focos, del {elegido.inicio:%d/%m %H:%M} al {elegido.ultimo:%d/%m %H:%M} UTC, "
          f"huella VIIRS ~{fmt(elegido.huella_ha)} ha, ranking #{elegido.ranking}")
    resultados["incendio"] = {
        "distrito": elegido.distrito, "provincia": elegido.provincia, "departamento": elegido.departamento,
        "focos": int(elegido.focos), "frp_total_MW": float(elegido.frp_total_MW),
        "inicio": elegido.inicio.strftime("%d/%m/%Y"), "ultimo": elegido.ultimo.strftime("%d/%m/%Y"),
        "huella_viirs_ha": int(elegido.huella_ha), "ranking": int(elegido.ranking),
        "lon": float(elegido.lon), "lat": float(elegido.lat),
    }

    # zona de análisis en UTM (las áreas NUNCA se miden en EPSG:4326)
    epsg = epsg_utm(elegido.lon, elegido.lat)
    fz_utm = fz.to_crs(epsg)
    zona = fz_utm.geometry.buffer(BUFFER_AOI_M).union_all()
    rej = Rejilla(zona, epsg, RES)
    print(f"  Zona de análisis: {rej.ancho} x {rej.alto} píxeles de {RES} m en EPSG:{epsg}")
    bbox_ll = gpd.GeoSeries([zona], crs=epsg).to_crs(4326).total_bounds.tolist()

    paso(4, "Buscar Sentinel-2 L2A y medir nubes en la zona del incendio")
    catalogo = pystac_client.Client.open(STAC_URL, modifier=planetary_computer.sign_inplace)
    inicio = elegido.inicio.date()
    rango = f"{inicio - dt.timedelta(days=DIAS_ANTES)}/{dt.date.today()}"
    items = list(catalogo.search(collections=["sentinel-2-l2a"], bbox=bbox_ll, datetime=rango,
                                 query={"eo:cloud_cover": {"lt": 80}}).items())
    print(f"  {len(items)} escenas encontradas entre {rango.replace('/', ' y ')}")
    por_fecha: dict[dt.date, list] = {}
    for it in items:
        por_fecha.setdefault(it.datetime.date(), []).append(it)

    filas = []
    scl_cache = {}
    for fecha in sorted(por_fecha):
        scl = rej.leer(por_fecha[fecha], "SCL", Resampling.nearest)
        hay = ~np.isnan(scl)
        cobertura = 100 * hay.mean()
        nubes = 100 * np.isin(scl, SCL_NUBE).sum() / max(hay.sum(), 1)
        scl_cache[fecha] = scl
        filas.append({"fecha": fecha, "momento": "antes" if fecha < inicio else "después",
                      "cobertura_%": round(cobertura, 1), "nubes_%": round(nubes, 1)})
        print(f"   {fecha}  {filas[-1]['momento']:8s} cobertura {cobertura:5.1f}%   nubes {nubes:5.1f}%")
    escenas = pd.DataFrame(filas)
    if escenas.empty:
        sys.exit("No hay escenas Sentinel-2 para esta zona y fechas. Prueba otro incendio.")
    ok = escenas["cobertura_%"] >= MIN_COBERTURA

    antes = escenas[ok & (escenas.momento == "antes")]
    despues = escenas[ok & (escenas.momento == "después")]
    if antes.empty or despues.empty:
        sys.exit("Falta una imagen antes o después que cubra la zona. Sube DIAS_ANTES o elige otro incendio.")
    # ANTES: la más reciente con pocas nubes (si no hay, la menos nublada)
    pre_ok = antes[antes["nubes_%"] <= MAX_NUBES_PRE]
    pre = (pre_ok.iloc[-1] if not pre_ok.empty else antes.sort_values("nubes_%").iloc[0])
    # DESPUÉS: la más reciente con pocas nubes (más días = más área ya quemada)
    post_ok = despues[despues["nubes_%"] <= MAX_NUBES_POST]
    post = (post_ok.iloc[-1] if not post_ok.empty else despues.sort_values("nubes_%").iloc[0])
    escenas["elegida"] = escenas.fecha.isin([pre.fecha, post.fecha])
    escenas.to_csv(OUT / "escenas_sentinel2.csv", index=False)
    print(f"\n  ANTES:   {pre.fecha}  ({pre['nubes_%']}% nubes en la zona)")
    print(f"  DESPUÉS: {post.fecha}  ({post['nubes_%']}% nubes en la zona)")
    if pre["nubes_%"] > MAX_NUBES_PRE or post["nubes_%"] > MAX_NUBES_POST:
        print("  OJO: ninguna imagen cumple el máximo de nubes; uso la menos nublada. Repórtalo.")
    resultados["escenas"] = {
        "antes": {"fecha": pre.fecha.strftime("%d/%m/%Y"), "nubes": float(pre["nubes_%"])},
        "despues": {"fecha": post.fecha.strftime("%d/%m/%Y"), "nubes": float(post["nubes_%"])},
        "candidatas": int(len(escenas)),
    }

    paso(5, "Calcular NBR, dNBR y hectáreas quemadas")
    b = {}
    for nombre, fecha in (("pre", pre.fecha), ("post", post.fecha)):
        its = por_fecha[fecha]
        b[nombre] = {
            "B08": rej.leer(its, "B08", Resampling.average, reflectancia),
            "B12": rej.leer(its, "B12", Resampling.bilinear, reflectancia),
            "scl": scl_cache[fecha],
        }
    b["post"]["B04"] = rej.leer(por_fecha[post.fecha], "B04", Resampling.average, reflectancia)

    valido = np.isin(b["pre"]["scl"], SCL_VALIDO) & np.isin(b["post"]["scl"], SCL_VALIDO)
    dnbr = nbr(b["pre"]["B08"], b["pre"]["B12"]) - nbr(b["post"]["B08"], b["post"]["B12"])
    dnbr[~valido] = np.nan
    pct_valido = 100 * np.isfinite(dnbr).mean()
    print(f"  Píxeles válidos (sin nube en ninguna fecha): {pct_valido:.1f}% de la zona")

    quemado_umbral = np.nan_to_num(dnbr, nan=-9) >= UMBRAL_DNBR
    semillas = features.rasterize(
        [(g, 1) for g in fz_utm.geometry.buffer(BUFFER_SEMILLA_M)],
        out_shape=(rej.alto, rej.ancho), transform=rej.transform, fill=0, dtype="uint8").astype(bool)
    quemado = sembrar(quemado_umbral, semillas)
    ha_umbral = quemado_umbral.sum() * rej.ha_pixel
    ha = quemado.sum() * rej.ha_pixel
    print(f"  dNBR >= {UMBRAL_DNBR} en toda la zona:            {fmt(ha_umbral)} ha")
    print(f"  dNBR >= {UMBRAL_DNBR} sembrado con focos VIIRS:   {fmt(ha)} ha  <- cifra principal")

    sev = clasificar_severidad(dnbr)
    por_sev = []
    for i, (nombre, _, _) in enumerate(SEVERIDAD[1:], start=1):
        # baja (0,10-0,27) se cuenta solo dentro de parches conectados a focos, como referencia
        mascara = (sev == i) & (quemado if i >= 2 else ndimage.binary_dilation(quemado, iterations=2))
        por_sev.append({"severidad": nombre, "ha": round(float(mascara.sum() * rej.ha_pixel), 1)})
    for s in por_sev:
        print(f"    {s['severidad']:14s} {fmt(s['ha']):>8s} ha")

    rej.guardar(dnbr, OUT / "dnbr.tif")
    q = np.where(np.isfinite(dnbr), quemado.astype("uint8"), 255)
    rej.guardar(q, OUT / "quemado.tif", dtype="uint8", nodata=255)

    # hectáreas por distrito con zonal_stats (como en la clase de raster)
    d_utm = distritos.to_crs(epsg)  # MISMO CRS que el raster
    d_utm = d_utm[d_utm.intersects(gpd.GeoSeries([zona], crs=epsg).iloc[0])].copy()
    zs = zonal_stats(d_utm, str(OUT / "quemado.tif"), stats=["sum", "count"], nodata=255, all_touched=False)
    d_utm["ha_quemadas"] = [round((z["sum"] or 0) * rej.ha_pixel, 1) for z in zs]
    tabla_dist = (d_utm[d_utm.ha_quemadas > 0][["ubigeo", "departamento", "provincia", "distrito", "ha_quemadas"]]
                  .sort_values("ha_quemadas", ascending=False))
    tabla_dist.to_csv(OUT / "ha_por_distrito.csv", index=False)
    print(tabla_dist.to_string(index=False))

    resultados["area"] = {
        "ha_sembrado": round(float(ha), 1), "ha_solo_umbral": round(float(ha_umbral), 1),
        "pct_pixeles_validos": round(float(pct_valido), 1), "por_severidad": por_sev,
        "por_distrito": tabla_dist.drop(columns="ubigeo").to_dict("records"),
        "dnbr_medio": round(float(np.nanmean(dnbr[quemado])), 2) if quemado.any() else None,
    }

    paso(6, "¿Qué se quemó? Cruce con ESA WorldCover (10 m)")
    wc_items = list(catalogo.search(collections=["esa-worldcover"], bbox=bbox_ll).items())
    if wc_items:
        ultimo = max(wc_items, key=lambda i: i.datetime or dt.datetime.min.replace(tzinfo=dt.timezone.utc))
        anio = ultimo.datetime.year if ultimo.datetime else "?"
        mismos = [i for i in wc_items if i.datetime == ultimo.datetime]
        wc = rej.leer(mismos, "map", Resampling.nearest)
        clases, cuentas = np.unique(wc[quemado & np.isfinite(wc)], return_counts=True)
        total = cuentas.sum()
        cobertura = sorted(
            [{"clase": WORLDCOVER.get(int(c), str(int(c))), "ha": round(float(n * rej.ha_pixel), 1),
              "pct": round(float(100 * n / total), 1)} for c, n in zip(clases, cuentas)],
            key=lambda x: -x["ha"])
        for c in cobertura:
            print(f"    {c['clase']:14s} {fmt(c['ha']):>8s} ha  ({c['pct']:.1f}%)")
        pd.DataFrame(cobertura).to_csv(OUT / "ha_por_cobertura.csv", index=False)
        resultados["cobertura"] = {"anio": anio, "clases": cobertura}
    else:
        print("  No se encontró WorldCover para la zona.")
        resultados["cobertura"] = {"anio": None, "clases": []}
        wc = None

    paso(7, "Mapas y figuras")
    graficar(rej, b, sev, quemado, fz_utm, d_utm, resultados)
    mapa_interactivo(rej, quemado, fz, d_utm, resultados)

    resultados["hallazgos"] = redactar_hallazgos(resultados)
    (OUT / "resultados.json").write_text(json.dumps(resultados, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    print("\nHallazgos:")
    for h in resultados["hallazgos"]:
        print("  •", h)

    paso(8, "Presentación HTML y README")
    import presentacion
    presentacion.main()
    print("\nListo. Abre docs/index.html en el navegador y revisa README.md.")


# ---------------------------------------------------------------------
# figuras
# ---------------------------------------------------------------------
COLORES_SEV = ["#d9d9d9", "#fee391", "#fe9929", "#d95f0e", "#7f2704"]


def estirar(a, p1=2, p99=98):
    lo, hi = np.nanpercentile(a, [p1, p99])
    return np.clip((a - lo) / (hi - lo + 1e-9), 0, 1)


def graficar(rej, b, sev, quemado, fz_utm, d_utm, res):
    ext = (rej.bounds[0], rej.bounds[2], rej.bounds[1], rej.bounds[3])
    inc, esc = res["incendio"], res["escenas"]

    fig, ax = plt.subplots(1, 2, figsize=(15, 7.5), constrained_layout=True)
    rgb = np.dstack([estirar(b["post"]["B12"]), estirar(b["post"]["B08"]), estirar(b["post"]["B04"])])
    rgb = np.nan_to_num(rgb, nan=0)
    ax[0].imshow(rgb, extent=ext)
    ax[0].set_title(f"Sentinel-2 del {esc['despues']['fecha']} (falso color SWIR-NIR-Rojo)\n"
                    "lo quemado se ve marrón/rojizo", fontsize=11)
    sev_m = np.ma.masked_where((sev == 255) | (sev == 0), sev)
    ax[1].imshow(np.nan_to_num(rgb.mean(axis=2)), cmap="gray", extent=ext, alpha=0.5)
    ax[1].imshow(sev_m, cmap=ListedColormap(COLORES_SEV), norm=BoundaryNorm(np.arange(-0.5, 5.5), 5),
                 extent=ext, interpolation="nearest")
    ax[1].contour(quemado.astype(int), levels=[0.5], colors="black", linewidths=0.6, extent=ext, origin="upper")
    fz_utm.plot(ax=ax[1], color="red", markersize=6, label="Focos VIIRS")
    d_utm.boundary.plot(ax=ax[1], color="#3366aa", linewidth=0.8)
    for _, r in d_utm.iterrows():
        p = r.geometry.representative_point()
        if rej.bounds[0] < p.x < rej.bounds[2] and rej.bounds[1] < p.y < rej.bounds[3]:
            ax[1].annotate(r.distrito.title(), (p.x, p.y), fontsize=8, color="#1f3b70", ha="center")
    ax[1].set_xlim(ext[0], ext[1]); ax[1].set_ylim(ext[2], ext[3])
    ax[1].set_title(f"Severidad (dNBR) {esc['antes']['fecha']} → {esc['despues']['fecha']}\n"
                    f"contorno negro = quemado (dNBR ≥ {UMBRAL_DNBR}, sembrado): {fmt(res['area']['ha_sembrado'])} ha",
                    fontsize=11)
    ax[1].legend(handles=[Patch(color=c, label=n) for c, (n, _, _) in zip(COLORES_SEV[1:], SEVERIDAD[1:])]
                 + [plt.Line2D([], [], marker="o", color="red", ls="", label="Focos VIIRS")],
                 loc="lower left", fontsize=8, framealpha=0.9)
    for a in ax:
        a.set_xticks([]); a.set_yticks([])
    fig.suptitle(f"Incendio de {inc['distrito'].title()} ({inc['provincia'].title()}, {inc['departamento'].title()})",
                 fontsize=14, weight="bold")
    fig.savefig(DOCS / "img" / "mapa_dnbr.png", dpi=130)
    plt.close(fig)

    cob = res["cobertura"]["clases"]
    if cob:
        fig, ax = plt.subplots(figsize=(8, 4.2), constrained_layout=True)
        nombres = [c["clase"] for c in cob][::-1]
        ax.barh(nombres, [c["ha"] for c in cob][::-1], color="#b5441b")
        for i, c in enumerate(cob[::-1]):
            ax.text(c["ha"], i, f"  {fmt(c['ha'])} ha ({c['pct']:.0f}%)", va="center", fontsize=9)
        ax.set_xlabel("hectáreas quemadas")
        ax.set_title(f"¿Qué se quemó? Área quemada por cobertura (ESA WorldCover {res['cobertura']['anio']})")
        ax.spines[["top", "right"]].set_visible(False)
        ax.set_xlim(0, max(c["ha"] for c in cob) * 1.35)
        fig.savefig(DOCS / "img" / "cobertura.png", dpi=130)
        plt.close(fig)

    top = pd.DataFrame(res["top_incendios"])
    fig, ax = plt.subplots(figsize=(8, 4.2), constrained_layout=True)
    etiquetas = [f"{r.distrito.title()} ({r.departamento.title()})" for r in top.itertuples()][::-1]
    colores = ["#b5441b" if r.distrito == inc["distrito"] else "#9aa7b8" for r in top.itertuples()][::-1]
    ax.barh(etiquetas, top["focos"][::-1], color=colores)
    ax.set_xlabel("focos VIIRS (7 días)")
    ax.set_title("Los 10 incendios con más focos en el Perú, últimos 7 días")
    ax.spines[["top", "right"]].set_visible(False)
    fig.savefig(DOCS / "img" / "top_incendios.png", dpi=130)
    plt.close(fig)


def mapa_interactivo(rej, quemado, fz, d_utm, res):
    try:
        import folium
    except ImportError:
        print("  folium no está instalado; omito el mapa interactivo.")
        return
    poligonos = [g for g, v in features.shapes(quemado.astype("uint8"), mask=quemado, transform=rej.transform) if v == 1]
    m = folium.Map(location=[res["incendio"]["lat"], res["incendio"]["lon"]], zoom_start=12, tiles=None)
    folium.TileLayer("https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
                     attr="Esri World Imagery", name="Satélite").add_to(m)
    folium.TileLayer("OpenStreetMap", name="Calles").add_to(m)
    folium.GeoJson(d_utm.to_crs(4326)[["distrito", "provincia", "geometry"]], name="Distritos",
                   style_function=lambda _: {"color": "#3366aa", "weight": 1.5, "fillOpacity": 0},
                   tooltip=folium.GeoJsonTooltip(["distrito", "provincia"])).add_to(m)
    if poligonos:
        from shapely.geometry import shape
        q = gpd.GeoSeries([shape(p) for p in poligonos], crs=rej.crs).union_all().simplify(RES)
        folium.GeoJson(gpd.GeoSeries([q], crs=rej.crs).to_crs(4326).__geo_interface__,
                       name=f"Área quemada ({fmt(res['area']['ha_sembrado'])} ha)",
                       style_function=lambda _: {"color": "#7f2704", "weight": 1, "fillColor": "#d95f0e",
                                                 "fillOpacity": 0.55}).add_to(m)
    capa = folium.FeatureGroup(name="Focos VIIRS").add_to(m)
    for _, r in fz.iterrows():
        folium.CircleMarker([r.latitude, r.longitude], radius=3, color="red", fill=True, fill_opacity=0.9,
                            tooltip=f"{r.fecha_hora:%d/%m %H:%M} UTC · {r.fuente} · FRP {r.frp} MW").add_to(capa)
    folium.LayerControl(collapsed=False).add_to(m)
    m.save(DOCS / "mapa.html")


# ---------------------------------------------------------------------
# hallazgos con números
# ---------------------------------------------------------------------
def redactar_hallazgos(r) -> list[str]:
    inc, esc, area = r["incendio"], r["escenas"], r["area"]
    h = []
    lugar = f"{inc['distrito'].title()} ({inc['provincia'].title()}, {inc['departamento'].title()})"
    h.append(
        f"El incendio de {lugar} quemó unas {fmt(area['ha_sembrado'])} ha entre el {esc['antes']['fecha']} y el "
        f"{esc['despues']['fecha']} (dNBR ≥ {UMBRAL_DNBR}, parches conectados a {inc['focos']} focos VIIRS). "
        f"Solo con el umbral, sin exigir focos cerca, saldrían {fmt(area['ha_solo_umbral'])} ha: "
        "la decisión de método cambia el número."
    )
    cob = r["cobertura"]["clases"]
    if cob:
        veg = {c["clase"]: c for c in cob}
        partes = [f"{c['pct']:.0f}% {c['clase'].lower()}" for c in cob[:3]]
        texto = f"Lo que se quemó fue sobre todo {', '.join(partes)} (WorldCover {r['cobertura']['anio']})"
        if "Cultivo" in veg:
            texto += f"; {fmt(veg['Cultivo']['ha'])} ha eran cultivos, una pérdida directa para los productores"
        h.append(texto + ".")
    sev = {s["severidad"]: s["ha"] for s in area["por_severidad"]}
    alta = sev.get("Moderada-alta", 0) + sev.get("Alta", 0)
    pct_alta = 100 * alta / area["ha_sembrado"] if area["ha_sembrado"] else 0
    p = r["peru_7d"]
    h.append(
        f"{pct_alta:.0f}% del área quemada tiene severidad moderada-alta o alta ({fmt(alta)} ha). "
        f"En el Perú hubo {fmt(p['focos'])} focos VIIRS del {p['desde']} al {p['hasta']}, agrupados en "
        f"{p['incendios']} incendios ({p['incendios_mas_100ha']} con huella > 100 ha); este es el #{inc['ranking']} por número de focos."
    )
    if len(area["por_distrito"]) > 1:
        d = area["por_distrito"]
        h.append("El fuego cruzó límites distritales: " + "; ".join(
            f"{x['distrito'].title()} {fmt(x['ha_quemadas'])} ha" for x in d[:4]) + ".")
    return h


if __name__ == "__main__":
    main()
