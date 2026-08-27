"""
examples/case_studies/brmangue_dominio/01_dominio_estrutural.py

BR-MANGUE — domínio estrutural nacional (30 m, EPSG:5880).

Mostra o caminho completo para transformar tiles brutos num raster
categórico de domínio, usando o operador genérico `reclassify`:

    geomosaic   tiles ANADEM  -> VRT (uma banda por chamada)
    disscube    brm_state_2024 -> papel, tile a tile (derive(tile_id=...))
    rasterio    escrita janelada do GeoTIFF de 2 bandas

O que é genérico e o que é do projeto
-------------------------------------
Do disscube, nada aqui é específico de manguezal: `reclassify` só aplica
uma tabela `{valor_origem: valor_destino}` que chega como dado. O que é
BR-MANGUE são as ~10 linhas de PAPEIS_ESTADOS e o acoplamento
papel/elevação no fim do loop — ambos vivem neste exemplo, não no pacote.

Por que tile a tile
-------------------
Um derive() único na extensão completa (18352x21350 = 391M células)
alocaria dezenas de GB e derruba máquinas modestas. `derive(tile_id=...)`
processa um recorte por vez com memória limitada — ver
docs/architecture/tiling.md.

Por que a escrita é feita aqui
------------------------------
`tools/zarr_to_tif.py` converte UM Zarr inteiro de uma vez; para remontar
N tiles num único GeoTIFF multibanda é preciso escrever janela a janela,
o que é feito no loop abaixo.

Pré-requisitos
--------------
  - pip install geomosaic     (não é dependência do disscube)
  - tiles ANADEM v2 em $BRMANGUE_ENTRADA/anadem_v2/
    (3 bandas: elevation_m, lulc_mb_2024, brm_state_2024)

Usage:
    export BRMANGUE_ENTRADA=/caminho/para/pymangue/dados/entrada
    python examples/case_studies/brmangue_dominio/01_dominio_estrutural.py
"""

import os
import time
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from disscube.client import CubeClient
from disscube.models import GridSpec, SpatialSource, SpatialDerivation, Variable

try:
    from geomosaic.core import build_mosaic_contract, write_vrt
except ImportError as exc:  # pragma: no cover — exemplo, não faz parte do pacote
    raise SystemExit(
        "Este exemplo precisa do geomosaic (não é dependência do disscube):\n"
        "    pip install geomosaic\n"
        "ou, a partir do repositório irmão:\n"
        "    pip install -e ../geomosaic"
    ) from exc


# ── Configuração ──────────────────────────────────────────────────────────────
ENTRADA   = Path(os.environ.get("BRMANGUE_ENTRADA", "./dados/entrada"))
TILES_DIR = ENTRADA / "anadem_v2"

# Catálogo e store compartilhados, como em todos os demais exemplos: os
# derivados entram no MESMO cubo e aparecem em cube.search() junto com as
# outras variáveis — que é o ponto de catalogá-los.
CATALOGO  = "catalog.db"
STORE     = "./data/"
VRT_DIR   = Path("./data/raw/brmangue")
SAIDA     = Path("./data/brmangue_dominio_2024.tif")

GRID_ID       = "brmangue/30m"
TILE          = 4096          # lado do recorte processado por vez
BANDA_ESTADO  = 3             # brm_state_2024
BANDA_ELEV    = 1             # elevation_m
NODATA_SAIDA  = 255
NODATA_ELEV   = -9999.0

# ── Específico do BR-MANGUE — dado do projeto, não do disscube ────────────────
# Agrupa a legenda de estados 0–12 nos seis papéis do modelo.
PAPEIS_ESTADOS = {
    "excluidos":                ([0, 12],           0),
    "mangue_inicial":           ([1],               1),
    "acomodacao_natural":       ([2, 3, 4, 5, 6],   2),
    "uso_manejado_candidato":   ([7],               3),
    "restricao_ou_barreira":    ([8, 9, 10],        4),
    "agua_aberta_nao_validada": ([11],              5),
}
MAPPING = {c: papel for _, (codes, papel) in PAPEIS_ESTADOS.items() for c in codes}


def _mascara_valida(vrt: Path, largura_alvo: int = 2048):
    """
    Máscara booleana, em baixa resolução, de onde a fonte tem dado válido,
    junto com o fator de decimação usado.

    Uma única leitura decimada — a memória fica limitada por ``largura_alvo``,
    não pelo tamanho da fonte. A máscara é dilatada em uma célula porque a
    decimação por vizinho mais próximo pode perder feições finas (a faixa de
    mangue tem poucos pixels de largura em muitos trechos), e aqui um falso
    positivo custa um recorte derivado à toa enquanto um falso negativo custa
    dado faltando no produto final.
    """
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


