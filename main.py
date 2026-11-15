"""Точка входа: AFK Farm для MMORPG «Седьмой элемент».

Запуск:  python main.py
"""

from __future__ import annotations

import shutil
import sys
import traceback


def main() -> int:
    from PySide6.QtWidgets import QApplication, QMessageBox

    from afkfarm import APP_NAME
    from afkfarm.config import Config, ConfigStore, default_config_path, load_config, save_config
    from afkfarm.logger import log, setup_logging
    from afkfarm.ui.main_window import MainWindow
    from afkfarm.ui.style import apply_theme

    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    apply_theme(app)
    setup_logging(True)

    def excepthook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        log.error("Необработанная ошибка:\n%s", text)
        QMessageBox.critical(None, "Ошибка", f"Произошла ошибка:\n\n{exc}\n\nПодробности — в журнале.")

    sys.excepthook = excepthook

    path = default_config_path()
    try:
        cfg = load_config(path)
    except Exception as exc:
        backup = path.with_suffix(".broken.json")
        try:
            shutil.copy(path, backup)
        except OSError:
            pass
        QMessageBox.warning(None, APP_NAME, f"Файл настроек повреждён ({exc}).\n"
                            f"Копия сохранена как {backup.name}, загружены значения по умолчанию.")
        cfg = Config()
    store = ConfigStore(cfg, path)
    if not path.exists():
        try:
            save_config(cfg, path)
        except OSError:
            pass

    win = MainWindow(store)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
