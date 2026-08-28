#!/usr/bin/env python3
"""
tools/tiles_to_tif.py

Gera um GeoTIFF só de alguns tiles, recortado à envoltória deles.

Serve para inspecionar a malha: em vez de abrir o mosaico inteiro, sai um
arquivo pequeno cobrindo exatamente os tiles pedidos, com uma banda extra
marcando de qual tile veio cada pixel — assim as fronteiras ficam visíveis
no QGIS mesmo onde os valores dos dois lados coincidem.

Bandas de saída:
    1..N   uma por fatia temporal pedida (ou uma só, se a variável é estática)
    N+1    índice do tile (1, 2, 3... na ordem em que foram pedidos; 255 = fora)

A banda de índice é o que torna as fronteiras visíveis: onde os valores dos
dois lados coincidem, só ela distingue de qual tile veio cada pixel.

Diferente de `tools/zarr_to_tif.py`, que converte UM store inteiro, aqui a
saída é montada de vários tiles — e percorrendo os blocos do GeoTIFF, não os
tiles: quando o lado do tile não é múltiplo do bloco (tiles BDC são 3520,
blocos são 512), escrever tile a tile deixa a parte não coberta dos blocos de
borda com zero em vez do nodata declarado.

Uso:
    python tools/tiles_to_tif.py --tiles 029006 030006 030007
    python tools/tiles_to_tif.py --tiles 029006 --anos 1985 2024
    python tools/tiles_to_tif.py --grid brmangue/30m --tiles R00000C00000 \
        --variavel papel --anos 0
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import rasterio
import zarr
from rasterio.transform import Affine
from rasterio.windows import Window

from disscube.client import CubeClient

# Raiz do repositório — tools/ fica um nível abaixo.
REPO = Path(__file__).resolve().parents[1]
NODATA = 255
BLOCO = 512


def _resolver(url: str, raiz: Path) -> str:
    """O catálogo guarda asset_url RELATIVO ao diretório de onde o disscube foi
    usado. Rodando de outro lugar, é preciso resolver contra a raiz do repo —
    senão o caminho não existe e a leitura falha."""
    p = Path(url)
    return str(p if p.is_absolute() else (raiz / url).resolve())


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--catalogo", default=str(REPO / "catalog.db"))
    ap.add_argument("--store", default=str(REPO / "data"))
    ap.add_argument("--grid", required=True)
    ap.add_argument("--variavel", required=True)
    ap.add_argument("--tiles", nargs="+", required=True,
                    help="ids dos tiles; para tiles BDC o prefixo BDC_SM_ é opcional")
    ap.add_argument("--tempos", nargs="+", type=int, default=None,
                    help="fatias temporais a incluir, uma banda cada; "
                         "omita para variável estática")
    ap.add_argument("--saida", default=None)
    args = ap.parse_args()

    cube = CubeClient(catalog=args.catalogo, store=args.store)
    grid = cube.catalog.get_grid(args.grid)
    if grid is None:
        raise SystemExit(f"Grade {args.grid!r} não encontrada em {args.catalogo}")

    # ── localizar os tiles pedidos ───────────────────────────────────────────
    pedidos, vistos = [], set()
    for t in args.tiles:
        if t in vistos:
            print(f"  aviso: tile {t!r} repetido no pedido — usando uma vez só")
            continue
        vistos.add(t)
        pedidos.append(t)

    tempos = args.tempos
    try:
        layout = cube.tile_layout(args.variavel, args.grid,
                                  time=tempos[0] if tempos else None)
    except ValueError as erro:
        # tile_layout fala na linguagem da API (time=<ano>); aqui o usuário
        # tem um flag, então vale traduzir em vez de repassar o traceback.
        if "temporal" in str(erro):
            fatias = sorted({t for d in cube.search(grid=args.grid)
                             if d.name == args.variavel for t in (d.times or [])})
            raise SystemExit(
                f"{args.variavel!r} é uma variável temporal com as fatias {fatias}.\n"
                f"Escolha uma ou mais com --tempos, por exemplo:\n"
                f"    --tempos {fatias[0]}" +
                (f" {fatias[-1]}" if len(fatias) > 1 else "")
            ) from erro
        raise SystemExit(str(erro)) from erro
    if tempos is None:
        tempos = [None]   # uma banda só, sem fatia
    por_id = {}
    for item in layout:
        por_id[item["tile_id"]] = item
        por_id[item["tile_id"].replace("BDC_SM_", "")] = item

    escolhidos = []
    for t in pedidos:
        if t not in por_id:
            disp = sorted({i["tile_id"].replace("BDC_SM_", "") for i in layout})
            raise SystemExit(
                f"Tile {t!r} não existe em {args.grid!r}.\nDisponíveis: {disp}"
            )
        escolhidos.append(por_id[t])

    # ── envoltória dos tiles escolhidos ─────────────────────────────────────
    r0 = min(t["row_off"] for t in escolhidos)
    c0 = min(t["col_off"] for t in escolhidos)
    r1 = max(t["row_off"] + t["height"] for t in escolhidos)
    c1 = max(t["col_off"] + t["width"] for t in escolhidos)
    altura, largura = r1 - r0, c1 - c0

    print(f"\ngrade {args.grid}: {grid.rows}x{grid.cols}")
    print(f"recorte: linhas {r0}–{r1}, colunas {c0}–{c1}  ({altura}x{largura})")
    for i, t in enumerate(escolhidos, start=1):
        print(f"  {i}. {t['tile_id']:<16} em ({t['row_off']},{t['col_off']}) "
              f"-> local ({t['row_off']-r0},{t['col_off']-c0})")

    # transform deslocado para a origem do recorte
    base = grid.transform
    transform = Affine(base.a, base.b, base.c + c0 * base.a,
                       base.d, base.e, base.f + r0 * base.e)

    saida = Path(args.saida) if args.saida else Path(
        f"./{args.variavel}_{'_'.join(pedidos)}.tif"
    )

    perfil = dict(
        driver="GTiff", height=altura, width=largura,
        count=len(tempos) + 1, dtype="uint8", nodata=NODATA,
        crs=grid.crs, transform=transform,
        tiled=True, blockxsize=BLOCO, blockysize=BLOCO,
        compress="deflate", zlevel=6,
    )

    # ── escrever, percorrendo os BLOCOS do destino ──────────────────────────
    # Os tiles não são múltiplos do bloco do GeoTIFF, então escrever tile a
    # tile deixaria a parte não coberta dos blocos de borda com zero em vez
    # do nodata — e zero é valor válido aqui.
    with rasterio.open(saida, "w", **perfil) as dst:
        for banda, tempo in enumerate(tempos, start=1):
            rotulo = args.variavel if tempo is None else f"{args.variavel}_{tempo}"
            dst.set_band_description(banda, rotulo)
            lay_ano = {i["tile_id"]: i for i in
                       cube.tile_layout(args.variavel, args.grid, time=tempo)}
            raiz = Path(args.catalogo).resolve().parent
            abertos = {
                t["tile_id"]: zarr.open(
                    _resolver(lay_ano[t["tile_id"]]["url"], raiz), mode="r"
                )[args.variavel]
                for t in escolhidos
            }
            _escrever_banda(dst, banda, escolhidos, abertos, r0, c0, altura, largura,
                            valor=None)
            print(f"  banda {banda}: {rotulo}")

        # banda extra: índice do tile, para ver as fronteiras
        idx = len(tempos) + 1
        dst.set_band_description(idx, "indice_do_tile")
        _escrever_banda(dst, idx, escolhidos, None, r0, c0, altura, largura,
                        valor="indice")
        print(f"  banda {idx}: indice_do_tile (1..{len(escolhidos)})")

    with rasterio.open(saida) as ds:
        print(f"\n{saida}")
        print(f"  {ds.height}x{ds.width}, {ds.count} bandas, crs={ds.crs.to_string()[:24]}")
        for b in range(1, ds.count + 1):
            a = ds.read(b)
            v = a != NODATA
            u = np.unique(a[v])
            print(f"  {ds.descriptions[b-1]:<18} válidos={int(v.sum()):>10,}  "
                  f"valores={u[:6]}")
    return 0


def _escrever_banda(dst, banda, escolhidos, abertos, r0, c0, altura, largura, valor):
    for br in range(0, altura, BLOCO):
        br1 = min(br + BLOCO, altura)
        for bc in range(0, largura, BLOCO):
            bc1 = min(bc + BLOCO, largura)
            buf = None
            for i, t in enumerate(escolhidos, start=1):
                # posição do tile dentro do recorte
                tr, tc = t["row_off"] - r0, t["col_off"] - c0
                sr0, sr1 = max(br, tr), min(br1, tr + t["height"])
                sc0, sc1 = max(bc, tc), min(bc1, tc + t["width"])
                if sr0 >= sr1 or sc0 >= sc1:
                    continue
                if buf is None:
                    buf = np.full((br1 - br, bc1 - bc), NODATA, np.uint8)
                if valor == "indice":
                    buf[sr0 - br:sr1 - br, sc0 - bc:sc1 - bc] = i
                else:
                    trecho = np.asarray(abertos[t["tile_id"]][
                        sr0 - tr:sr1 - tr, sc0 - tc:sc1 - tc])
                    buf[sr0 - br:sr1 - br, sc0 - bc:sc1 - bc] = np.where(
                        np.isnan(trecho), NODATA, trecho).astype(np.uint8)
            if buf is not None:
                dst.write(buf, banda, window=Window(bc, br, bc1 - bc, br1 - br))


if __name__ == "__main__":
    raise SystemExit(main())
