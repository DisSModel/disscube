"""
examples/case_studies/brmangue_dominio/03_carregar_no_haloexec.py

BR-MANGUE — carrega as variáveis derivadas num MemmapRasterWorkspace do
haloexec, prontas para um modelo rodar em disco.

É o par em disco do `maranhao/03_brmangue_simulate.py`: aquele entrega o
cubo em RAM via `to_lucc_data()` -> `RasterBackend`; este entrega em disco
via workspace memmap, para domínios que não cabem na memória.

NÃO roda simulação — só deixa o workspace pronto e prova que os dados
chegaram corretos, inclusive nas costuras entre tiles.

Por padrão usa a grade **BDC Albers** (`brmangue/30m_bdc`, do exemplo 02),
por ser a malha canônica: tiles BDC_SM compartilháveis entre projetos.
Troque `GRID_ID` para `brmangue/30m` para usar a malha ad hoc do 01.

Pré-requisitos:
  - python examples/case_studies/brmangue_dominio/02_dominio_bdc.py
  - pip install "haloexec[zarr]"

Usage:
    python examples/case_studies/brmangue_dominio/03_carregar_no_haloexec.py

Por que o carregamento é feito à mão aqui
------------------------------------------
As variáveis foram derivadas TILE A TILE (`derive(tile_id=...)`), então
existe um Zarr por tile, não um só. Nenhum dos dois lados resolve isso hoje:

  * `CubeClient.load(nome, grid_id=...)` levanta ValueError quando há mais
    de um tile — é preciso pedir um tile específico (comportamento
    documentado em docs/architecture/tiling.md: "mosaico automático não
    está implementado");
  * `haloexec.load_zarr_into_workspace()` recebe UM store e exige que o
    shape dele seja igual ao do workspace inteiro — não há equivalente
    multi-tile como `load_geotiffs_into_workspace` é para GeoTIFF.

Então este exemplo faz a costura: descobre no catálogo quais tiles existem,
usa o bbox de cada um (registrado como SpatialSource) para achar sua posição
na grade mestra — a mesma ideia de `build_mosaic_contract` do geomosaic, mas
partindo da geometria catalogada em vez do nome do arquivo — e monta cada
bloco do workspace lendo dos tiles que o cobrem.

Buracos na malha são esperados (o MapBiomas não exporta onde não há costa)
e viram nodata, não erro.
"""

from pathlib import Path

import numpy as np

from disscube.client import CubeClient
from disscube.models import GridSpec

try:
    import zarr
    from haloexec import Block, MemmapRasterWorkspace
except ImportError as exc:  # pragma: no cover — exemplo, fora do pacote
    raise SystemExit(
        'Este exemplo precisa do haloexec:  pip install "haloexec[zarr]"'
    ) from exc


# ── Configuração ──────────────────────────────────────────────────────────────
CATALOGO  = "catalog.db"
STORE     = "./data/"
GRID_ID   = "brmangue/30m_bdc"        # malha BDC (exemplo 02); ou "brmangue/30m"
VARIAVEIS = ["papel", "elevacao"]
WS_DIR    = Path("./data/workspace_brmangue_bdc")

# float32 em vez de float64: o workspace mantém DOIS slots (double buffer),
# então cada variável ocupa 2x o tamanho da grade em disco.
DTYPE   = "float32"
BLOCK_H = BLOCK_W = 512
HALO    = 2


def _tiles_da_variavel(cube: CubeClient, nome: str, grid: GridSpec) -> list[dict]:
    """Descobre os tiles de ``nome`` e onde cada um cai na grade mestra.

    A posição vem do bbox do SpatialSource do tile — o mesmo bbox que
    `derive(tile_id=...)` usou para recortar — convertido para deslocamento
    em pixel contra a origem da grade. O id do SpatialSource pode ser
    ``{grid_id}_{tile_id}`` (malha própria) ou o próprio ``tile_id`` quando
    já é um id canônico BDC.
    """
    achados = []
    for d in cube.search(grid=grid.id):
        if d.name != nome or not d.tile_id:
            continue
        src = (cube.catalog.get_spatial_source(f"{grid.id}_{d.tile_id}")
               or cube.catalog.get_spatial_source(d.tile_id))
        if src is None or not src.bbox:
            raise RuntimeError(
                f"tile {d.tile_id} sem SpatialSource com bbox — não dá para "
                "saber onde ele cai na grade mestra"
            )
        arr = zarr.open(d.asset_url, mode="r")[nome]
        achados.append({
            "tile_id": d.tile_id,
            "url": d.asset_url,
            "r0": int(round((grid.bbox[3] - src.bbox[3]) / grid.resolution)),
            "c0": int(round((src.bbox[0] - grid.bbox[0]) / grid.resolution)),
            "h": arr.shape[0],
            "w": arr.shape[1],
        })
    return sorted(achados, key=lambda t: (t["r0"], t["c0"]))


