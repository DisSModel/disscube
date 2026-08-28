"""
examples/case_studies/brmangue_dominio/04_serie_temporal_mangue.py

BR-MANGUE — série temporal binária mangue/não-mangue (MapBiomas).

Traz para o cubo a presença de mangue ano a ano, como variável TEMPORAL:
cada ano é uma fatia, e `load()` devolve `(time, y, x)`.

O que este exemplo mostra, além do 01/02
----------------------------------------
Os exemplos anteriores derivam variáveis estáticas. Aqui a mesma variável
existe em várias fatias de tempo, o que muda três coisas:

  * cada ano é um `SpatialSource` com `time=<ano>` e uma derivação com
    `valid_from`/`valid_until` — é isso que faz o catálogo tratá-los como
    fatias de uma série, e não como variáveis diferentes;
  * `tile_layout()` exige `time=<ano>`, porque cada fatia repete as mesmas
    posições de tile — sem escolher uma, o layout descreveria cada célula
    várias vezes;
  * o loop chama `gc.collect()` (ver nota no fim do arquivo).

Estrutura da fonte
------------------
Os produtos `mangue_*` do MapBiomas trazem uma DÉCADA por arquivo, com um
ano por banda (`mangue_1985` ... `mangue_1994`). Como `write_vrt` mosaica
uma banda por chamada, cada ano vira um VRT próprio.

Pré-requisitos:
  - pip install geomosaic
  - tiles em $BRMANGUE_ENTRADA/mapbiomas_historico/

Usage:
    export BRMANGUE_ENTRADA=/caminho/para/pymangue/dados/entrada
    python examples/case_studies/brmangue_dominio/04_serie_temporal_mangue.py
"""

import gc
import os
import time as _time
from pathlib import Path

import numpy as np
import rasterio

from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable

try:
    from geomosaic.core import build_mosaic_contract, write_vrt
except ImportError as exc:  # pragma: no cover — exemplo, fora do pacote
    raise SystemExit(
        "Este exemplo precisa do geomosaic:  pip install geomosaic"
    ) from exc


# ── Configuração ──────────────────────────────────────────────────────────────
ENTRADA   = Path(os.environ.get("BRMANGUE_ENTRADA", "./dados/entrada"))
MB_DIR    = ENTRADA / "mapbiomas_historico"

CATALOGO  = "catalog.db"
STORE     = "./data/"
VRT_DIR   = Path("./data/raw/brmangue_serie")

GRID_ID   = "brmangue/30m"
VARIAVEL  = "mangue"
TILE      = 4096

# Exportação para conferência: um GeoTIFF com um ano por banda. Não é
# necessário para nada no cubo — serve para abrir no QGIS e ver que as fatias
# estão certas, já que Zarr multi-tile não é diretamente visualizável.
SAIDA_TIF    = Path("./data/brmangue_serie_mangue.tif")
NODATA_SAIDA = 255

# Cinco anos-marco, um por década. Trocar por range(1985, 2025) traz a série
# completa — 40 anos x ~28 tiles, o que leva bem mais tempo.
ANOS = [1985, 1995, 2005, 2015, 2024]

# Em qual produto (década) e banda cada ano está.
DECADAS = [
    ("mangue_1985_1994", 1985),
    ("mangue_1995_2004", 1995),
    ("mangue_2005_2014", 2005),
    ("mangue_2015_2024", 2015),
]


def _produto_e_banda(ano: int) -> tuple[str, int]:
    """Década que contém o ano, e a banda dele dentro dela (1-based)."""
    for produto, inicio in DECADAS:
        if inicio <= ano <= inicio + 9:
            return produto, ano - inicio + 1
    raise ValueError(f"Ano {ano} fora da cobertura {DECADAS[0][1]}–{DECADAS[-1][1]+9}")


