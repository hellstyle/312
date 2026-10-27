"""
Модуль для управления лотами: выборочное автоподнятие и маркировка автовыдачи.
"""

from __future__ import annotations
from typing import TYPE_CHECKING, List, Optional
import logging

if TYPE_CHECKING:
    from cardinal import Cardinal

logger = logging.getLogger("LotManager")


class LotManager:
    """Класс для управления лотами на FunPay."""

    # Ключевые слова, по которым лот считается Steam Offline активацией
    OFFLINE_KEYWORDS = [
        "offline", "оффлайн", "офлайн",
        "offline activation", "оффлайн активаци",
        "автоактивац", "steam offline",
    ]

    def __init__(self, cardinal: "Cardinal"):
        self.cardinal = cardinal
        self.auto_delivery_marker = "✔️ АВТОВЫДАЧА"

    # ------------------------------------------------------------------
    # Проверка лота / подкатегории на принадлежность к оффлайн-активациям
    # ------------------------------------------------------------------

    def is_offline_lot(self, name: str) -> bool:
        """
        Возвращает True, если название лота или подкатегории содержит
        ключевые слова Steam Offline активации.

        :param name: название лота или подкатегории
        """
        name_lower = name.lower()
        for kw in self.OFFLINE_KEYWORDS:
            if kw in name_lower:
                return True
        return False

    def has_offline_lots_in_subcat(self, subcat) -> bool:
        """
        Проверяет, есть ли в подкатегории хотя бы один лот с ключевыми словами
        оффлайн-активации OR подкатегория сама по себе является оффлайн.

        Используется в cardinal.raise_lots() для фильтрации подкатегорий.

        :param subcat: объект подкатегории FunPayAPI
        :return: True — поднимать эту подкатегорию, False — пропустить
        """
        # Если само название подкатегории содержит ключевые слова — поднимаем
        if self.is_offline_lot(subcat.name):
            return True

        # Проверяем лоты внутри этой подкатегории
        try:
            lots = self.cardinal.curr_profile.get_sorted_lots(2).get(subcat, {})
            for lot in lots.values():
                desc = ", ".join(
                    filter(None, [lot.server, lot.side, lot.description])
                )
                if self.is_offline_lot(desc):
                    return True
        except Exception as e:
            logger.debug(f"Ошибка проверки лотов в подкатегории {subcat.name}: {e}")

        return False

    def get_offline_lot_names_from_config(self) -> List[str]:
        """
        Возвращает список секций из AD_CFG, чьи названия содержат ключевые слова
        оффлайн-активации. Используется для маркировки и логирования.
        """
        result = []
        for section in self.cardinal.AD_CFG.sections():
            if self.is_offline_lot(section):
                result.append(section)
        return result

    # ------------------------------------------------------------------
    # Фильтрация словаря лотов (остаётся для совместимости / ручного вызова)
    # ------------------------------------------------------------------

    def filter_lots_for_raise(self, lots: dict) -> dict:
        """
        Фильтрует словарь лотов — оставляет только те, что относятся
        к оффлайн-активациям.

        :param lots: {lot_id: lot_shortcut}
        :return: отфильтрованный словарь
        """
        filtered = {
            lot_id: lot
            for lot_id, lot in lots.items()
            if self.is_offline_lot(
                ", ".join(filter(None, [lot.server, lot.side, lot.description]))
            )
        }
        logger.info(
            f"Лотов для автоподнятия (оффлайн): {len(filtered)} из {len(lots)}"
        )
        return filtered

    # ------------------------------------------------------------------
    # Маркировка лотов пометкой АВТОВЫДАЧА
    # ------------------------------------------------------------------

    def add_auto_delivery_marker(self, lot_name: str) -> str:
        """Добавляет пометку автовыдачи в конец названия лота."""
        if self.auto_delivery_marker in lot_name:
            return lot_name
        return f"{lot_name} {self.auto_delivery_marker}"

    def remove_auto_delivery_marker(self, lot_name: str) -> str:
        """Убирает пометку автовыдачи из названия лота."""
        return lot_name.replace(self.auto_delivery_marker, "").strip()

    def mark_lots_with_auto_delivery(self) -> int:
        """
        Добавляет пометку «✔️ АВТОВЫДАЧА» ко всем лотам, чьи описания
        совпадают с секциями в AD_CFG.

        :return: количество изменённых лотов
        """
        try:
            profile = self.cardinal.account.get_user(self.cardinal.account.id)
            lots = profile.get_sorted_lots(1)
            delivery_sections = self.cardinal.AD_CFG.sections()
            changed = 0

            for lot_id, lot in lots.items():
                matched = any(
                    sec in lot.description or lot.description in sec
                    for sec in delivery_sections
                )
                if matched and self.auto_delivery_marker not in lot.description:
                    try:
                        lot_fields = self.cardinal.account.get_lot_fields(lot.id)
                        lot_fields.description = self.add_auto_delivery_marker(
                            lot_fields.description
                        )
                        self.cardinal.account.save_lot(lot_fields)
                        changed += 1
                        logger.info(
                            f"Добавлена пометка автовыдачи: {lot.description}"
                        )
                    except Exception as e:
                        logger.error(
                            f"Ошибка при добавлении пометки к лоту «{lot.description}»: {e}"
                        )

            logger.info(f"Маркировка завершена. Изменено лотов: {changed}")
            return changed

        except Exception as e:
            logger.error(f"Ошибка при маркировке лотов: {e}")
            return 0


# ----------------------------------------------------------------------
# Глобальный экземпляр
# ----------------------------------------------------------------------

_lot_manager: Optional[LotManager] = None


def init_lot_manager(cardinal: "Cardinal") -> LotManager:
    """Инициализирует и возвращает глобальный LotManager."""
    global _lot_manager
    _lot_manager = LotManager(cardinal)
    return _lot_manager


def get_lot_manager() -> Optional[LotManager]:
    """Возвращает глобальный LotManager (или None до инициализации)."""
    return _lot_manager
