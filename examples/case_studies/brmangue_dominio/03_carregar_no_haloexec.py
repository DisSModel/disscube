"""
examples/case_studies/brmangue_dominio/03_carregar_no_haloexec.py

BR-MANGUE — carrega as variáveis derivadas num MemmapRasterWorkspace do
haloexec, prontas para um modelo rodar em disco.

É o par em disco do `maranhao/03_brmangue_simulate.py`: aquele entrega o
cubo em RAM via `to_lucc_data()` -> `RasterBackend`; este entrega em disco
via workspace memmap, para domínios que não cabem na memória.

NÃO roda simulação — só deixa o workspace pronto.

Como as duas metades se encaixam
--------------------------------
As variáveis foram derivadas tile a tile (`derive(tile_id=...)`), então
existe um Zarr por tile. `cube.tile_layout()` responde ONDE cada pedaço
cai na grade mestra — caminho e posição, dado puro — e o loader do
haloexec monta o workspace a partir disso, bloco a bloco.

Nenhum dos dois pacotes importa o outro: o contrato entre eles é a lista
de dicionários, do mesmo jeito que o geomosaic entrega `tile_offsets` sem
conhecer quem vai lê-los.

Por padrão usa a grade **BDC Albers** (`brmangue/30m_bdc`, do exemplo 02),
por ser a malha canônica. Troque `GRID_ID` para `brmangue/30m` para usar a
malha ad hoc do 01.

Pré-requisitos:
  - python examples/case_studies/brmangue_dominio/02_dominio_bdc.py
  - pip install "haloexec[zarr]"

Usage:
    python examples/case_studies/brmangue_dominio/03_carregar_no_haloexec.py
"""

from pathlib import Path

from disscube.client import CubeClient

try:
    from haloexec import MemmapRasterWorkspace, load_zarr_tiles_into_workspace
except ImportError as exc:  # pragma: no cover — exemplo, fora do pacote
    raise SystemExit(
        'Este exemplo precisa do haloexec:  pip install "haloexec[zarr]"'
    ) from exc


GRID_ID   = "brmangue/30m_bdc"        # malha BDC (exemplo 02); ou "brmangue/30m"
VARIAVEIS = ["papel", "elevacao"]
WS_DIR    = Path("./data/workspace_brmangue_bdc")

# float32 em vez de float64: o workspace mantém dois slots (double buffer),
# então cada variável ocupa 2x o tamanho da grade em disco.
DTYPE   = "float32"
BLOCK_H = BLOCK_W = 512
HALO    = 2


def main() -> None:
    cube = CubeClient(catalog="catalog.db", store="./data/")
    grid = cube.catalog.get_grid(GRID_ID)
    if grid is None:
        raise SystemExit(
            f"Grade {GRID_ID!r} não encontrada. Rode antes:\n"
            "    python examples/case_studies/brmangue_dominio/02_dominio_bdc.py"
        )

    print(f"\n[1/2] {GRID_ID}: {grid.rows}x{grid.cols}")
    layouts = {nome: cube.tile_layout(nome, GRID_ID) for nome in VARIAVEIS}
    for nome, tiles in layouts.items():
        print(f"      {nome}: {len(tiles)} tiles de "
              f"{tiles[0]['height']}x{tiles[0]['width']}")

    print(f"\n[2/2] montando o workspace em {WS_DIR}")
    ws = MemmapRasterWorkspace.create(
        WS_DIR, shape=(grid.rows, grid.cols),
        arrays={nome: DTYPE for nome in VARIAVEIS},
        block_h=BLOCK_H, block_w=BLOCK_W, halo=HALO,
    )
    for nome, tiles in layouts.items():
        load_zarr_tiles_into_workspace(ws, tiles, array=nome)
        print(f"      {nome}: carregado")

    print(f"\n=== workspace pronto ===")
    print(f"    shape={ws.shape} arrays={list(ws.metadata['arrays'])} "
          f"blocos={len(ws.blocks())} halo={ws.halo}")
    print("    (nenhum modelo foi executado — o workspace está pronto para receber um)")
    print("\n    Para rodar um modelo sobre ele, componha o mixin de disco:")
    print("        class FloodModelDiskHalo(DiskChunkedSyncRasterModel, FloodModel): pass")
    print("        FloodModelDiskHalo(workspace=ws, ...)")


if __name__ == "__main__":
    main()
