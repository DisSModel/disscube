# DisSCube — Exemplos

Demonstração estruturada do pipeline DisSCube, do bootstrap do catálogo até
estudos de caso completos.

## Ordem de execução

### 1. Setup (one-time)
Bootstrap do catálogo e registro dos dados base.
- `python examples/setup/01_init_catalog.py` — registra grades nacionais e locais.
- `python examples/setup/02_register_sources.py` — registra arquivos brutos como SpatialSources.

### 2. Drivers nacionais
Deriva variáveis na grade nacional BR/5km.
- `python examples/drivers/01_brazil_national.py` — slope, TI, distância a cidades/rios.

### 3. Estudo de caso: Maranhão (Ilha do Maranhão, 100 m)
Dois estudos sobre a mesma área geográfica e grade.
- `python examples/case_studies/maranhao/01_mapbiomas_temporal.py` — série temporal MapBiomas (uso majority) + dist_sedes estática.
- `python examples/case_studies/maranhao/02_brmangue_derive.py` — deriva uso, alt, solo para o modelo BR-MANGUE.
- `python examples/case_studies/maranhao/03_brmangue_simulate.py` — executa BrmangueRasterExecutor.

### 4. Estudo de caso: BR-MANGUE domínio estrutural (nacional, 30 m)
Standalone — não depende dos scripts de `setup/`; registra a própria grade e fontes.
- `python examples/case_studies/brmangue_dominio/01_dominio_estrutural.py` — reclassifica
  a legenda de estados num raster de papéis, tile a tile, e escreve o GeoTIFF final.
- `python examples/case_studies/brmangue_dominio/02_dominio_bdc.py` — o mesmo produto
  sobre a malha nacional **BDC_SM**, com a grade mestra em BDC Albers.

Demonstram três coisas de uso geral:
- o operador genérico **`reclassify`** (tabela `{valor_origem: valor_destino}` como dado);
- **`derive(tile_id=...)`** para processar uma extensão grande (391M células) com
  memória limitada — ver `docs/architecture/tiling.md`;
- a fronteira entre o que é do cubo e o que é do projeto: a tabela de papéis e o
  cruzamento entre duas bandas ficam no exemplo, não no pacote.

A diferença entre os dois está só na partição e no CRS de trabalho:

| | `01_` | `02_` |
|---|---|---|
| Malha | ad hoc, 4096² em pixel | **BDC_SM**, canônica |
| Grade | EPSG:5880 (a da fonte) | BDC Albers |
| Reprojeção | nenhuma (caminho de identidade) | 5880 → Albers |
| Medido | 30 tiles, 80 s, < 1 GB | 32 tiles, 100 s, 3,7 GB de pico |

Use a malha BDC quando os derivados forem virar patrimônio do cubo — os tiles têm
significado fora do script e alinham com o resto do ecossistema BDC. Use a ad hoc
quando forem só um passo intermediário: é mais leve e reproduz a fonte bit a bit
(reprojetar reamostra, então `02_` perde ~1% de células por classe nas bordas).

Requerem `geomosaic` (`pip install geomosaic`) e a variável `BRMANGUE_ENTRADA`
apontando para o diretório com os tiles ANADEM v2. O `02_` também precisa de
`pip install "disscube[bdc]"` e das grades em `data/bdc_grids/`.

### 5. Estudo de caso: Acre (AC/5km)
- `python examples/drivers/02_acre_5km.py` — drivers regionais Acre 5 km.
- `python examples/case_studies/lucc_acre/01_derive.py` — atributos de uso do solo de fonte vetorial.
- `python examples/case_studies/lucc_acre/02_simulate.py` — executa LUCCRasterExecutor.
- `python examples/case_studies/lucc_acre/03_temporal_drivers.py` — loop de simulação com drivers temporais.

---

## Utilitários (`tools/`)

| Script | Uso |
|---|---|
| `tools/zarr_to_tif.py` | Converte Zarr derivado para GeoTIFF |
| `tools/import_bdc_tiles.py` | Importa tiles BDC SM/MD/LG no catálogo (one-time, lento) |

```bash
python tools/zarr_to_tif.py data/derived/.../var.zarr output.tif
python tools/import_bdc_tiles.py
```
