"""Create and manage the web map service layers."""
from __future__ import annotations

from qgis.core import (
    QgsLayerTreeGroup,
    QgsLayerTreeModel,
    QgsMapThemeCollection,
    QgsProject,
    QgsRasterLayer,
)

from . import config


def get_layer(project: QgsProject, name: str):
    layers = project.mapLayersByName(name)
    return layers[0] if layers else None


def create_wms_layer(base_url: str, layer: str, name: str, fmt: str = "image/png"):
    uri = config.wms_uri(base_url, layer, fmt)
    rl = QgsRasterLayer(uri, name, "wms")
    if not rl.isValid():
        raise RuntimeError(
            f"WMS-Layer '{name}' konnte nicht geladen werden.\n"
            f"URL: {base_url}\nLayer: {layer}"
        )
    return rl


def create_xyz_layer(url_template: str, name: str, tile_pixel_ratio: int = 2) -> QgsRasterLayer:
    uri = f"type=xyz&url={url_template}&tilePixelRatio={tile_pixel_ratio}"
    rl = QgsRasterLayer(uri, name, "wms")
    if not rl.isValid():
        raise RuntimeError(
            f"XYZ-Layer '{name}' konnte nicht geladen werden.\nURL: {url_template}"
        )
    return rl


def _group(project: QgsProject, name: str) -> QgsLayerTreeGroup:
    root = project.layerTreeRoot()
    node = root.findGroup(name)
    if node is None:
        node = root.addGroup(name)
    return node


def ensure_basemaps(project: QgsProject) -> dict:
    """Create the basemap layers if missing and return them by role."""
    group = _group(project, config.BASEMAP_GROUP)
    specs = {
        "topo_web": (
            config.TOPO_WMS,
            config.TOPO_LAYERS["web"][0],
            config.TOPO_LAYERS["web"][1],
            "image/png",
            False,
        ),
        "topo_light": (
            config.TOPO_WMS,
            config.TOPO_LAYERS["web_light"][0],
            config.TOPO_LAYERS["web_light"][1],
            "image/png",
            False,
        ),
        "topo_grau": (
            config.TOPO_WMS,
            config.TOPO_LAYERS["web_grau"][0],
            config.TOPO_LAYERS["web_grau"][1],
            "image/png",
            False,
        ),
        "dop_rgb": (
            config.DOP_RGB_WMS,
            config.IMAGERY_LAYERS["IMAGES_DOP_20_RGB"][0],
            config.IMAGERY_LAYERS["IMAGES_DOP_20_RGB"][1],
            "image/jpeg",
            False,
        ),
        "dop_cir": (
            config.DOP_CIR_WMS,
            config.IMAGERY_LAYERS["IMAGES_DOP_20_CIR"][0],
            config.IMAGERY_LAYERS["IMAGES_DOP_20_CIR"][1],
            "image/jpeg",
            False,
        ),
        "dgm": (
            config.DGM_WMS,
            config.DGM_LAYER[0],
            config.DGM_LAYER[1],
            "image/png",
            False,
        ),
    }

    result = {}
    for role, (url, lyr, name, fmt, visible) in specs.items():
        layer = get_layer(project, name)
        if layer is None:
            try:
                layer = create_wms_layer(url, lyr, name, fmt)
            except Exception as exc:
                print(f"Warnung: {name} nicht verfügbar: {exc}")
                continue
            layer.setCustomProperty("kartenwerk_role", role)
            project.addMapLayer(layer, False)
            group.addLayer(layer)
        node = group.findLayer(layer.id())
        if node is not None:
            node.setItemVisibilityChecked(visible)
        result[role] = layer

    topo = get_layer(project, config.OPENTOPO_NAME)
    if topo is None:
        try:
            topo = create_xyz_layer(config.OPENTOPO_URL, config.OPENTOPO_NAME)
        except Exception as exc:
            print(f"Warnung: {config.OPENTOPO_NAME} nicht verfügbar: {exc}")
        else:
            topo.setCustomProperty("kartenwerk_role", "topo")
            project.addMapLayer(topo, False)
            group.addLayer(topo)
    if topo is not None:
        node = group.findLayer(topo.id())
        if node is not None:
            node.setItemVisibilityChecked(True)
        result["topo"] = topo

    return result


def ensure_map_themes(project: QgsProject, basemaps: dict) -> None:
    """Create canvas themes Topo / Luftbild / Luftbild_CIR if absent."""
    root = project.layerTreeRoot()
    model = QgsLayerTreeModel(root)
    coll = project.mapThemeCollection()

    themes = {
        "Topo": {"topo"},
        "Luftbild": {"dop_rgb"},
        "Luftbild_CIR": {"dop_cir"},
        "Topo + Relief": {"topo", "dgm"},
    }
    role_by_layer = {layer.id(): role for role, layer in basemaps.items()}

    current = {lid: _is_visible(root, lid) for lid in role_by_layer}
    try:
        for theme_name, roles in themes.items():
            if coll.hasMapTheme(theme_name):
                continue
            for layer_id, role in role_by_layer.items():
                _set_visible(root, layer_id, role in roles)
            record = QgsMapThemeCollection.createThemeFromCurrentState(root, model)
            coll.insert(theme_name, record)
    finally:
        for layer_id, visible in current.items():
            _set_visible(root, layer_id, visible)


def _find_layer_node(node, layer_id: str):
    if hasattr(node, "layerId") and node.layerId() == layer_id:
        return node
    for child in node.children():
        found = _find_layer_node(child, layer_id)
        if found is not None:
            return found
    return None


def _is_visible(root, layer_id: str) -> bool:
    node = _find_layer_node(root, layer_id)
    return bool(node and node.isVisible())


def _set_visible(root, layer_id: str, visible: bool) -> None:
    node = _find_layer_node(root, layer_id)
    if node is not None:
        node.setItemVisibilityChecked(bool(visible))
