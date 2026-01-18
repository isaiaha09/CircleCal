import os
from pathlib import PurePosixPath

from django.core.files.storage import FileSystemStorage, Storage
from django.utils.deconstruct import deconstructible


def _env_truthy(name: str) -> bool:
    val = os.getenv(name)
    return bool(val and str(val).strip())


_use_cloudinary = _env_truthy("CLOUDINARY_URL") or _env_truthy("CLOUDINARY_CLOUD_NAME")


if _use_cloudinary:
    try:
        import cloudinary
        import cloudinary.uploader
        import cloudinary.utils
    except Exception:  # pragma: no cover
        cloudinary = None  # type: ignore


    @deconstructible
    class OverwriteStorage(Storage):
        """Cloudinary-backed storage for avatars with stable keys + overwrite.

        We keep the Django "name" stable (e.g. profile_pictures/user_123/avatar.jpg)
        and upload to Cloudinary using a stable public_id derived from that name.
        """

        def _public_id_for_name(self, name: str) -> str:
            # Cloudinary public_id should not include the extension.
            # Also normalize Windows paths to posix-style.
            p = PurePosixPath(str(name).replace("\\", "/"))
            stem = os.path.splitext(str(p))[0]
            return stem

        def _open(self, name, mode="rb"):
            raise NotImplementedError("Cloudinary storage does not support file streaming via Django Storage")

        def _save(self, name, content):
            if cloudinary is None:
                raise RuntimeError("Cloudinary SDK not installed")

            public_id = self._public_id_for_name(name)
            # Ensure pointer is at start.
            try:
                content.seek(0)
            except Exception:
                pass

            result = cloudinary.uploader.upload(
                content,
                public_id=public_id,
                resource_type="image",
                overwrite=True,
                invalidate=True,
            )

            fmt = (result or {}).get("format")
            if fmt:
                return f"{public_id}.{fmt}"
            return str(name)

        def delete(self, name):
            if cloudinary is None:
                return
            public_id = self._public_id_for_name(name)
            try:
                cloudinary.uploader.destroy(public_id, resource_type="image", invalidate=True)
            except Exception:
                pass

        def exists(self, name):
            # Avoid network round-trips; allow overwrite semantics.
            return False

        def url(self, name):
            if cloudinary is None:
                raise RuntimeError("Cloudinary SDK not installed")
            public_id = self._public_id_for_name(name)
            url, _opts = cloudinary.utils.cloudinary_url(public_id, secure=True, resource_type="image")
            return url


else:
    try:
        # Firebase Storage is backed by a Google Cloud Storage bucket.
        from storages.backends.gcloud import GoogleCloudStorage
    except Exception:  # pragma: no cover
        GoogleCloudStorage = None

    if GoogleCloudStorage is not None:
        @deconstructible
        class OverwriteStorage(GoogleCloudStorage):
            """GoogleCloudStorage that overwrites existing files with the same name."""

            file_overwrite = True

    else:
        @deconstructible
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
