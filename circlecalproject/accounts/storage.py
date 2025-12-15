from django.core.files.storage import FileSystemStorage
import os


class OverwriteStorage(FileSystemStorage):
    """FileSystemStorage that overwrites existing files with same name."""

    def get_available_name(self, name, max_length=None):
        full_path = self.path(name)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass
        return name
