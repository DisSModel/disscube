"""
examples/case_studies/brmangue_dominio/02_dominio_bdc.py

BR-MANGUE — domínio estrutural na malha nacional BDC (tiles SM, 30 m).

Variante do `01_dominio_estrutural.py`. A diferença está inteiramente na
definição da partição e do CRS de trabalho:

    01_  malha ad hoc (4096x4096 em pixel), grade em EPSG:5880.
         Sem reprojeção — a fonte já está na grade alvo.
    02_  malha BDC_SM (canônica, nacional), grade em BDC Albers.
         Reprojeta 5880 -> Albers dentro do GridAligner.

Quando usar cada uma
--------------------
A malha ad hoc é mais rápida e reproduz a fonte bit a bit, mas os recortes
só têm significado dentro do script que os criou. A malha BDC dá tiles
canônicos, compartilháveis entre projetos e alinhados com o resto do
ecossistema BDC — ao custo de uma reprojeção real e de um produto que já
não é idêntico à fonte (Albers reamostrado, não Polyconic).

Escolha a malha BDC quando os derivados forem virar patrimônio do cubo;
a ad hoc quando forem só um passo intermediário.

Nota sobre `elevacao_valida`
----------------------------
Com reprojeção, a máscara de validade da elevação é reamostrada junto
(operador `mean` -> `Resampling.average`), então as bordas entre válido e
nodata ficam aproximadas. Em `01_` isso não acontece porque não há
reamostragem. É consequência de mudar de grade, não um defeito do cubo.

Pré-requisitos
--------------
  - pip install geomosaic
  - pip install "disscube[bdc]"          (fiona, para ler os shapefiles BDC)
  - grades BDC em data/bdc_grids/BDC_{SM,MD,LG}_V2.zip
  - tiles ANADEM v2 em $BRMANGUE_ENTRADA/anadem_v2/

Usage:
    export BRMANGUE_ENTRADA=/caminho/para/pymangue/dados/entrada
    python examples/case_studies/brmangue_dominio/02_dominio_bdc.py
"""

import os
import time
from pathlib import Path

import numpy as np
import rasterio
from pyproj import CRS as ProjCRS, Transformer
from rasterio.windows import Window
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
TILES_DIR = ENTRADA / "anadem_v2"
BDC_SM    = "zip://data/bdc_grids/BDC_SM_V2.zip"
# Catálogo e store compartilhados, como em todos os demais exemplos: os
# derivados entram no MESMO cubo e aparecem em cube.search() junto com as
# outras variáveis — que é o ponto de catalogá-los.
CATALOGO  = "catalog.db"
STORE     = "./data/"
VRT_DIR   = Path("./data/raw/brmangue")
SAIDA     = Path("./data/brmangue_dominio_2024_bdc.tif")

GRID_ID      = "brmangue/30m_bdc"
RESOLUCAO    = 30.0
BANDA_ESTADO = 3
BANDA_ELEV   = 1
NODATA_SAIDA = 255
NODATA_ELEV  = -9999.0

