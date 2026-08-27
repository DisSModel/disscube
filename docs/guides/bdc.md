# Integração com Brazil Data Cube

## Grades BDC e tiles

O Brazil Data Cube (BDC) particionam o Brasil em tiles hierárquicos. DisSCube representa isso com:

- **Master Grid**: definição de resolução e CRS para todo o país.
- **Tiles**: `SpatialSource` com o `bbox` de cada partição.

## Registrar grades e tiles BDC

O utilitário `bdc_importer` indexa as master grids e registra cada tile como `SpatialSource` no catálogo:

```python
from disscube.utils.bdc_importer import import_bdc_grids

import_bdc_grids(
    cube,
    sm_path="data/bdc_grids/BDC_SM_V2.shp",
    md_path="data/bdc_grids/BDC_MD_V2.shp",
    lg_path="data/bdc_grids/BDC_LG_V2.shp",
)
```

Isso registra as master grids e cada tile como `SpatialSource` com `bbox` preenchido.

!!! warning "Ingestão de dados STAC — planejada"
    `bdc_importer` indexa a grade BDC e os tiles (geometria e metadados), mas **não realiza ingestão de dados via STAC**. Os `SpatialSource` registrados têm `asset_url` como placeholder (`"planned"`) e não são diretamente carregáveis como dados raster. A integração com o catálogo STAC do BDC está planejada e ainda não implementada. Para usar dados BDC reais, forneça os arquivos localmente via um `SpatialSource` com `asset_url` apontando para o arquivo correto.

## Derivação por tile

```python
from disscube.models import SpatialDerivation, Variable

derivation = SpatialDerivation(
    source_id="slope_brazil",
    grid_id="BR/5km",
    role="driver",
    variables=[Variable(name="slope", operator="mean")],
)

# Processar um tile
cube.derive(derivation, tile_id="009002")

# Processar todos os tiles SM em loop
# Tiles são registrados com IDs no formato BDC_SM_<tile> (ex: BDC_SM_009002)
tiles = [s for s in cube.catalog.list_spatial_sources() if s.id.startswith("BDC_SM_")]
for tile_source in tiles:
    cube.derive(derivation, tile_id=tile_source.id)   # ID completo: BDC_SM_009002
```

Cada tile é processado de forma independente e pode ser paralelizado.

!!! warning "IDs de tile não são únicos entre níveis"
    O mesmo número identifica tiles diferentes em níveis diferentes — nas grades
    V2, 189 IDs existem em SM **e** MD, cobrindo áreas distintas. Um ID simples
    (`"009002"`) só é aceito quando existe em um único nível; se existir em mais
    de um, `derive()` levanta `ValueError` em vez de escolher por precedência.
    **Em loops, prefira sempre o ID completo** (`tile_source.id`), como acima.

## Como o tile é localizado

`derive(tile_id=...)` procura o `SpatialSource` que carrega o `bbox`, nesta ordem:

1. `{grid_id}_{tile_id}` — malha de tiles definida para uma grade específica
   (ver `docs/architecture/tiling.md`);
2. o `tile_id` tratado como ID completo — é assim que se desambigua
   (`"BDC_SM_009002"`);
3. `BDC_{SM,MD,LG}_{tile_id}` — as grades nacionais BDC, registradas uma única
   vez e compartilhadas por todas as grades no CRS do BDC.

Os tiles BDC são independentes de grade: a mesma envoltória serve qualquer grade
que compartilhe o CRS, por isso não são duplicados por grade.

## Carregar resultado tileado

```python
# Tile específico — sempre funciona
da = cube.load("slope", tile_id="009002")

# Por grade — funciona apenas quando há um único tile no resultado
da = cube.load("slope", grid_id="BR/5km")
```

!!! warning "Carga multi-tile"
    `load()` sem `tile_id` levanta `ValueError` quando múltiplos tiles da mesma variável existem na mesma grade. Mosaico automático não está implementado. **Sempre especifique `tile_id` em workloads multi-tile.**

## Grade 100m nacional

Para projetos que precisam de resolução mais alta que BDC_SM (10m), DisSCube suporta uma grade de 100m customizada:

```python
from disscube.utils.grids import register_local_grid

grid_100m = register_local_grid(
    cube,
    name="BR",
    bbox_geo=(-73.98, -33.75, -28.65, 5.27),  # bbox do Brasil em WGS84
    resolution=100.0,
    snap=True,
)
```

## Fluxo completo: setup → derivação → carregamento

```python
from disscube.client import CubeClient
from disscube.models import SpatialSource, SpatialDerivation, Variable

cube = CubeClient(catalog="catalog.db", store="./data/")

# 1. Fonte
cube.register_spatial_source(SpatialSource(
    id="urban_centers",
    name="Centros Urbanos PNLT",
    format="vector",
    asset_url="data/raw/urban_centers.shp",
    crs="EPSG:5880",
))

# 2. Derivação — distância a centros urbanos em BR/5km
derivation = SpatialDerivation(
    source_id="urban_centers",
    grid_id="BR/5km",
    role="driver",
    variables=[Variable(name="dist_cidades", operator="min_distance")],
    valid_from="2000",
    valid_until="2014",
)

# 3. Executar para um tile
cube.derive(derivation, tile_id="009002")

# 4. Carregar
da = cube.load("dist_cidades", tile_id="009002")
print(da.shape)   # (rows, cols)
```

## Variáveis temporais com tiles

Para drivers com variação temporal, derive múltiplos períodos e carregue como série:

```python
for start, end in [("2000", "2014"), ("2015", "2025")]:
    cube.derive(SpatialDerivation(
        source_id="urban_centers",
        grid_id="BR/5km",
        role="driver",
        variables=[Variable(name="dist_cidades", operator="min_distance")],
        valid_from=start, valid_until=end,
    ))

# Carrega série temporal (time, y, x)
da = cube.load("dist_cidades", grid_id="BR/5km")
print(da.dims)   # ('time', 'y', 'x')
```
