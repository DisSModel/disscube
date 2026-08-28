"""
examples/case_studies/brmangue_dominio/05_serie_temporal_bdc.py

BR-MANGUE — a série temporal mangue/não-mangue sobre a malha BDC (Albers).

Combina o que os dois exemplos anteriores fazem separadamente:

    04_  série temporal, malha ad hoc, grade EPSG:5880 (a da fonte)
    05_  série temporal, malha BDC_SM, grade em BDC Albers  ← este

A diferença não é cosmética. Em `04` a fonte já está na grade alvo, então o
GridAligner usa o caminho de identidade — leitura janelada, sem reamostrar.
Aqui cada tile passa por uma reprojeção real (EPSG:5880 → BDC Albers), o que
custa tempo e memória, e reamostra: o resultado não é bit a bit igual à fonte.

Quando usar cada um
-------------------
Use a malha BDC quando os derivados forem virar patrimônio do cubo — os tiles
têm significado fora do script e alinham com o resto do ecossistema BDC. Use a
ad hoc quando forem passo intermediário: é mais leve e preserva a fonte.

Pré-requisitos:
  - pip install geomosaic
  - pip install "disscube[bdc]"          (fiona, para ler os shapefiles BDC)
  - grades BDC em data/bdc_grids/BDC_{SM,MD,LG}_V2.zip
  - tiles em $BRMANGUE_ENTRADA/mapbiomas_historico/

Usage:
    export BRMANGUE_ENTRADA=/caminho/para/pymangue/dados/entrada
    python examples/case_studies/brmangue_dominio/05_serie_temporal_bdc.py
"""

import gc
import os
import time as _time
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS as ProjCRS, Transformer
from shapely.geometry import MultiPoint, shape

from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable
from disscube.utils.grids import BDC_CRS

try:
    from geomosaic.core import build_mosaic_contract, write_vrt
except ImportError as exc:  # pragma: no cover — exemplo, fora do pacote
    raise SystemExit(
        "Este exemplo precisa do geomosaic:  pip install geomosaic"
    ) from exc

try:
    import fiona
except ImportError as exc:  # pragma: no cover
    raise SystemExit(
        'Este exemplo precisa do fiona:  pip install "disscube[bdc]"'
    ) from exc


# ── Configuração ──────────────────────────────────────────────────────────────
ENTRADA   = Path(os.environ.get("BRMANGUE_ENTRADA", "./dados/entrada"))
MB_DIR    = ENTRADA / "mapbiomas_historico"
BDC_SM    = "zip://data/bdc_grids/BDC_SM_V2.zip"

CATALOGO  = "catalog.db"
STORE     = "./data/"
VRT_DIR   = Path("./data/raw/brmangue_serie")
SAIDA_TIF = Path("./data/brmangue_serie_mangue_bdc.tif")

GRID_ID      = "brmangue/30m_bdc"
VARIAVEL     = "mangue"
RESOLUCAO    = 30.0
NODATA_SAIDA = 255

ANOS = [1985, 1995, 2005, 2015, 2024]

DECADAS = [
    ("mangue_1985_1994", 1985),
    ("mangue_1995_2004", 1995),
    ("mangue_2005_2014", 2005),
    ("mangue_2015_2024", 2015),
]


def _produto_e_banda(ano: int) -> tuple[str, int]:
    for produto, inicio in DECADAS:
        if inicio <= ano <= inicio + 9:
            return produto, ano - inicio + 1
    raise ValueError(f"Ano {ano} fora da cobertura")


