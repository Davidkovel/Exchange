import json
from pathlib import Path

from logger import logger


class JsonService:
    def __init__(self, file_path: str = "data.json"):
        self.file_path = Path(file_path)
        # Создаем файл при инициализации, если его нет
        if not self.file_path.exists():
            self.file_path.write_text("{}", encoding="utf-8")

    async def load_data(self) -> dict:
        """Загружает данные из JSON файла"""
        try:
            data = json.loads(self.file_path.read_text(encoding="utf-8"))
            return data
        except json.JSONDecodeError as e:
            logger.error(f"Ошибка чтения JSON: {e}")
            return {}

    async def save_data(self, data: dict) -> bool:
        """Сохраняет данные в JSON файл"""
        try:
            self.file_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=4),
                encoding="utf-8"
            )
            return True
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            return False