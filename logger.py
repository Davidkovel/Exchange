import logging

logger = logging.getLogger("my_app")
logger.setLevel(logging.ERROR)  # Логируем только ошибки и выше

# Создаём обработчик — куда писать (например, в файл)
error_handler = logging.FileHandler("error_exchange1.log", mode='a')
error_handler.setLevel(logging.ERROR)

# Настройка формата логов
formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
error_handler.setFormatter(formatter)

# Добавляем обработчик к логгеру
logger.addHandler(error_handler)