def _carregar(ws, nome: str, tiles: list[dict]) -> tuple[int, int]:
    """Preenche ``nome`` no workspace, bloco a bloco, a partir de N tiles.

    Percorre os blocos do workspace (e não os tiles) porque um bloco pode
    cair sobre dois tiles vizinhos, ou sobre um buraco da malha: montá-lo a
    partir de tudo que o cobre é o que faz a costura ficar correta.
    """
    abertos = {t["tile_id"]: zarr.open(t["url"], mode="r")[nome] for t in tiles}
    com = sem = 0

    for blk in ws.blocks():
        buf = np.full((blk.r1 - blk.r0, blk.c1 - blk.c0), np.nan, dtype=DTYPE)
        tocou = False
        for t in tiles:
            r0 = max(blk.r0, t["r0"]); r1 = min(blk.r1, t["r0"] + t["h"])
            c0 = max(blk.c0, t["c0"]); c1 = min(blk.c1, t["c0"] + t["w"])
            if r0 >= r1 or c0 >= c1:
                continue
            buf[r0 - blk.r0:r1 - blk.r0, c0 - blk.c0:c1 - blk.c0] = np.asarray(
                abertos[t["tile_id"]][r0 - t["r0"]:r1 - t["r0"],
                                      c0 - t["c0"]:c1 - t["c0"]]
            )
            tocou = True
        ws.write_block_to_read_slot(blk, nome, buf)
        com += tocou
        sem += not tocou

    ws.flush()
    return com, sem


