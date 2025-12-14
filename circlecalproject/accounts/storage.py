from django.core.files.storage import FileSystemStorage
import os


class OverwriteStorage(FileSystemStorage):
    """FileSystemStorage subclass that overwrites existing files with same name.

    This ensures saved filenames stay exactly as provided (no suffixes added).
    """

    def get_available_name(self, name, max_length=None):
        # If the filename already exists, remove it so it can be replaced.
        full_path = self.path(name)
        if os.path.exists(full_path):
            try:
                os.remove(full_path)
            except Exception:
                pass
        return name
