"""
Proximity operators — Euclidean distance and feature-count over vector sources.
"""

from __future__ import annotations

import warnings
from typing import ClassVar

import geopandas as gpd
import numpy as np
import rasterio.features
import xarray as xr
from rasterio.warp import Resampling

from disscube.models.grid import GridSpec
from disscube.models.variable import Variable
from disscube.operators.base import Operator


class MinDistanceOperator(Operator):
    """
    Euclidean distance (in CRS units) from each grid cell to the nearest
    feature in the vector source.
    """
    name = "min_distance"
    _resampling = Resampling.nearest

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            from scipy.ndimage import distance_transform_edt
            shapes = ((g, 1) for g in data.geometry if g is not None)
            mask = rasterio.features.rasterize(
                shapes,
                out_shape=(grid.rows, grid.cols),
                transform=grid.transform,
                fill=0,
                all_touched=True,
            )
            if not mask.any():
                # No feature inside the grid: the distance transform has no
                # target. Use ``distance`` for features outside the grid.
                warnings.warn(
                    f"min_distance: no feature of '{var.name}' falls inside the grid; "
                    "returning NaN — use operator 'distance' for features outside the grid",
                    RuntimeWarning, stacklevel=2,
                )
                dist = np.full((grid.rows, grid.cols), np.nan)
            else:
                dist = distance_transform_edt(1 - mask) * grid.resolution
            return xr.DataArray(
                dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs}
            )
        if isinstance(data, xr.DataArray):
            if "band" in data.dims:
                data = data.isel(band=0)
            return data.transpose("y", "x")
        raise TypeError(
            f"'min_distance' expects a vector source, got {type(data).__name__}"
        )


class DistanceOperator(Operator):
    """
    Exact Euclidean distance (in CRS units) from each cell centre to the
    nearest feature of a vector source.

    Unlike ``min_distance`` (a raster approximation between rasterized cell
    centres), this measures the true distance from the cell centre to the
    geometry, and the source is not clipped to the grid, so features outside
    it — a town 50 km away, a road beyond the edge — count. Distances are in
    the grid CRS units (degrees on a geographic grid), measured from cell
    centres. TerraME 2.0.1's ``distance`` fill measures from the cell centre to
    the nearest *vertex* of the feature: identical to this for points, and
    larger for lines wherever a line passes between vertices (this measures to
    the segment).

    With ``params = {"crs": ...}`` the cell centres and the features are
    projected to that CRS and the distance is measured there — metres on a
    geographic grid, e.g. in a national projection.
    """

    name = "distance"
    _resampling = Resampling.nearest
    clip_to_grid = False
    params: ClassVar[dict[str, str]] = {"crs": "measure in this CRS instead of the grid's (e.g. a projected CRS, for metres)"}

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            import shapely

            crs = var.params.get("crs")
            if crs is not None:
                data = data.to_crs(crs)
            geoms = [g for g in data.geometry if g is not None and not g.is_empty]
            if not geoms:
                dist = np.full((grid.rows, grid.cols), np.nan)
            else:
                xx, yy = np.meshgrid(grid.xs, grid.ys)
                if crs is not None:
                    from pyproj import Transformer

                    xx, yy = Transformer.from_crs(grid.crs, crs, always_xy=True).transform(xx, yy)
                centres = shapely.points(xx.ravel(), yy.ravel())
                tree = shapely.STRtree(geoms)
                (points_idx, _), d = tree.query_nearest(centres, return_distance=True, all_matches=False)
                flat = np.full(centres.shape, np.nan)
                flat[points_idx] = d
                dist = flat.reshape((grid.rows, grid.cols))
            return xr.DataArray(dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})
        raise TypeError(f"'distance' expects a vector source, got {type(data).__name__}")


