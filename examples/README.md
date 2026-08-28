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
- `python examples/case_studies/brmangue_dominio/03_carregar_no_haloexec.py` — carrega
  as variáveis derivadas num `MemmapRasterWorkspace` do haloexec (par em disco do
  `maranhao/03_brmangue_simulate.py`, que entrega em RAM). Requer `haloexec[zarr]`.
- `python examples/case_studies/brmangue_dominio/04_serie_temporal_mangue.py` — série
  **temporal** mangue/não-mangue: cada ano é uma fatia, `load()` devolve `(time, y, x)`.
- `python examples/case_studies/brmangue_dominio/05_serie_temporal_bdc.py` — a mesma
  série sobre a malha **BDC_SM**, com reprojeção 5880 → Albers. Requer `disscube[bdc]`.

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
| Medido | 15 tiles, 36 s, < 1 GB | 15 tiles, 57 s, 2,8 GB de pico |

Ambos selecionam os recortes por uma máscara de dado válido, não pela extensão:
o mosaico é um retângulo mas o dado é uma faixa costeira, então metade dos
recortes cobriria só nodata (30→15 e 32→15). Uma leitura decimada da fonte
resolve isso, e o resultado é bit a bit o mesmo — verificado.

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
| `tools/zarr_to_tif.py` | Converte **um** Zarr derivado para GeoTIFF |
| `tools/tiles_to_tif.py` | Recorta **alguns tiles** de uma variável num GeoTIFF, para inspecionar a malha |
| `tools/import_bdc_tiles.py` | Importa tiles BDC SM/MD/LG no catálogo (one-time, lento) |

```bash
python tools/zarr_to_tif.py data/derived/.../var.zarr output.tif
python tools/import_bdc_tiles.py
```

### Inspecionar a malha de tiles

Abrir o mosaico inteiro para conferir se os tiles estão no lugar certo é
desconfortável e, quando os valores dos dois lados de uma fronteira coincidem,
não mostra nada. `tools/tiles_to_tif.py` recorta só os tiles pedidos e
acrescenta uma banda com o **índice do tile**, que torna as fronteiras visíveis
mesmo aí:

```bash
# três tiles vizinhos, duas fatias temporais
python tools/tiles_to_tif.py --grid brmangue/30m_bdc --variavel mangue \
    --tiles 029006 030006 030007 --tempos 1985 2024

# variável estática: basta omitir --tempos
python tools/tiles_to_tif.py --grid BR/5km --variavel slope --tiles 009002
```

Saída: uma banda por fatia pedida, mais `indice_do_tile` (1, 2, 3… na ordem em
que foram pedidos). No QGIS, estilize essa última como categórica e os limites
aparecem. Um tile inexistente é recusado listando os disponíveis, e uma variável
temporal sem `--tempos` avisa quais fatias existem.

> **Por que ele monta por blocos, e não tile a tile:** quando o lado do tile não
> é múltiplo do bloco do GeoTIFF (os tiles BDC são 3520, o bloco é 512), cada
> borda de tile cai no meio de um bloco, e a parte não coberta fica com o
> preenchimento padrão do GDAL — zero, não o nodata declarado. Onde zero é um
> valor válido, isso vira dado inventado.
