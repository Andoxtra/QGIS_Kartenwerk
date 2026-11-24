def classFactory(iface):
    from .plugin import KartenwerkPlugin

    return KartenwerkPlugin(iface)
