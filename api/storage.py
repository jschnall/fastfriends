from django.conf import settings
from django.core.files import File
from django.core.files.storage import FileSystemStorage


class UniqueNameFileStorage(FileSystemStorage):
    """
    Store file only if another of the same name does not already exist.
    Useful when naming files by hash.
    """
            
    def get_available_name(self, name):
        return name
    
    def _save(self, name, content):
        # if file exists, simply return the name
        if self.exists(name):
            return name
        return super(UniqueNameFileStorage, self)._save(name, content)
    