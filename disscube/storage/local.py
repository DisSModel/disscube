import fsspec


class AssetStore:
    def __init__(self, base_url: str):
        self.base_url = base_url
        self.fs, self.path = fsspec.core.url_to_fs(base_url)

    def get_full_path(self, relative_path: str) -> str:
        # Ensure base_url ends with /
        base = self.base_url if self.base_url.endswith("/") else self.base_url + "/"
        return f"{base}{relative_path}"

    def exists(self, relative_path: str) -> bool:
        return self.fs.exists(f"{self.path}/{relative_path}")

    def open(self, relative_path: str, mode: str = "rb"):
        return self.fs.open(f"{self.path}/{relative_path}", mode=mode)
