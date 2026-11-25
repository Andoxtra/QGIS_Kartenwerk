"""Shared project / layer-tree helpers and the default basemaps."""
from __future__ import annotations

from qgis.core import (
    QgsLayerTreeGroup,
    QgsLayerTreeModel,
    QgsMapThemeCollection,
    QgsProject,
)

from . import sources


def get_layer(project: QgsProject, name: str):
    if not name:
        return None
    layers = project.mapLayersByName(name)
    return layers[0] if layers else None


def find_layer(project: QgsProject, name: str, legacy: tuple = ()):
    """Layer by its current name, else by one of its version-1 names."""
    for candidate in (name, *legacy):
        layer = get_layer(project, candidate)
        if layer is not None:
            return layer
    return None


def group(project: QgsProject, name: str) -> QgsLayerTreeGroup:
    root = project.layerTreeRoot()
    node = root.findGroup(name)
    return node if node is not None else root.addGroup(name)


def ensure_basemaps(project: QgsProject) -> dict:
    """Add the two worldwide basemaps (topo visible, imagery hidden) and return them by role."""
    result = {}
    for role, visible in ((sources.TOPO, True), (sources.IMAGERY, False)):
        source = sources.world_default(role)
        try:
            result[role] = sources.ensure_layer(project, source, visible=visible)
        except Exception as exc:  # offline: keep going, the export will report it
            print(f"Warning: {source.name} not available: {exc}")
    return result


def ensure_map_themes(project: QgsProject, basemaps: dict) -> None:
    """Create canvas themes 'Topo' and 'Imagery' if absent."""
    root = project.layerTreeRoot()
    model = QgsLayerTreeModel(root)
    coll = project.mapThemeCollection()
    themes = {"Topo": {sources.TOPO}, "Imagery": {sources.IMAGERY}}
    role_by_layer = {layer.id(): role for role, layer in basemaps.items()}

    current = {lid: _is_visible(root, lid) for lid in role_by_layer}
    try:
        for theme_name, roles in themes.items():
            if coll.hasMapTheme(theme_name):
                continue
            for layer_id, role in role_by_layer.items():
                _set_visible(root, layer_id, role in roles)
            coll.insert(theme_name, QgsMapThemeCollection.createThemeFromCurrentState(root, model))
    finally:
        for layer_id, visible in current.items():
            _set_visible(root, layer_id, visible)


def _is_visible(root, layer_id: str) -> bool:
    node = root.findLayer(layer_id)
    return bool(node and node.isVisible())


def _set_visible(root, layer_id: str, visible: bool) -> None:
    node = root.findLayer(layer_id)
    if node is not None:
        node.setItemVisibilityChecked(bool(visible))

