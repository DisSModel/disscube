"""
Tests for ``CubeClient.tile_layout()``.

O layout é o contrato entre o cubo e quem monta o dado — dado puro, sem
objeto de outro pacote. Estes testes fixam o formato e a aritmética de
posição, que é onde um erro passa despercebido: um offset trocado produz
um mosaico com tiles no lugar errado, sem erro nenhum.
"""

import tempfile
from pathlib import Path

import pytest

from disscube.client import CubeClient
from disscube.models import DerivedVariable, GridSpec, SpatialSource

CRS = "EPSG:31982"
RES = 10.0
# grade 100x100 px: bbox de 1000x1000 unidades
GRID_BBOX = [0.0, 0.0, 1000.0, 1000.0]


@pytest.fixture
def cube():
    with tempfile.TemporaryDirectory() as d:
        c = CubeClient(catalog=str(Path(d) / "c.db"), store=str(Path(d) / "s"))
        c.register_grid(GridSpec(
            id="G", type="local", crs=CRS, resolution=RES, bbox=list(GRID_BBOX),
        ))
        yield c


def _tile_source(cube, source_id, bbox):
    cube.register_spatial_source(SpatialSource(
        id=source_id, name=source_id, format="raster",
        asset_url="planned", crs=CRS, bbox=list(bbox),
    ))


def _derived(cube, name, tile_id, url="x.zarr"):
    cube.catalog.save_derived(DerivedVariable(
        id=f"{name}_{tile_id or 'global'}", name=name, grid_id="G", role="test",
        times=[], dtype="float64", derivation_id="d", spec_hash="h",
        tile_id=tile_id, asset_url=url,
    ))


# ── formato do contrato ──────────────────────────────────────────────────────

