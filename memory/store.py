import json
from pathlib import Path


class MemoryStore:
    def __init__(self, path="memory/data.json"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, key, value):
        data = self.load_all()
        data[key] = value

        with self.path.open("w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def get(self, key, default=None):
        data = self.load_all()
        return data.get(key, default)

    def load_all(self):
        if not self.path.exists():
            return {}

        with self.path.open("r", encoding="utf-8") as file:
            return json.load(file)
