# Tiling e Processamento Particionado

Para processar dados em escala continental (ex: Brasil a 100m) sem carregar o país inteiro em memória, DisSCube usa um modelo de particionamento baseado em tiles.

## Conceito

Uma **Master Grid** define a resolução e o CRS para todo o país. **Tiles** são recortes com o mesmo CRS e resolução — apenas o bbox muda. O pipeline executa um tile por vez, gerando Zarr isolados que podem ser paralelizados.

```mermaid
graph TD
    MG[Master Grid: BR/5km] --> T1[Tile 001]
    MG --> T2[Tile 002]
    MG --> TN[Tile N…]
    T1 --> Z1[zarr: .../BR_5km/001/{hash}/var.zarr]
    T2 --> Z2[zarr: .../BR_5km/002/{hash}/var.zarr]
```

## Como o CubeClient usa tiles

```python
cube.derive(derivation, tile_id="009002")
```

Internamente:
1. Busca `GridSpec` da master grid.
2. Busca o `SpatialSource` que carrega o `bbox` do tile, tentando nesta ordem:
   `{grid_id}_{tile_id}` (malha própria da grade), o `tile_id` como id completo,
   e `BDC_{SM,MD,LG}_{tile_id}` (grades nacionais BDC). Um id simples que exista
   em mais de um nível BDC levanta `ValueError` — ver `docs/guides/bdc.md`.
3. Cria um `GridSpec` temporário: mesmos CRS e resolução, bbox restrito ao tile.
4. Executa o pipeline nessa grade temporária.
5. Salva em `data/derived/{grid_id}/009002/{spec_hash}/{var}.zarr`.

## Registrar tiles

Cada tile é registrado como um `SpatialSource` especial com o bbox do tile:

```python
from disscube.models import SpatialSource

cube.register_spatial_source(SpatialSource(
    id="BDC_SM_009002",          # convenção: {grid_id}_{tile_id}
    name="BDC SM Tile 009002",
    format="raster",
    asset_url="data/raw/tile_009002.tif",
    crs="EPSG:...",
    bbox=[-70.0, -10.0, -65.0, -5.0],   # bbox do tile em coordenadas geográficas
))
```

O utilitário `disscube.utils.bdc_importer` automatiza esse processo para tiles BDC.

## Processamento em loop

```python
tiles = cube.catalog.list_spatial_sources()
tile_ids = [s.id.split("_")[-1] for s in tiles if s.id.startswith("BDC_SM_")]

for tile_id in tile_ids:
    cube.derive(derivation, tile_id=tile_id)
```

> **Nota:** o `bdc_importer` registra tiles BDC como `SpatialSource` com IDs no formato
> `BDC_SM_<tile>`. A grade de simulação permanece `BR/5km` ou `BR/1km` — os tiles BDC
> definem apenas o bbox do recorte a processar.

Cada iteração é independente. Workers paralelos podem processar tiles diferentes sem conflitos (caminhos Zarr únicos por tile + spec_hash).

## Carregar dados tileados

```python
# Carga de um tile específico (tile_id sempre funciona)
da = cube.load("dist_road", tile_id="009002")

# Carga por grade — funciona quando há apenas um tile
da = cube.load("dist_road", grid_id="BR/5km")
```

> **Limitação atual:** `load()` sem `tile_id` levanta `ValueError` quando múltiplos
> tiles da mesma variável existem na mesma grade — remontá-los num array único não
> está implementado.

## Consumir uma variável multi-tile

`tile_layout()` responde o que `load()` não responde: onde cada pedaço cai na grade
mestra. Devolve dado puro — caminho e posição — sem montar nada e sem impor um
destino.

```python
tiles = cube.tile_layout("dist_road", "BR/5km")
# [{"tile_id": "009002", "variable": "dist_road", "url": ".../dist_road.zarr",
#   "row_off": 0, "col_off": 3520, "height": 3520, "width": 3520, "times": []}, ...]
```

Uma variável **temporal** tem um conjunto de pedaços por fatia, todos nas mesmas
posições; juntá-los descreveria cada célula várias vezes. Por isso `tile_layout()`
exige a fatia nesse caso:

```python
tiles = cube.tile_layout("uso", "BR/5km", time=2015)
```

Sem `time`, uma variável com mais de uma fatia levanta `ValueError` nomeando as
disponíveis — a mesma disciplina que `load()` aplica a multi-tile.

Uma variável sem tiles (partição `global`) devolve um único item cobrindo a grade
inteira, para que quem consome trate os dois casos igual.

O destino fica a critério de quem chama. Para montar em disco, por exemplo,
`haloexec.load_zarr_tiles_into_workspace()` aceita essa lista diretamente.

## Vantagens

- **Memória controlada:** processa um tile de cada vez.
- **Paralelismo trivial:** workers independentes, sem race conditions.
- **Consistência garantida:** todos os tiles derivam da mesma `GridSpec` — pixels sempre alinhados.
- **Cache por tile:** re-executar um tile com o mesmo `spec_hash` é no-op.