def test_layout_item_has_the_documented_keys(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _derived(cube, "v", "T1")
    item = cube.tile_layout("v", "G")[0]
    assert set(item) == {
        "tile_id", "variable", "url", "row_off", "col_off", "height", "width",
        "times",
    }


def test_variable_name_travels_with_the_tile(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _derived(cube, "papel", "T1")
    assert cube.tile_layout("papel", "G")[0]["variable"] == "papel"


# ── aritmética de posição ────────────────────────────────────────────────────

def test_top_left_tile_sits_at_origin(cube):
    """bbox no canto superior-esquerdo da grade -> offset (0, 0)."""
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _derived(cube, "v", "T1")
    t = cube.tile_layout("v", "G")[0]
    assert (t["row_off"], t["col_off"]) == (0, 0)
    assert (t["height"], t["width"]) == (50, 50)


def test_row_offset_grows_downward_not_upward(cube):
    """y cresce para cima no CRS, mas row cresce para baixo — o sinal aqui
    é o erro clássico, e inverteria o mosaico verticalmente."""
    # tile na METADE DE BAIXO da grade: y de 0 a 500
    _tile_source(cube, "G_low", (0.0, 0.0, 500.0, 500.0))
    _derived(cube, "v", "low")
    t = cube.tile_layout("v", "G")[0]
    assert t["row_off"] == 50, "tile inferior deve começar na linha 50, não 0"


def test_column_offset_grows_rightward(cube):
    _tile_source(cube, "G_right", (500.0, 500.0, 1000.0, 1000.0))
    _derived(cube, "v", "right")
    t = cube.tile_layout("v", "G")[0]
    assert (t["row_off"], t["col_off"]) == (0, 50)


def test_four_quadrants_tile_the_grid_without_gap_or_overlap(cube):
    quadrantes = {
        "NO": (0.0, 500.0, 500.0, 1000.0),
        "NE": (500.0, 500.0, 1000.0, 1000.0),
        "SO": (0.0, 0.0, 500.0, 500.0),
        "SE": (500.0, 0.0, 1000.0, 500.0),
    }
    for tid, bbox in quadrantes.items():
        _tile_source(cube, f"G_{tid}", bbox)
        _derived(cube, "v", tid)

    layout = cube.tile_layout("v", "G")
    assert len(layout) == 4
    coberto = sum(t["height"] * t["width"] for t in layout)
    assert coberto == 100 * 100, "os quatro quadrantes devem cobrir a grade"
    cantos = {(t["row_off"], t["col_off"]) for t in layout}
    assert cantos == {(0, 0), (0, 50), (50, 0), (50, 50)}


def test_layout_is_ordered_by_position(cube):
    for tid, bbox in [
        ("SE", (500.0, 0.0, 1000.0, 500.0)),
        ("NO", (0.0, 500.0, 500.0, 1000.0)),
        ("NE", (500.0, 500.0, 1000.0, 1000.0)),
    ]:
        _tile_source(cube, f"G_{tid}", bbox)
        _derived(cube, "v", tid)
    ordem = [(t["row_off"], t["col_off"]) for t in cube.tile_layout("v", "G")]
    assert ordem == sorted(ordem)


# ── convenções de registro de tile ───────────────────────────────────────────

def test_bdc_style_tile_id_resolves(cube):
    """Tiles BDC são registrados pelo id canônico, sem prefixo de grade."""
    _tile_source(cube, "BDC_SM_009002", (0.0, 500.0, 500.0, 1000.0))
    _derived(cube, "v", "BDC_SM_009002")
    assert cube.tile_layout("v", "G")[0]["tile_id"] == "BDC_SM_009002"


def test_grid_scoped_wins_over_bare_id(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _tile_source(cube, "T1", (500.0, 0.0, 1000.0, 500.0))
    _derived(cube, "v", "T1")
    t = cube.tile_layout("v", "G")[0]
    assert (t["row_off"], t["col_off"]) == (0, 0)


# ── variável global (sem tiles) ──────────────────────────────────────────────

def test_global_variable_yields_one_item_covering_the_grid(cube):
    """Quem consome deve poder tratar global e tileado do mesmo jeito."""
    _derived(cube, "v", None)
    layout = cube.tile_layout("v", "G")
    assert len(layout) == 1
    t = layout[0]
    assert t["tile_id"] is None
    assert (t["row_off"], t["col_off"]) == (0, 0)
    assert (t["height"], t["width"]) == (100, 100)


# ── erros ────────────────────────────────────────────────────────────────────

def test_unknown_grid_raises(cube):
    with pytest.raises(ValueError, match="Grid not found"):
        cube.tile_layout("v", "inexistente")


def test_variable_without_derivations_raises(cube):
    with pytest.raises(ValueError, match="Derived variable not found"):
        cube.tile_layout("nao_existe", "G")


def test_tile_without_bbox_raises_naming_what_was_searched(cube):
    """Sem bbox não há posição — falhar alto é melhor que empilhar em (0,0)."""
    cube.register_spatial_source(SpatialSource(
        id="G_T1", name="T1", format="raster", asset_url="planned", crs=CRS,
    ))
    _derived(cube, "v", "T1")
    with pytest.raises(ValueError) as exc:
        cube.tile_layout("v", "G")
    assert "G_T1" in str(exc.value)


# ── variáveis temporais ──────────────────────────────────────────────────────
# Uma variável derivada com valid_from/valid_until tem um conjunto de pedaços
# POR FATIA, todos nas mesmas posições. Juntá-los daria um layout em que cada
# célula aparece N vezes — e quem montasse a partir dele sobrescreveria uma
# fatia com outra sem erro nenhum. Foi o que dado real revelou (série mangue
# 1985-2024): 140 pedaços em 28 posições.

def _derived_t(cube, name, tile_id, times, url):
    cube.catalog.save_derived(DerivedVariable(
        id=f"{name}_{tile_id}_{times[0]}", name=name, grid_id="G", role="test",
        times=times, dtype="float64", derivation_id="d",
        spec_hash=f"h{times[0]}", tile_id=tile_id, asset_url=url,
    ))


def test_temporal_variable_without_time_raises(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    for ano in (1985, 1995):
        _derived_t(cube, "v", "T1", [ano], f"v_{ano}.zarr")
    with pytest.raises(ValueError, match="temporal"):
        cube.tile_layout("v", "G")


def test_error_lists_the_available_slices(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    for ano in (1985, 1995, 2005):
        _derived_t(cube, "v", "T1", [ano], f"v_{ano}.zarr")
    with pytest.raises(ValueError) as exc:
        cube.tile_layout("v", "G")
    assert "1985" in str(exc.value) and "2005" in str(exc.value)


def test_time_selects_one_slice(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    for ano in (1985, 1995):
        _derived_t(cube, "v", "T1", [ano], f"v_{ano}.zarr")
    layout = cube.tile_layout("v", "G", time=1995)
    assert len(layout) == 1
    assert layout[0]["url"] == "v_1995.zarr"
    assert layout[0]["times"] == [1995]


def test_each_slice_covers_every_position_exactly_once(cube):
    """O ponto todo: com a fatia escolhida, nenhuma posição se repete."""
    for tid, bbox in [("A", (0.0, 500.0, 500.0, 1000.0)),
                      ("B", (500.0, 500.0, 1000.0, 1000.0))]:
        _tile_source(cube, f"G_{tid}", bbox)
        for ano in (1985, 1995):
            _derived_t(cube, "v", tid, [ano], f"v_{tid}_{ano}.zarr")
    layout = cube.tile_layout("v", "G", time=1985)
    posicoes = [(t["row_off"], t["col_off"]) for t in layout]
    assert len(posicoes) == 2 and len(set(posicoes)) == 2


def test_unknown_time_raises_naming_what_exists(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _derived_t(cube, "v", "T1", [1985], "v.zarr")
    with pytest.raises(ValueError, match="1985"):
        cube.tile_layout("v", "G", time=2020)


def test_single_slice_needs_no_time(cube):
    """Uma variável com uma fatia só não é ambígua — não deve exigir time."""
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _derived_t(cube, "v", "T1", [1985], "v.zarr")
    assert len(cube.tile_layout("v", "G")) == 1


def test_static_variable_reports_empty_times(cube):
    _tile_source(cube, "G_T1", (0.0, 500.0, 500.0, 1000.0))
    _derived(cube, "v", "T1")
    assert cube.tile_layout("v", "G")[0]["times"] == []
