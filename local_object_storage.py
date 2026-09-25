"""
Локальная замена replit.object_storage.

Повторяет тот же интерфейс (Client + ObjectNotFoundError), но хранит объекты
в обычной папке на диске (STORAGE_DIR, по умолчанию ./storage_data).
Так бот работает на любом сервере (VPS/Oracle) без Replit.

Поддерживает методы, которые реально используются в проекте:
  download_as_bytes / download_as_text
  upload_from_bytes / upload_from_text
  delete(key, ignore_not_found=False)
  list(prefix="") -> список объектов с атрибутом .name
"""

import os
import shutil

STORAGE_DIR = os.getenv("STORAGE_DIR", "storage_data")


class ObjectNotFoundError(Exception):
    """Аналог replit.object_storage.errors.ObjectNotFoundError."""


class _Obj:
    """Мини-объект списка: имеет .name, как в replit.object_storage."""
    __slots__ = ("name",)

    def __init__(self, name: str):
        self.name = name


class Client:
    def __init__(self, base: str | None = None):
        self.base = base or STORAGE_DIR
        os.makedirs(self.base, exist_ok=True)

    def _path(self, key: str) -> str:
        # ключи вида "vk_images/abc.jpeg" -> подкаталоги на диске
        safe = key.replace("\\", "/").lstrip("/")
        return os.path.join(self.base, *safe.split("/"))

    # ---------- чтение ----------
    def download_as_bytes(self, key: str) -> bytes:
        path = self._path(key)
        if not os.path.isfile(path):
            raise ObjectNotFoundError(key)
        with open(path, "rb") as f:
            return f.read()

    def download_as_text(self, key: str, encoding: str = "utf-8") -> str:
        return self.download_as_bytes(key).decode(encoding)

    # ---------- запись ----------
    def upload_from_bytes(self, key: str, data: bytes) -> None:
        path = self._path(key)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        # атомарная запись через временный файл
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)

    def upload_from_text(self, key: str, text: str, encoding: str = "utf-8") -> None:
        self.upload_from_bytes(key, text.encode(encoding))

    # ---------- удаление ----------
    def delete(self, key: str, ignore_not_found: bool = False) -> None:
        path = self._path(key)
        if os.path.isfile(path):
            os.remove(path)
        elif not ignore_not_found:
            raise ObjectNotFoundError(key)

    # ---------- список ----------
    def list(self, prefix: str = ""):
        result = []
        if not os.path.isdir(self.base):
            return result
        for root, _dirs, files in os.walk(self.base):
            for fname in files:
                if fname.endswith(".tmp"):
                    continue
                full = os.path.join(root, fname)
                rel = os.path.relpath(full, self.base).replace(os.sep, "/")
                if rel.startswith(prefix):
                    result.append(_Obj(rel))
        return result


# на случай, если где-то ждут этот символ
__all__ = ["Client", "ObjectNotFoundError"]
