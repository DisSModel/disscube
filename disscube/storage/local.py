import fsspec


class AssetStore:
    """
    Filesystem-agnostic access to the asset store, and the boundary between
    the two path forms the catalog deals with.

    Inside the store an asset is identified by a path **relative to the store
    root** (``derived/<grid>/<partition>/<spec_hash>/<var>.zarr``). That
    relative form is what the catalog persists, so an entry keeps resolving
    after the store is moved to another directory or machine, and regardless
    of the working directory the process runs from.

    The absolute (or protocol-qualified) form is produced on demand by
    :meth:`get_full_path` and used only by whoever actually opens the bytes.
    Keeping the two apart is what makes the catalog portable: ``base_url``
    is supplied by the caller at construction time and may be relative
    (``"./data/"``) or absolute (``"/srv/cube"``), so a path built from it
    is only meaningful in the process that built it.
    """

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.fs, self.path = fsspec.core.url_to_fs(base_url)

    def _base(self) -> str:
        """base_url with a guaranteed trailing separator."""
        return self.base_url if self.base_url.endswith("/") else self.base_url + "/"

    def get_full_path(self, relative_path: str) -> str:
        return f"{self._base()}{relative_path}"

    def to_relative(self, path: str) -> str:
        """
        Inverse of :meth:`get_full_path` — express ``path`` relative to the
        store root, which is the form the catalog stores.

        A path outside the store is refused rather than silently recorded in
        a form that only resolves on this machine: an asset the store does
        not contain has no portable name, and writing its absolute path into
        the catalog is exactly the bug this method exists to prevent.

        Raises
        ------
        ValueError
            If ``path`` is not under the store root.
        """
        base = self._base()
        if path.startswith(base):
            return path[len(base):]

        # base_url may be relative ("./data/") while `path` is already
        # absolute — compare both in the filesystem's own path space.
        native = self.fs._strip_protocol(path)
        root = self.path.rstrip("/")
        if native == root:
            return ""
        if native.startswith(root + "/"):
            return native[len(root) + 1:]

        raise ValueError(
            f"Asset path is outside the store root, so it has no portable "
            f"name: {path!r} is not under {self.base_url!r}."
        )

    def resolve(self, asset_url: str) -> str:
        """
        Full, openable path for a catalog ``asset_url``.

        Accepts both the relative form written by ``VariableWriter`` and the
        absolute or working-directory-relative form written by earlier
        versions, so an existing catalog keeps working without migration.
        A legacy entry is returned unchanged — it is not portable, but it
        still resolves wherever it already resolved.
        """
        if not asset_url:
            raise ValueError(
                "Catalog entry has an empty asset_url and cannot be resolved."
            )
        # Legacy: absolute or protocol-qualified path stored by an older version.
        if "://" in asset_url or asset_url.startswith("/"):
            return asset_url
        # Legacy: path built from a relative base_url, e.g. "./data/derived/...".
        if asset_url.startswith(self._base()):
            return asset_url
        return self.get_full_path(asset_url)

    def exists(self, relative_path: str) -> bool:
        return self.fs.exists(f"{self.path}/{relative_path}")

    def open(self, relative_path: str, mode: str = "rb"):
        return self.fs.open(f"{self.path}/{relative_path}", mode=mode)