def _provar_costura(ws, nome: str, esq: dict, dir_: dict) -> tuple[bool, str]:
    """Prova que a janela com halo sobre a costura entre DOIS Zarr está certa.

    A comparação é feita contra cada arquivo Zarr de origem separadamente —
    o lado esquerdo contra o Zarr de ``esq``, o direito contra o de ``dir_``,
    célula a célula. Note o reindexamento: a primeira coluna do vizinho é a
    coluna 0 DELE, não a coluna global — é exatamente aí que um erro de
    offset apareceria.

    A linha é escolhida onde os dois lados DIFEREM. Sem isso a prova é fraca:
    numa região toda 0.0 ou toda NaN a comparação passa sem provar nada.
    """
    za = zarr.open(esq["url"], mode="r")[nome]
    zb = zarr.open(dir_["url"], mode="r")[nome]
    col = dir_["c0"]                       # coluna global onde o vizinho começa

    # linhas em que ambos os tiles existem
    r_ini = max(esq["r0"], dir_["r0"]) + HALO + 1
    r_fim = min(esq["r0"] + esq["h"], dir_["r0"] + dir_["h"]) - HALO - 9
    if r_fim <= r_ini:
        return True, "sem sobreposição vertical suficiente"

    ult = np.asarray(za[r_ini - esq["r0"]:r_fim - esq["r0"], esq["w"] - 1])
    pri = np.asarray(zb[r_ini - dir_["r0"]:r_fim - dir_["r0"], 0])
    dif = np.where(np.isfinite(ult) & np.isfinite(pri) & (ult != pri))[0]
    if dif.size == 0:
        return True, "nenhuma linha com lados diferentes (prova fraca, ignorada)"

    linha = r_ini + int(dif[len(dif) // 2])
    blk = Block(r0=linha - 4, r1=linha + 4, c0=col - 4, c1=col + 4)
    jan = ws.read_block_with_halo(blk, boundary_value=np.nan)[nome]

    jr0, jc0 = blk.r0 - HALO, blk.c0 - HALO
    for jr in range(jan.shape[0]):
        for jc in range(jan.shape[1]):
            gr, gc = jr0 + jr, jc0 + jc
            if gc < col:
                fonte, t = za, esq
            else:
                fonte, t = zb, dir_
            lr, lc = gr - t["r0"], gc - t["c0"]
            if not (0 <= lr < t["h"] and 0 <= lc < t["w"]):
                continue
            esperado = np.float32(np.asarray(fonte[lr, lc]))
            obtido = jan[jr, jc]
            if not ((np.isnan(esperado) and np.isnan(obtido)) or esperado == obtido):
                return False, (f"janela[{jr},{jc}] (global r={gr} c={gc}, tile "
                               f"{t['tile_id']} local r={lr} c={lc}): "
                               f"esperado {esperado}, obtido {obtido}")

    n = len(np.unique(jan[np.isfinite(jan)]))
    return True, f"linha r={linha}, {n} valores distintos na janela"


def main() -> None:
    cube = CubeClient(catalog=CATALOGO, store=STORE)
    grid = cube.catalog.get_grid(GRID_ID)
    if grid is None:
        raise SystemExit(
            f"Grade {GRID_ID!r} não encontrada. Rode antes:\n"
            "    python examples/case_studies/brmangue_dominio/02_dominio_bdc.py"
        )

    print(f"\n[1/3] catálogo: {GRID_ID} = {grid.rows}x{grid.cols} @ {grid.crs[:40]}")
    por_var = {}
    for nome in VARIAVEIS:
        tiles = _tiles_da_variavel(cube, nome, grid)
        if not tiles:
            raise SystemExit(f"nenhum tile derivado para {nome!r} em {GRID_ID}")
        por_var[nome] = tiles
        print(f"      {nome}: {len(tiles)} tiles de {tiles[0]['h']}x{tiles[0]['w']}")

    try:
        cube.load(VARIAVEIS[0], grid_id=GRID_ID)
        print("      (load() sem tile_id funcionou — havia um tile só)")
    except ValueError:
        print("      load() sem tile_id recusa multi-tile, como esperado")

    gib = grid.rows * grid.cols * np.dtype(DTYPE).itemsize * len(VARIAVEIS) * 2 / 1024**3
    print(f"\n[2/3] workspace {grid.rows}x{grid.cols} {DTYPE} "
          f"({len(VARIAVEIS)} arrays x 2 slots = {gib:.1f} GB em disco)")
    ws = MemmapRasterWorkspace.create(
        WS_DIR, shape=(grid.rows, grid.cols),
        arrays={n: DTYPE for n in VARIAVEIS},
        block_h=BLOCK_H, block_w=BLOCK_W, halo=HALO,
    )
    for nome, tiles in por_var.items():
        com, sem = _carregar(ws, nome, tiles)
        print(f"      {nome}: {com} blocos com dado, {sem} inteiramente vazios")

    # ── prova de costura entre arquivos Zarr distintos ──────────────────────
    print("\n[3/3] costuras entre tiles (cada lado conferido contra o SEU Zarr)")
    ws2 = MemmapRasterWorkspace(WS_DIR)
    nome = VARIAVEIS[0]
    tiles = por_var[nome]
    ok = fracas = falhas = 0
    for a in tiles:
        b = next((o for o in tiles
                  if o["r0"] == a["r0"] and o["c0"] == a["c0"] + a["w"]), None)
        if b is None:
            continue
        passou, msg = _provar_costura(ws2, nome, a, b)
        rotulo = f"{a['tile_id']} | {b['tile_id']}"
        if not passou:
            falhas += 1
            print(f"      FALHA  {rotulo}: {msg}")
        elif "fraca" in msg or "sem sobreposição" in msg:
            fracas += 1
            print(f"      pulada {rotulo}: {msg}")
        else:
            ok += 1
            print(f"      OK     {rotulo}: {msg}")
    print(f"\n      {ok} costuras provadas, {fracas} sem contraste, {falhas} FALHAS")

    print(f"\n=== workspace pronto em {WS_DIR} ===")
    print(f"    shape={ws2.shape} arrays={list(ws2.metadata['arrays'])} "
          f"blocos={len(ws2.blocks())} halo={ws2.halo}")
    print("    (nenhum modelo foi executado — o workspace está pronto para receber um)")


if __name__ == "__main__":
    main()