def main() -> None:
    if not TILES_DIR.is_dir():
        raise RuntimeError(
            f"Diretório de tiles não encontrado: {TILES_DIR}\n"
            "Defina BRMANGUE_ENTRADA apontando para .../pymangue/dados/entrada"
        )
    tiles = sorted(TILES_DIR.glob("*.tif"))
    if not tiles:
        raise RuntimeError(f"Nenhum .tif em {TILES_DIR}")

    t0 = time.perf_counter()
    VRT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. geomosaic: contrato + um VRT por banda ────────────────────────────
    print(f"\n[1/3] geomosaic: {len(tiles)} tiles -> contrato")
    contract = build_mosaic_contract([str(t) for t in tiles])
    altura, largura = contract.mosaic_height, contract.mosaic_width
    a, _b, ox, _d, e, oy = contract.mosaic_transform
    resolution = a
    print(f"      grade mestra: {altura}x{largura} @ {contract.crs}")

    vrt_estado = write_vrt(contract, str(VRT_DIR / "estado.vrt"), band=BANDA_ESTADO)
    vrt_elev   = write_vrt(contract, str(VRT_DIR / "elevacao.vrt"), band=BANDA_ELEV)

    # ── 2. disscube: grade, fontes e tiles ───────────────────────────────────
    print("[2/3] disscube: registrando grade, fontes e tiles")
    cube = CubeClient(
        catalog=CATALOGO, store=STORE
    )
    cube.register_grid(GridSpec(
        id=GRID_ID, type="reference", crs=str(contract.crs), resolution=resolution,
        bbox=[ox, oy - altura * abs(e), ox + largura * resolution, oy],
    ))
    cube.register_spatial_source(SpatialSource(
        id="estado_2024", name="brm_state_2024", format="raster",
        asset_url=str(vrt_estado), crs=str(contract.crs),
    ))
    cube.register_spatial_source(SpatialSource(
        id="elevacao", name="elevation_m", format="raster",
        asset_url=str(vrt_elev), crs=str(contract.crs),
    ))

    # A extensão do mosaico é um retângulo, mas o dado válido é só a faixa
    # costeira: boa parte dos recortes cobre apenas nodata, e derivá-los é
    # tempo jogado fora. Uma passada decimada sobre a fonte dá a máscara de
    # onde há dado de verdade, e só esses recortes entram no loop.
    mascara, fator = _mascara_valida(vrt_estado)

    # Cada recorte vira um SpatialSource {grid_id}_{tile_id} carregando só o
    # bbox — é assim que derive(tile_id=...) descobre a janela a processar.
    janelas, vazios = [], 0
    for r0 in range(0, altura, TILE):
        for c0 in range(0, largura, TILE):
            h, w = min(TILE, altura - r0), min(TILE, largura - c0)

            # Os recortes são janelas em pixel na própria fonte, então basta
            # indexar a máscara na mesma proporção — sem transformar CRS.
            sub = mascara[r0 // fator:-(-(r0 + h) // fator),
                          c0 // fator:-(-(c0 + w) // fator)]
            if not sub.any():
                vazios += 1
                continue

            tile_id = f"R{r0:05d}C{c0:05d}"
            minx, maxy = ox + c0 * resolution, oy - r0 * abs(e)
            cube.register_spatial_source(SpatialSource(
                id=f"{GRID_ID}_{tile_id}", name=tile_id, format="raster",
                asset_url=str(vrt_estado), crs=str(contract.crs),
                bbox=[minx, maxy - h * abs(e), minx + w * resolution, maxy],
            ))
            janelas.append((tile_id, r0, c0, h, w))
    print(f"      {len(janelas)} tiles de {TILE}x{TILE} com dado válido "
          f"({vazios} descartados por serem só nodata)")

    deriv_papel = SpatialDerivation(
        source_id="estado_2024", grid_id=GRID_ID, role="state",
        variables=[Variable(name="papel", operator="reclassify", mapping=MAPPING)],
    )
    deriv_elev = SpatialDerivation(
        source_id="elevacao", grid_id=GRID_ID, role="driver",
        variables=[Variable(name="elevacao", operator="mean")],
    )

    # ── 3. derive por tile + escrita janelada ────────────────────────────────
    print(f"[3/3] derive() por tile -> {SAIDA}")
    perfil = dict(
        driver="GTiff", height=altura, width=largura, count=2, dtype="uint8",
        nodata=NODATA_SAIDA, crs=contract.crs,
        transform=rasterio.transform.Affine(a, 0.0, ox, 0.0, e, oy),
        tiled=True, blockxsize=512, blockysize=512,
        compress="deflate", zlevel=6, bigtiff="IF_SAFER", sparse_ok=True,
    )

    with rasterio.open(SAIDA, "w", **perfil) as dst:
        dst.set_band_description(1, "papel_dominio_2024")
        dst.set_band_description(2, "elevacao_valida")

        for i, (tile_id, r0, c0, h, w) in enumerate(janelas, start=1):
            cube.derive(deriv_papel, tile_id=tile_id)
            cube.derive(deriv_elev, tile_id=tile_id)

            papel = cube.load("papel", tile_id=tile_id).values
            elev  = cube.load("elevacao", tile_id=tile_id).values

            # ── Regra do projeto: acoplamento papel/elevação ──────────────
            # `elevacao_valida` só é 0/1 onde o ESTADO é válido; onde o estado
            # é nodata a banda 2 também é nodata. O modelo Variable do
            # disscube deriva uma banda por variável, então este cruzamento
            # entre duas bandas é responsabilidade de quem chama.
            estado_valido = ~np.isnan(papel)
            elev_valida   = np.isfinite(elev) & (elev != NODATA_ELEV)

            janela = Window(c0, r0, w, h)
            dst.write(
                np.where(estado_valido, papel, NODATA_SAIDA).astype(np.uint8)[:h, :w],
                1, window=janela,
            )
            dst.write(
                np.where(
                    estado_valido, elev_valida.astype(np.uint8), NODATA_SAIDA
                ).astype(np.uint8)[:h, :w],
                2, window=janela,
            )

            if i % 5 == 0 or i == len(janelas):
                print(f"      {i}/{len(janelas)} tiles ({100 * i / len(janelas):.0f}%)")

    print(f"\n=== domínio estrutural gerado em {time.perf_counter() - t0:.1f}s ===")
    print(f"    {SAIDA}  ({altura}x{largura}, 2 bandas uint8)")


if __name__ == "__main__":
    main()