# ── Específico do BR-MANGUE — dado do projeto, não do disscube ────────────────
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
    Máscara booleana, em baixa resolução, de onde a fonte tem dado válido.

    Uma única leitura decimada — a memória fica limitada por ``largura_alvo``,
    não pelo tamanho da fonte. A máscara é dilatada em uma célula porque a
    decimação por vizinho mais próximo pode perder feições finas (a faixa de
    mangue tem poucos pixels de largura em muitos trechos), e aqui um falso
    positivo custa um tile derivado à toa enquanto um falso negativo custa
    dado faltando no produto final.

    Returns
    -------
    (mascara, transform, resolucao_da_mascara)
    """
    from scipy.ndimage import binary_dilation

    with rasterio.open(vrt) as ds:
        fator = max(1, int(np.ceil(ds.width / largura_alvo)))
        altura = max(1, ds.height // fator)
        largura = max(1, ds.width // fator)
        baixa = ds.read(
            1, out_shape=(altura, largura), resampling=rasterio.enums.Resampling.nearest
        )
        nodata = ds.nodata
        transform = ds.transform
        resolucao = abs(transform.a) * (ds.width / largura)

    valida = np.isfinite(baixa)
    if nodata is not None and np.isfinite(nodata):
        valida &= baixa != nodata
    return binary_dilation(valida), transform, resolucao


def _tem_dado(bounds, mascara, transform, resolucao, para_fonte) -> bool:
    """True se o tile (bounds em BDC Albers) toca algum pixel válido da fonte."""
    minx, miny, maxx, maxy = bounds
    xs, ys = para_fonte.transform(
        [minx, minx, maxx, maxx], [miny, maxy, miny, maxy]
    )
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


def main() -> None:
    if not TILES_DIR.is_dir():
        raise RuntimeError(
            f"Diretório de tiles não encontrado: {TILES_DIR}\n"
            "Defina BRMANGUE_ENTRADA apontando para .../pymangue/dados/entrada"
        )
    tiles_src = sorted(TILES_DIR.glob("*.tif"))
    if not tiles_src:
        raise RuntimeError(f"Nenhum .tif em {TILES_DIR}")

    t0 = time.perf_counter()
    VRT_DIR.mkdir(parents=True, exist_ok=True)

    # ── 1. geomosaic: VRTs na projeção nativa da fonte (EPSG:5880) ───────────
    print(f"\n[1/4] geomosaic: {len(tiles_src)} tiles -> contrato")
    contract = build_mosaic_contract([str(t) for t in tiles_src])
    a, _b, ox, _d, e, oy = contract.mosaic_transform
    fonte_bbox = (
        ox, oy - contract.mosaic_height * abs(e),
        ox + contract.mosaic_width * a, oy,
    )
    print(f"      fonte: {contract.mosaic_height}x{contract.mosaic_width} @ {contract.crs}")

    vrt_estado = write_vrt(contract, str(VRT_DIR / "estado.vrt"), band=BANDA_ESTADO)
    vrt_elev   = write_vrt(contract, str(VRT_DIR / "elevacao.vrt"), band=BANDA_ELEV)

    # ── 2. Selecionar os tiles BDC_SM que cobrem a fonte ─────────────────────
    print("[2/4] BDC: selecionando tiles SM sobre a extensão da fonte")
    para_bdc = Transformer.from_crs(
        ProjCRS.from_user_input(str(contract.crs)),
        ProjCRS.from_user_input(BDC_CRS),
        always_xy=True,
    )
    # O footprint é densificado antes de projetar: transformar só os quatro
    # cantos e tomar min/max devolve uma envoltória maior que a área real
    # (as bordas viram curvas em outra projeção), o que seleciona tiles que
    # não encostam no dado. Eles seriam derivados como vazios — correto, mas
    # trabalho à toa.
    passo = 200
    contorno = []
    x0, y0, x1, y1 = fonte_bbox
    for i in range(passo + 1):
        f = i / passo
        contorno += [
            (x0 + (x1 - x0) * f, y0), (x0 + (x1 - x0) * f, y1),
            (x0, y0 + (y1 - y0) * f), (x1, y0 + (y1 - y0) * f),
        ]
    px, py = para_bdc.transform([p[0] for p in contorno], [p[1] for p in contorno])
    alvo = MultiPoint(list(zip(px, py))).convex_hull

    candidatos = []
    with fiona.open(BDC_SM) as src:
        for rec in src:
            geom = shape(rec["geometry"])
            if geom.intersects(alvo):
                candidatos.append((rec["properties"]["tile"], geom.bounds))
    if not candidatos:
        raise RuntimeError("Nenhum tile BDC_SM cobre a extensão da fonte.")
    candidatos.sort()

    # A extensão do mosaico é um retângulo, mas o dado válido é só a faixa
    # costeira — mais da metade dos tiles que a intersectam cobrem apenas
    # nodata. Derivá-los é trabalho e disco jogados fora (medido: 17 de 32
    # tiles saíam 100% vazios). Uma passada decimada sobre a fonte dá a
    # máscara de onde há dado de verdade, e a seleção passa a ser por ela.
    print(f"      {len(candidatos)} tiles intersectam a extensão; "
          "lendo máscara de dado válido")
    mascara, m_transform, m_res = _mascara_valida(vrt_estado)
    para_fonte = Transformer.from_crs(
        ProjCRS.from_user_input(BDC_CRS),
        ProjCRS.from_user_input(str(contract.crs)),
        always_xy=True,
    )

    selecionados = [
        (tile, bounds) for tile, bounds in candidatos
        if _tem_dado(bounds, mascara, m_transform, m_res, para_fonte)
    ]
    if not selecionados:
        raise RuntimeError("Nenhum tile BDC_SM contém dado válido.")
    print(f"      {len(selecionados)} tiles com dado válido "
          f"({len(candidatos) - len(selecionados)} descartados por serem só nodata)")

    # Extensão de saída = envoltória dos tiles escolhidos (já alinhada à malha).
    out_minx = min(b[0] for _t, b in selecionados)
    out_miny = min(b[1] for _t, b in selecionados)
    out_maxx = max(b[2] for _t, b in selecionados)
    out_maxy = max(b[3] for _t, b in selecionados)
    altura   = int(round((out_maxy - out_miny) / RESOLUCAO))
    largura  = int(round((out_maxx - out_minx) / RESOLUCAO))
    print(f"      saída: {altura}x{largura} @ BDC Albers, {RESOLUCAO:.0f}m")

    # ── 3. disscube: grade mestra BDC + fontes + tiles ───────────────────────
    print("[3/4] disscube: registrando grade, fontes e tiles")
    cube = CubeClient(catalog=CATALOGO, store=STORE)
    cube.register_grid(GridSpec(
        id=GRID_ID, type="reference", crs=BDC_CRS, resolution=RESOLUCAO,
        bbox=[out_minx, out_miny, out_maxx, out_maxy],
        description="BR-MANGUE 30 m sobre a malha BDC (Albers)",
    ))
    cube.register_spatial_source(SpatialSource(
        id="estado_2024", name="brm_state_2024", format="raster",
        asset_url=str(vrt_estado), crs=str(contract.crs),
    ))
    cube.register_spatial_source(SpatialSource(
        id="elevacao", name="elevation_m", format="raster",
        asset_url=str(vrt_elev), crs=str(contract.crs),
    ))

    # Tiles BDC registrados com o id canônico. São independentes de grade —
    # o mesmo envelope serve qualquer grade no CRS do BDC.
    for tile, bounds in selecionados:
        cube.register_spatial_source(SpatialSource(
            id=f"BDC_SM_{tile}", name=f"BDC SM Tile {tile}", format="raster",
            asset_url="planned", crs=BDC_CRS, bbox=list(bounds),
        ))

    deriv_papel = SpatialDerivation(
        source_id="estado_2024", grid_id=GRID_ID, role="state",
        variables=[Variable(name="papel", operator="reclassify", mapping=MAPPING)],
    )
    deriv_elev = SpatialDerivation(
        source_id="elevacao", grid_id=GRID_ID, role="driver",
        variables=[Variable(name="elevacao", operator="mean")],
    )

    # ── 4. derive por tile BDC + escrita janelada ────────────────────────────
    print(f"[4/4] derive() por tile BDC -> {SAIDA}")
    perfil = dict(
        driver="GTiff", height=altura, width=largura, count=2, dtype="uint8",
        nodata=NODATA_SAIDA, crs=BDC_CRS,
        transform=rasterio.transform.Affine(
            RESOLUCAO, 0.0, out_minx, 0.0, -RESOLUCAO, out_maxy
        ),
        tiled=True, blockxsize=512, blockysize=512,
        compress="deflate", zlevel=6, bigtiff="IF_SAFER", sparse_ok=True,
    )

    with rasterio.open(SAIDA, "w", **perfil) as dst:
        dst.set_band_description(1, "papel_dominio_2024")
        dst.set_band_description(2, "elevacao_valida")

        for i, (tile, bounds) in enumerate(selecionados, start=1):
            # id COMPLETO: ids simples não são únicos entre níveis BDC.
            tile_id = f"BDC_SM_{tile}"
            cube.derive(deriv_papel, tile_id=tile_id)
            cube.derive(deriv_elev, tile_id=tile_id)

            papel = cube.load("papel", tile_id=tile_id).values
            elev  = cube.load("elevacao", tile_id=tile_id).values

            # Regra do projeto: acoplamento papel/elevação (duas bandas).
            estado_valido = ~np.isnan(papel)
            elev_valida   = np.isfinite(elev) & (elev != NODATA_ELEV)

            c0 = int(round((bounds[0] - out_minx) / RESOLUCAO))
            r0 = int(round((out_maxy - bounds[3]) / RESOLUCAO))
            h, w = papel.shape
            janela = Window(c0, r0, w, h)

            dst.write(
                np.where(estado_valido, papel, NODATA_SAIDA).astype(np.uint8),
                1, window=janela,
            )
            dst.write(
                np.where(
                    estado_valido, elev_valida.astype(np.uint8), NODATA_SAIDA
                ).astype(np.uint8),
                2, window=janela,
            )

            if i % 5 == 0 or i == len(selecionados):
                print(f"      {i}/{len(selecionados)} tiles "
                      f"({100 * i / len(selecionados):.0f}%)")

    print(f"\n=== domínio na malha BDC gerado em {time.perf_counter() - t0:.1f}s ===")
    print(f"    {SAIDA}  ({altura}x{largura}, 2 bandas uint8, BDC Albers)")


if __name__ == "__main__":
    main()