class CountOperator(Operator):
    """Count of vector features whose centroid falls within each grid cell."""
    name = "count"
    _resampling = Resampling.nearest

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            valid = data[data.geometry.notnull()]
            if valid.empty:
                counts = np.zeros((grid.rows, grid.cols), dtype=np.float64)
            else:
                minx, _miny, _maxx, maxy = grid.bbox
                res = grid.resolution
                centroids = valid.geometry.centroid
                cols_idx = ((centroids.x - minx) / res).astype(int)
                rows_idx = ((maxy - centroids.y) / res).astype(int)
                in_bounds = (
                    (cols_idx >= 0) & (cols_idx < grid.cols)
                    & (rows_idx >= 0) & (rows_idx < grid.rows)
                )
                flat = rows_idx[in_bounds] * grid.cols + cols_idx[in_bounds]
                counts = np.bincount(
                    flat, minlength=grid.rows * grid.cols
                ).reshape((grid.rows, grid.cols)).astype(np.float64)
            return xr.DataArray(
                counts, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs}
            )
        if isinstance(data, xr.DataArray):
            if "band" in data.dims:
                data = data.isel(band=0)
            return data.transpose("y", "x")
        raise TypeError(
            f"'count' expects a vector source, got {type(data).__name__}"
        )




class NetworkCostOperator(Operator):
    """
    Least-cost network travel distance/cost from each cell centre to target
    destinations via a linear transport network (e.g. roads, railways),
    accounting for off-road access and on-road impedance.

    Replicates and improves upon TerraME's GPM (Generalized Proximity Matrix)
    Network connectivity algorithm (used in LuccME-BR for e_connmkt and e_connport).

    Parameters in var.params:
    -------------------------
    targets : str or list
        Path to vector file (e.g. shapefile/GeoJSON) of target destinations
        (points or polygons), or a list of [x, y] coordinates.
    cost_column : str, optional
        Name of attribute column with individual road impedance/cost multiplier
        (e.g. 'custo_ajus'). If present, overrides status_column.
    status_column : str, optional
        Name of attribute column distinguishing road status (e.g. 'status', 'paved').
    inside_paved : float, optional
        Cost/impedance factor on paved lines (default 1.0).
    inside_unpaved : float, optional
        Cost/impedance factor on unpaved lines (default 1.0).
    outside : float, optional
        Cost/impedance factor outside the network (default 1.0).
    unit_scale : float, optional
        Multiplier applied to distances (e.g. 1e-3 to convert metres to kilometres, default 1.0).
    entrance : str, optional
        How cell connects to the network: 'segment' (orthogonal projection, default)
        or 'vertex' (nearest line vertex, replicating TerraME).
    crs : str, optional
        Projected CRS for metric distance calculation (e.g. 'EPSG:5880').
    """

    name = "network_cost"
    _resampling = Resampling.nearest
    clip_to_grid = False
    params: ClassVar[dict[str, str]] = {
        "targets": "path to destinations vector layer or coordinate list [[x, y], ...]",
        "cost_column": "column in network vector with per-segment cost multiplier (e.g. 'custo_ajus')",
        "status_column": "column in network vector distinguishing road status (e.g. 'status')",
        "inside_paved": "impedance/friction multiplier on paved lines (default: 1.0)",
        "inside_unpaved": "impedance/friction multiplier on unpaved lines (default: 1.0)",
        "outside": "impedance/friction multiplier off-road from cell to network (default: 1.0)",
        "unit_scale": "scale factor for output units, e.g. 1e-3 for km (default: 1.0)",
        "entrance": "connection rule: 'segment' (nearest edge) or 'vertex' (nearest vertex, TerraME)",
        "crs": "projected CRS to measure distances in metres (e.g. 'EPSG:5880')",
    }

    def compute(self, data, var: Variable, grid: GridSpec) -> xr.DataArray:
        if isinstance(data, gpd.GeoDataFrame):
            import shapely
            from shapely.geometry import Point, LineString
            from scipy.sparse import csr_matrix
            from scipy.sparse.csgraph import dijkstra

            crs = var.params.get("crs")
            if crs is not None:
                data = data.to_crs(crs)

            geoms = [g for g in data.geometry if g is not None and not g.is_empty]
            if not geoms:
                dist = np.full((grid.rows, grid.cols), np.nan)
                return xr.DataArray(dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})

            outside_factor = float(var.params.get("outside", 1.0))
            inside_paved_factor = float(var.params.get("inside_paved", 1.0))
            inside_unpaved_factor = float(var.params.get("inside_unpaved", 1.0))
            unit_scale = float(var.params.get("unit_scale", 1.0))
            entrance_mode = var.params.get("entrance", "segment").lower()
            cost_col = var.params.get("cost_column")
            status_col = var.params.get("status_column")

            # 1. Carregar os destinos (alvos)
            targets_param = var.params.get("targets")
            target_points = []
            if isinstance(targets_param, str):
                import os
                if os.path.exists(targets_param):
                    tgdf = gpd.read_file(targets_param)
                    if crs is not None and tgdf.crs is not None:
                        tgdf = tgdf.to_crs(crs)
                    for g in tgdf.geometry:
                        if g is not None and not g.is_empty:
                            target_points.append(g if g.geom_type == "Point" else g.centroid)
            elif isinstance(targets_param, (list, tuple)):
                for pt in targets_param:
                    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
                        target_points.append(Point(pt[0], pt[1]))
                    elif isinstance(pt, Point):
                        target_points.append(pt)

            if not target_points:
                warnings.warn(
                    f"network_cost: no valid targets provided for '{var.name}'; returning NaN",
                    RuntimeWarning,
                    stacklevel=2,
                )
                dist = np.full((grid.rows, grid.cols), np.nan)
                return xr.DataArray(dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})

            # 2. Construir o grafo da rede viária
            node_coords = {}
            node_list = []
            edges = []
            segment_geoms = []
            seg_to_edge = []

            def get_node(pt):
                key = (round(pt.x, 3), round(pt.y, 3))
                if key not in node_coords:
                    idx = len(node_coords)
                    node_coords[key] = idx
                    node_list.append(Point(pt.x, pt.y))
                    return idx
                return node_coords[key]

            for _, row in data.iterrows():
                geom = row.geometry
                if geom is None or geom.is_empty:
                    continue

                # Determinar o fator de impedância da linha
                if cost_col and cost_col in row and not pd.isna(row[cost_col]):
                    factor = float(row[cost_col])
                elif status_col and status_col in row:
                    val = str(row[status_col]).lower()
                    if any(term in val for term in ("unpaved", "terra", "implantada", "natural", "leito")):
                        factor = inside_unpaved_factor
                    else:
                        factor = inside_paved_factor
                else:
                    factor = inside_paved_factor

                lines = geom.geoms if geom.geom_type == "MultiLineString" else [geom]
                for line in lines:
                    coords = list(line.coords)
                    for i in range(len(coords) - 1):
                        p1 = Point(coords[i])
                        p2 = Point(coords[i + 1])
                        seg_dist = p1.distance(p2)
                        if seg_dist == 0:
                            continue
                        u = get_node(p1)
                        v = get_node(p2)
                        seg_cost = seg_dist * factor * unit_scale
                        edges.append((u, v, seg_cost))
                        segment_geoms.append(LineString([coords[i], coords[i + 1]]))
                        seg_to_edge.append((u, v, factor))

            num_nodes = len(node_list)
            if num_nodes == 0 or not segment_geoms:
                dist = np.full((grid.rows, grid.cols), np.nan)
                return xr.DataArray(dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})

            row_idx, col_idx, cost_data = [], [], []
            for u, v, cost in edges:
                row_idx.extend([u, v])
                col_idx.extend([v, u])
                cost_data.extend([cost, cost])

            adj_matrix = csr_matrix((cost_data, (row_idx, col_idx)), shape=(num_nodes, num_nodes))
            node_tree = shapely.STRtree(node_list)

            # 3. Mapear alvos na rede com custo de acesso inicial (TerraME GPM)
            target_indices = []
            target_access_penalties = []
            for t in target_points:
                nearest_node_idx = int(np.atleast_1d(node_tree.query_nearest(t))[0])
                nearest_pt = node_list[nearest_node_idx]
                target_indices.append(nearest_node_idx)
                # Adiciona o custo de acesso euclidiano fora da rede do porto até a linha
                access_dist = t.distance(nearest_pt)
                target_access_penalties.append(access_dist * outside_factor * unit_scale)

            # 4. Resolver caminhos mínimos via Dijkstra
            dist_matrix = dijkstra(adj_matrix, indices=target_indices, directed=False)
            
            # Somar a penalidade de acesso do alvo ao vetor de distâncias do grafo
            if len(target_indices) == 1:
                node_min_cost = dist_matrix[0] if dist_matrix.ndim == 2 else dist_matrix
                node_min_cost = node_min_cost + target_access_penalties[0]
            else:
                penalties = np.array(target_access_penalties)[:, np.newaxis]
                node_min_cost = np.min(dist_matrix + penalties, axis=0)

            # 5. Interpolar para as células da grade
            xx, yy = np.meshgrid(grid.xs, grid.ys)
            if crs is not None:
                from pyproj import Transformer
                xx, yy = Transformer.from_crs(grid.crs, crs, always_xy=True).transform(xx, yy)

            centres = shapely.points(xx.ravel(), yy.ravel())
            
            if entrance_mode == "vertex":
                # Regra do vértice mais próximo (reproduz a lógica discreta do TerraME)
                indices, offroad_dists = node_tree.query_nearest(centres, return_distance=True, all_matches=False)
                c_idx, v_idx = indices[0], indices[1]
                flat_costs = offroad_dists * (outside_factor * unit_scale) + node_min_cost[v_idx]
            else:
                # Regra do segmento contínuo (ortogonal, mais precisa)
                seg_tree = shapely.STRtree(segment_geoms)
                indices, offroad_dists = seg_tree.query_nearest(centres, return_distance=True, all_matches=False)
                pt_idx, seg_idx = indices[0], indices[1]

                flat_costs = np.full(len(centres), np.nan)
                for i in range(len(pt_idx)):
                    s_idx = seg_idx[i]
                    c_idx = pt_idx[i]
                    u, v, factor = seg_to_edge[s_idx]
                    seg_geom = segment_geoms[s_idx]
                    c_pt = centres[c_idx]

                    dist_along = seg_geom.project(c_pt)
                    p_proj = seg_geom.interpolate(dist_along)
                    d_u = p_proj.distance(node_list[u])
                    d_v = p_proj.distance(node_list[v])
                    net_cost = min(node_min_cost[u] + d_u * factor * unit_scale,
                                   node_min_cost[v] + d_v * factor * unit_scale)
                    flat_costs[c_idx] = offroad_dists[i] * (outside_factor * unit_scale) + net_cost

            out_dist = flat_costs.reshape((grid.rows, grid.cols))
            return xr.DataArray(out_dist, dims=("y", "x"), coords={"y": grid.ys, "x": grid.xs})

        raise TypeError(f"'network_cost' expects a vector source, got {type(data).__name__}")

class GpmNetworkOperator(NetworkCostOperator):
    """
    Alias for NetworkCostOperator implementing TerraME GPM Network connectivity.
    """
    name = "gpm_network"


# ---------------------------------------------------------------------------
# Legacy compatibility shim
# ---------------------------------------------------------------------------

class ProximityAggregator:
    """
    Deprecated.  Use ``MinDistanceOperator`` / ``CountOperator`` directly.
    """

    @staticmethod
    def aggregate(data, variables, grid_spec):
        import warnings
        warnings.warn(
            "ProximityAggregator is deprecated; use OPERATOR_REGISTRY operators directly.",
            DeprecationWarning,
            stacklevel=2,
        )
        from disscube.operators.base import OPERATOR_REGISTRY
        var = variables[0]
        op_cls = OPERATOR_REGISTRY.get(var.operator)
        if op_cls is None:
            raise ValueError(f"Unknown operator: {var.operator}")
        return op_cls().compute(data, var, grid_spec)