def _mascara_valida(vrt: Path, largura_alvo: int = 2048):
    from scipy.ndimage import binary_dilation

    with rasterio.open(vrt) as ds:
        fator = max(1, int(np.ceil(ds.width / largura_alvo)))
        baixa = ds.read(
            1,
            out_shape=(max(1, ds.height // fator), max(1, ds.width // fator)),
            resampling=rasterio.enums.Resampling.nearest,
        )
        nodata, transform = ds.nodata, ds.transform
        resolucao = abs(transform.a) * (ds.width / baixa.shape[1])

    valida = np.isfinite(baixa)
    if nodata is not None and np.isfinite(nodata):
        valida &= baixa != nodata
    return binary_dilation(valida), transform, resolucao


def _tem_dado(bounds, mascara, transform, resolucao, para_fonte) -> bool:
    minx, miny, maxx, maxy = bounds
    xs, ys = para_fonte.transform([minx, minx, maxx, maxx], [miny, maxy, miny, maxy])
    c0 = int(np.floor((min(xs) - transform.c) / resolucao))
    c1 = int(np.ceil((max(xs) - transform.c) / resolucao))
    r0 = int(np.floor((transform.f - max(ys)) / resolucao))
    r1 = int(np.ceil((transform.f - min(ys)) / resolucao))
    h, w = mascara.shape
    r0, r1 = max(0, r0), min(h, r1)
    c0, c1 = max(0, c0), min(w, c1)
    if r0 >= r1 or c0 >= c1:
        return False
    return bool(mascara[r0:r1, c0:c1].any())


BLOCO_TIF = 512


def _exportar_tif(cube, grid, anos: list[int]) -> None:
    """GeoTIFF com uma banda por ano, montado dos Zarr — conferência visual.

    Percorre os BLOCOS do GeoTIFF, não os tiles, e é preciso que seja assim.
    Os tiles BDC são 3520x3520, que não é múltiplo do bloco de 512, então cada
    borda de tile cai no meio de um bloco. Escrevendo tile a tile, a parte não
    coberta desses blocos de borda fica com o preenchimento padrão do GDAL —
    zero, não o nodata declarado — e zero é um valor VÁLIDO aqui (não-mangue).
    O resultado seriam milhões de células fora da área de estudo aparecendo
    como "não-mangue" observado, o que é dado inventado.

    Montando cada bloco a partir dos tiles que o cobrem, com nodata no resto,
    o problema não existe. (Nos exemplos 01/02 os tiles são 4096, múltiplo de
    512, e por isso a questão nunca apareceu lá.)
    """
    import zarr

    perfil = dict(
        driver="GTiff", height=grid.rows, width=grid.cols, count=len(anos),
        dtype="uint8", nodata=NODATA_SAIDA, crs=grid.crs, transform=grid.transform,
        tiled=True, blockxsize=BLOCO_TIF, blockysize=BLOCO_TIF,
        compress="deflate", zlevel=6, bigtiff="IF_SAFER", sparse_ok=True,
    )
    SAIDA_TIF.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(SAIDA_TIF, "w", **perfil) as dst:
        for banda, ano in enumerate(anos, start=1):
            dst.set_band_description(banda, f"{VARIAVEL}_{ano}")
            tiles = cube.tile_layout(VARIAVEL, GRID_ID, time=ano)
            abertos = {t["tile_id"]: zarr.open(t["url"], mode="r")[t["variable"]]
                       for t in tiles}

            for r0 in range(0, grid.rows, BLOCO_TIF):
                r1 = min(r0 + BLOCO_TIF, grid.rows)
                for c0 in range(0, grid.cols, BLOCO_TIF):
                    c1 = min(c0 + BLOCO_TIF, grid.cols)
                    buf = None
                    for t in tiles:
                        tr0 = max(r0, t["row_off"]); tr1 = min(r1, t["row_off"] + t["height"])
                        tc0 = max(c0, t["col_off"]); tc1 = min(c1, t["col_off"] + t["width"])
                        if tr0 >= tr1 or tc0 >= tc1:
                            continue
                        if buf is None:
                            buf = np.full((r1 - r0, c1 - c0), NODATA_SAIDA, np.uint8)
                        trecho = np.asarray(abertos[t["tile_id"]][
                            tr0 - t["row_off"]:tr1 - t["row_off"],
                            tc0 - t["col_off"]:tc1 - t["col_off"],
                        ])
                        buf[tr0 - r0:tr1 - r0, tc0 - c0:tc1 - c0] = np.where(
                            np.isnan(trecho), NODATA_SAIDA, trecho
                        ).astype(np.uint8)
                    if buf is not None:
                        dst.write(buf, banda,
                                  window=rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0))
            print(f"      banda {banda}: {VARIAVEL}_{ano}")


def main() -> None:
    if not MB_DIR.is_dir():
        raise SystemExit(
            f"Diretório não encontrado: {MB_DIR}\n"
            "Defina BRMANGUE_ENTRADA apontando para .../pymangue/dados/entrada"
        )

    t0 = _time.perf_counter()
    VRT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. geomosaic: um VRT por ano, na projeção nativa da fonte ───────────
    print(f"\n[1/5] geomosaic: {len(ANOS)} anos")
    contratos: dict[str, object] = {}
    vrts: dict[int, Path] = {}
    for ano in ANOS:
        produto, banda = _produto_e_banda(ano)
        if produto not in contratos:
            tiles = sorted(str(p) for p in MB_DIR.glob(f"*{produto}_serie*.tif"))
            if not tiles:
                raise SystemExit(f"Nenhum tile de {produto} em {MB_DIR}")
            contratos[produto] = build_mosaic_contract(tiles)
        vrts[ano] = write_vrt(
            contratos[produto], str(VRT_DIR / f"{VARIAVEL}_{ano}.vrt"), band=banda
        )
    contrato = next(iter(contratos.values()))
    print(f"      fonte: {contrato.mosaic_height}x{contrato.mosaic_width} "
          f"@ {contrato.crs}")

    # ── 2. selecionar os tiles BDC com dado ─────────────────────────────────
    print("[2/5] BDC: selecionando tiles SM com dado válido")
    a, _b, ox, _d, e, oy = contrato.mosaic_transform
    fonte_bbox = (ox, oy - contrato.mosaic_height * abs(e),
                  ox + contrato.mosaic_width * a, oy)

    para_bdc = Transformer.from_crs(
        ProjCRS.from_user_input(str(contrato.crs)),
        ProjCRS.from_user_input(BDC_CRS), always_xy=True,
    )
    # Footprint densificado antes de projetar: só os quatro cantos dariam uma
    # envoltória maior que a área real, porque as bordas viram curvas.
    passo, contorno = 200, []
    x0, y0, x1, y1 = fonte_bbox
    for i in range(passo + 1):
        f = i / passo
        contorno += [(x0 + (x1 - x0) * f, y0), (x0 + (x1 - x0) * f, y1),
                     (x0, y0 + (y1 - y0) * f), (x1, y0 + (y1 - y0) * f)]
    px, py = para_bdc.transform([p[0] for p in contorno], [p[1] for p in contorno])
    alvo = MultiPoint(list(zip(px, py))).convex_hull

    candidatos = []
    with fiona.open(BDC_SM) as src:
        for rec in src:
            geom = shape(rec["geometry"])
            if geom.intersects(alvo):
                candidatos.append((rec["properties"]["tile"], geom.bounds))
    candidatos.sort()

    mascara, m_transform, m_res = _mascara_valida(vrts[ANOS[0]])
    para_fonte = Transformer.from_crs(
        ProjCRS.from_user_input(BDC_CRS),
        ProjCRS.from_user_input(str(contrato.crs)), always_xy=True,
    )
    selecionados = [
        (tile, bounds) for tile, bounds in candidatos
        if _tem_dado(bounds, mascara, m_transform, m_res, para_fonte)
    ]
    if not selecionados:
        raise SystemExit("Nenhum tile BDC_SM contém dado válido.")
    print(f"      {len(candidatos)} intersectam a extensão, "
          f"{len(selecionados)} têm dado válido")

    out_minx = min(b[0] for _t, b in selecionados)
    out_miny = min(b[1] for _t, b in selecionados)
    out_maxx = max(b[2] for _t, b in selecionados)
    out_maxy = max(b[3] for _t, b in selecionados)

    # ── 3. grade BDC + fontes ───────────────────────────────────────────────
    print("[3/5] disscube: grade Albers, fontes e tiles")
    cube = CubeClient(catalog=CATALOGO, store=STORE)
    bbox = [out_minx, out_miny, out_maxx, out_maxy]

    grid = cube.catalog.get_grid(GRID_ID)
    if grid is None:
        grid = GridSpec(id=GRID_ID, type="reference", crs=BDC_CRS,
                        resolution=RESOLUCAO, bbox=bbox,
                        description="BR-MANGUE 30 m sobre a malha BDC (Albers)")
        cube.register_grid(grid)
        print(f"      grade {GRID_ID} registrada: {grid.rows}x{grid.cols}")
    else:
        if not np.allclose(grid.bbox, bbox):
            raise SystemExit(
                f"A grade {GRID_ID} já existe com outra extensão. "
                "Use outro GRID_ID para esta série."
            )
        print(f"      grade {GRID_ID} reutilizada: {grid.rows}x{grid.cols}")

    for ano, vrt in vrts.items():
        cube.register_spatial_source(SpatialSource(
            id=f"{VARIAVEL}_{ano}", name=f"MapBiomas mangue {ano}",
            format="raster", asset_url=str(vrt), crs=str(contrato.crs), time=ano,
        ))
    for tile, bounds in selecionados:
        cube.register_spatial_source(SpatialSource(
            id=f"BDC_SM_{tile}", name=f"BDC SM Tile {tile}", format="raster",
            asset_url="planned", crs=BDC_CRS, bbox=list(bounds),
        ))
    print(f"      {len(vrts)} fontes + {len(selecionados)} tiles BDC")

    # ── 4. derive por tile BDC, por ano ─────────────────────────────────────
    print("[4/5] derive por tile (COM reprojeção 5880 -> Albers)")
    for ano in ANOS:
        t = _time.perf_counter()
        derivacao = SpatialDerivation(
            source_id=f"{VARIAVEL}_{ano}", grid_id=GRID_ID, role="land_use",
            variables=[Variable(name=VARIAVEL, operator="majority")],
            valid_from=str(ano), valid_until=str(ano),
        )
        for tile, _bounds in selecionados:
            # id completo: ids simples não são únicos entre níveis BDC.
            cube.derive(derivacao, tile_id=f"BDC_SM_{tile}")
            # Sem isto a memória cresce a cada derive — ver a nota no exemplo 04.
            gc.collect()
        print(f"      {ano}: {len(selecionados)} tiles em "
              f"{_time.perf_counter() - t:.1f}s")

    # ── 5. exportação para conferência ──────────────────────────────────────
    print(f"[5/5] GeoTIFF multibanda -> {SAIDA_TIF}")
    _exportar_tif(cube, grid, ANOS)

    derivados = [d for d in cube.search(grid=GRID_ID) if d.name == VARIAVEL]
    print(f"\n=== série na malha BDC em {_time.perf_counter() - t0:.1f}s ===")
    print(f"    {len(derivados)} pedaços, {len(ANOS)} fatias, grade {grid.rows}x{grid.cols}")
    print(f"    {SAIDA_TIF} — {len(ANOS)} bandas em Albers")
    print(f"\n    A reprojeção reamostra: as contagens por classe ficam ~1% abaixo")
    print(f"    das do exemplo 04 (EPSG:5880), que não reamostra. É o custo de")
    print(f"    estar numa malha canônica, não um erro.")


if __name__ == "__main__":
    main()