def _mascara_valida(vrt: Path, largura_alvo: int = 2048):
    """Onde a fonte tem dado, em baixa resolução — ver nota no exemplo 01."""
    from scipy.ndimage import binary_dilation

    with rasterio.open(vrt) as ds:
        fator = max(1, int(np.ceil(ds.width / largura_alvo)))
        baixa = ds.read(
            1,
            out_shape=(max(1, ds.height // fator), max(1, ds.width // fator)),
            resampling=rasterio.enums.Resampling.nearest,
        )
        nodata = ds.nodata

    valida = np.isfinite(baixa)
    if nodata is not None and np.isfinite(nodata):
        valida &= baixa != nodata
    return binary_dilation(valida), fator


def _exportar_tif(cube, grid, anos: list[int]) -> None:
    """Escreve um GeoTIFF com uma banda por ano, a partir dos Zarr do cubo.

    Vai janela a janela, montando cada banda dos tiles daquela fatia — o Zarr
    de uma variável multi-tile não é um arquivo só, então não há conversão
    direta. (`tools/zarr_to_tif.py` converte UM store inteiro, o que não
    cobre este caso.)
    """
    import zarr

    perfil = dict(
        driver="GTiff", height=grid.rows, width=grid.cols, count=len(anos),
        dtype="uint8", nodata=NODATA_SAIDA, crs=grid.crs,
        transform=grid.transform,
        tiled=True, blockxsize=512, blockysize=512,
        compress="deflate", zlevel=6, bigtiff="IF_SAFER", sparse_ok=True,
    )
    SAIDA_TIF.parent.mkdir(parents=True, exist_ok=True)

    with rasterio.open(SAIDA_TIF, "w", **perfil) as dst:
        for banda, ano in enumerate(anos, start=1):
            dst.set_band_description(banda, f"{VARIAVEL}_{ano}")
            for t in cube.tile_layout(VARIAVEL, GRID_ID, time=ano):
                arr = np.asarray(zarr.open(t["url"], mode="r")[t["variable"]])
                dst.write(
                    np.where(np.isnan(arr), NODATA_SAIDA, arr).astype(np.uint8),
                    banda,
                    window=rasterio.windows.Window(
                        t["col_off"], t["row_off"], t["width"], t["height"]
                    ),
                )
            print(f"      banda {banda}: {VARIAVEL}_{ano}")


def main() -> None:
    if not MB_DIR.is_dir():
        raise SystemExit(
            f"Diretório não encontrado: {MB_DIR}\n"
            "Defina BRMANGUE_ENTRADA apontando para .../pymangue/dados/entrada"
        )

    t0 = _time.perf_counter()
    VRT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. geomosaic: um contrato por década, um VRT por ano ────────────────
    print(f"\n[1/4] geomosaic: {len(ANOS)} anos")
    contratos: dict[str, object] = {}
    vrts: dict[int, Path] = {}
    for ano in ANOS:
        produto, banda = _produto_e_banda(ano)
        if produto not in contratos:
            tiles = sorted(str(p) for p in MB_DIR.glob(f"*{produto}_serie*.tif"))
            if not tiles:
                raise SystemExit(f"Nenhum tile de {produto} em {MB_DIR}")
            contratos[produto] = build_mosaic_contract(tiles)
            c = contratos[produto]
            print(f"      {produto}: {len(tiles)} tiles -> "
                  f"{c.mosaic_height}x{c.mosaic_width}")
        vrts[ano] = write_vrt(
            contratos[produto], str(VRT_DIR / f"{VARIAVEL}_{ano}.vrt"), band=banda
        )

    formas = {(c.mosaic_height, c.mosaic_width) for c in contratos.values()}
    if len(formas) > 1:
        raise SystemExit(f"As décadas têm extensões diferentes: {formas}")

    # ── 2. grade e fontes ───────────────────────────────────────────────────
    print("\n[2/4] disscube: grade e fontes")
    cube = CubeClient(catalog=CATALOGO, store=STORE)
    contrato = next(iter(contratos.values()))
    a, _b, ox, _d, e, oy = contrato.mosaic_transform
    bbox = [ox, oy - contrato.mosaic_height * abs(e),
            ox + contrato.mosaic_width * a, oy]

    grid = cube.catalog.get_grid(GRID_ID)
    if grid is None:
        grid = GridSpec(id=GRID_ID, type="reference", crs=str(contrato.crs),
                        resolution=a, bbox=bbox)
        cube.register_grid(grid)
        print(f"      grade {GRID_ID} registrada: {grid.rows}x{grid.cols}")
    else:
        if not (np.allclose(grid.bbox, bbox) and grid.resolution == a):
            raise SystemExit(
                f"A grade {GRID_ID} já existe com outra extensão. "
                "Use outro GRID_ID para esta série."
            )
        print(f"      grade {GRID_ID} reutilizada: {grid.rows}x{grid.cols}")

    # time=<ano> no SpatialSource é o que marca a fatia temporal.
    for ano, vrt in vrts.items():
        cube.register_spatial_source(SpatialSource(
            id=f"{VARIAVEL}_{ano}", name=f"MapBiomas mangue {ano}",
            format="raster", asset_url=str(vrt), crs=str(contrato.crs), time=ano,
        ))
    print(f"      {len(vrts)} fontes registradas, uma por ano")

    # ── 3. tiles com dado + derivação por ano ───────────────────────────────
    print("\n[3/4] derive por tile")
    mascara, fator = _mascara_valida(vrts[ANOS[0]])
    tiles_com_dado = []
    for r0 in range(0, grid.rows, TILE):
        for c0 in range(0, grid.cols, TILE):
            h, w = min(TILE, grid.rows - r0), min(TILE, grid.cols - c0)
            sub = mascara[r0 // fator:-(-(r0 + h) // fator),
                          c0 // fator:-(-(c0 + w) // fator)]
            if not sub.any():
                continue
            tile_id = f"R{r0:05d}C{c0:05d}"
            minx, maxy = ox + c0 * a, oy - r0 * abs(e)
            cube.register_spatial_source(SpatialSource(
                id=f"{GRID_ID}_{tile_id}", name=tile_id, format="raster",
                asset_url=str(vrts[ANOS[0]]), crs=str(contrato.crs),
                bbox=[minx, maxy - h * abs(e), minx + w * a, maxy],
            ))
            tiles_com_dado.append(tile_id)
    print(f"      {len(tiles_com_dado)} tiles com dado válido")

    for ano in ANOS:
        t = _time.perf_counter()
        derivacao = SpatialDerivation(
            source_id=f"{VARIAVEL}_{ano}", grid_id=GRID_ID, role="land_use",
            variables=[Variable(name=VARIAVEL, operator="majority")],
            valid_from=str(ano), valid_until=str(ano),
        )
        for tile_id in tiles_com_dado:
            cube.derive(derivacao, tile_id=tile_id)
            # Ver a nota no fim do arquivo: sem isto, a memória cresce a cada
            # derive e um loop longo acumula vários GB.
            gc.collect()
        print(f"      {ano}: {len(tiles_com_dado)} tiles em "
              f"{_time.perf_counter() - t:.1f}s")

    # ── 4. o que ficou no catálogo ──────────────────────────────────────────
    print("\n[4/5] catálogo")
    derivados = [d for d in cube.search(grid=GRID_ID) if d.name == VARIAVEL]
    fatias = sorted({t for d in derivados for t in (d.times or [])})
    print(f"      {len(derivados)} pedaços em {len(fatias)} fatias: {fatias}")

    # tile_layout() precisa da fatia: cada ano repete as mesmas posições.
    for ano in ANOS:
        layout = cube.tile_layout(VARIAVEL, GRID_ID, time=ano)
        posicoes = {(t["row_off"], t["col_off"]) for t in layout}
        print(f"      {ano}: {len(layout)} pedaços em {len(posicoes)} posições")

    # ── 5. exportação para conferência visual ───────────────────────────────
    print(f"\n[5/5] GeoTIFF multibanda -> {SAIDA_TIF}")
    _exportar_tif(cube, grid, ANOS)

    print(f"\n=== série no cubo em {_time.perf_counter() - t0:.1f}s ===")
    print(f"    {SAIDA_TIF} — {len(ANOS)} bandas, uma por ano, para abrir no QGIS")
    print(f"    Para carregar um ano num workspace do haloexec:")
    print(f"        tiles = cube.tile_layout({VARIAVEL!r}, {GRID_ID!r}, time={ANOS[0]})")
    print(f"        load_zarr_tiles_into_workspace(ws, tiles)")
    print(f"    Para o backend em RAM do DisSModel:")
    print(f"        cube.to_lucc_data([{VARIAVEL!r}], grid_id={GRID_ID!r})")


# ── Nota: por que gc.collect() no loop ────────────────────────────────────────
# Cada derive() deixa ~50 objetos presos em ciclos de referência, retendo
# ~380 MB. Contagem de referências não desfaz ciclo — só o coletor geracional
# desfaz, e ele quase nunca dispara aqui: o gatilho é o NÚMERO de alocações, e
# o numpy concentra centenas de MB em pouquíssimos objetos, então o coletor não
# enxerga a pressão de memória.
#
# Medido em 28 tiles seguidos: sem collect, o RSS cresce ~380 MB por tile e
# passa de 7 GB; com collect, fica estável em ~0,35 GB. O custo é ~0,05 s por
# chamada contra ~0,7 s do derive — cerca de 7%.
#
# A chamada está aqui, e não dentro do disscube, porque impor esse custo a todo
# derive() é decisão do pacote, não deste exemplo.

if __name__ == "__main__":
    main()
