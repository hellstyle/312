#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Excel экспорт бухгалтерии клубного бота.
"""

import io
import logging
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger("ClubBot.Excel")


def export_day(day: Optional[str] = None) -> io.BytesIO:
    """Экспорт за один день."""
    from club_bot.billing import _load_ledger, CURRENCY, OWNER_SHARE, CLUB_SHARE
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError("openpyxl не установлен. Выполните: pip install openpyxl")

    day_key = day or date.today().isoformat()
    ledger  = _load_ledger()
    data    = ledger.get(day_key, {})
    sessions = data.get("sessions", [])

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = f"Отчёт {day_key}"

    # Стили
    header_font    = Font(bold=True, color="FFFFFF", size=11)
    header_fill    = PatternFill("solid", fgColor="2F4F8F")
    subheader_fill = PatternFill("solid", fgColor="4A90D9")
    total_fill     = PatternFill("solid", fgColor="E8F4FD")
    salary_fill    = PatternFill("solid", fgColor="D4EDDA")
    center         = Alignment(horizontal="center", vertical="center")
    thin_border    = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    def style_cell(cell, bold=False, fill=None, align=True):
        if bold:
            cell.font = Font(bold=True, size=10)
        if fill:
            cell.fill = fill
        if align:
            cell.alignment = center
        cell.border = thin_border

    # Заголовок отчёта
    ws.merge_cells("A1:I1")
    ws["A1"] = f"Отчёт за {day_key}"
    ws["A1"].font = Font(bold=True, size=14, color="FFFFFF")
    ws["A1"].fill = header_fill
    ws["A1"].alignment = center
    ws.row_dimensions[1].height = 30

    # Заголовки колонок
    headers = ["№", "Игра", "Аккаунт", "Игрок", "Часы",
               f"Ставка ({CURRENCY}/ч)", f"База ({CURRENCY})",
               f"Скидка ({CURRENCY})", f"Итого ({CURRENCY})",
               f"Владелец ({CURRENCY})", f"Клуб ({CURRENCY})"]
    ws.append([])  # пустая строка
    ws.append(headers)

    for col_num, header in enumerate(headers, 1):
        cell = ws.cell(row=3, column=col_num)
        cell.font = header_font
        cell.fill = subheader_fill
        cell.alignment = center
        cell.border = thin_border
    ws.row_dimensions[3].height = 20

    # Данные сессий
    for i, e in enumerate(sessions, 1):
        owner = e.get("owner_share", round(e["total"] * OWNER_SHARE, 2))
        club  = e.get("club_share",  round(e["total"] * CLUB_SHARE, 2))
        row   = [
            i,
            e["pool"].upper(),
            e["acc_username"],
            str(e["player_id"]),
            e["hours"],
            e["rate"],
            e["base"],
            e.get("discount", 0),
            e["total"],
            owner,
            club,
        ]
        ws.append(row)
        row_idx = ws.max_row
        for col in range(1, 12):
            cell = ws.cell(row=row_idx, column=col)
            cell.alignment = center
            cell.border = thin_border
            if col in (9, 10, 11):
                cell.number_format = f'#,##0.00 "{CURRENCY}"'

    # Итоговая строка
    if sessions:
        total       = data.get("total", 0)
        owner_total = data.get("owner_total", round(total * OWNER_SHARE, 2))
        club_total  = data.get("club_total",  round(total * CLUB_SHARE, 2))
        ws.append([])
        total_row = ws.max_row + 1

        ws.cell(row=total_row, column=1, value="ИТОГО")
        ws.cell(row=total_row, column=5, value=sum(e["hours"] for e in sessions))
        ws.cell(row=total_row, column=9, value=total)
        ws.cell(row=total_row, column=10, value=owner_total)
        ws.cell(row=total_row, column=11, value=club_total)

        for col in range(1, 12):
            cell = ws.cell(row=total_row, column=col)
            cell.font = Font(bold=True, size=11)
            cell.fill = total_fill
            cell.alignment = center
            cell.border = thin_border

        # Блок зарплат
        ws.append([])
        salary_row = ws.max_row + 1

        ws.merge_cells(f"A{salary_row}:D{salary_row}")
        ws.cell(row=salary_row, column=1, value="💼 Зарплата владельца")
        ws.cell(row=salary_row, column=1).font = Font(bold=True)
        ws.cell(row=salary_row, column=1).fill = salary_fill
        ws.cell(row=salary_row, column=1).alignment = center
        ws.merge_cells(f"E{salary_row}:F{salary_row}")
        ws.cell(row=salary_row, column=5, value=f"{owner_total} {CURRENCY}")
        ws.cell(row=salary_row, column=5).font = Font(bold=True, color="1A6B1A")
        ws.cell(row=salary_row, column=5).fill = salary_fill
        ws.cell(row=salary_row, column=5).alignment = center

        club_row = salary_row + 1
        ws.merge_cells(f"A{club_row}:D{club_row}")
        ws.cell(row=club_row, column=1, value="🏢 Зарплата клуба")
        ws.cell(row=club_row, column=1).font = Font(bold=True)
        ws.cell(row=club_row, column=1).fill = salary_fill
        ws.cell(row=club_row, column=1).alignment = center
        ws.merge_cells(f"E{club_row}:F{club_row}")
        ws.cell(row=club_row, column=5, value=f"{club_total} {CURRENCY}")
        ws.cell(row=club_row, column=5).font = Font(bold=True, color="1A6B1A")
        ws.cell(row=club_row, column=5).fill = salary_fill
        ws.cell(row=club_row, column=5).alignment = center

    # Ширина колонок
    col_widths = [5, 8, 18, 14, 7, 14, 14, 14, 14, 16, 12]
    for i, width in enumerate(col_widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def export_period(days: int = 7) -> io.BytesIO:
    """Экспорт за период — один лист на день + сводный лист."""
    from club_bot.billing import _load_ledger, CURRENCY, OWNER_SHARE, CLUB_SHARE
    try:
        import openpyxl
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError("openpyxl не установлен.")

    ledger  = _load_ledger()
    today   = date.today()
    wb      = openpyxl.Workbook()

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="2F4F8F")
    total_fill  = PatternFill("solid", fgColor="D4EDDA")
    center      = Alignment(horizontal="center")
    thin        = Border(
        left=Side(style="thin"), right=Side(style="thin"),
        top=Side(style="thin"), bottom=Side(style="thin")
    )

    # Сводный лист
    ws_sum = wb.active
    ws_sum.title = "Сводка"
    summary_headers = ["Дата", "Сессий", f"Выручка ({CURRENCY})",
                       f"Владелец ({CURRENCY})", f"Клуб ({CURRENCY})"]
    ws_sum.append(summary_headers)
    for col in range(1, 6):
        cell = ws_sum.cell(row=1, column=col)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = center
        cell.border = thin

    grand_total = grand_owner = grand_club = 0.0
    grand_sessions = 0

    for i in range(days - 1, -1, -1):
        day_key  = (today - timedelta(days=i)).isoformat()
        data     = ledger.get(day_key, {})
        sessions = data.get("sessions", [])
        if not sessions:
            ws_sum.append([day_key, 0, 0, 0, 0])
            continue

        day_total = data.get("total", 0)
        day_owner = data.get("owner_total", round(day_total * OWNER_SHARE, 2))
        day_club  = data.get("club_total",  round(day_total * CLUB_SHARE, 2))
        cnt       = len(sessions)

        ws_sum.append([day_key, cnt, day_total, day_owner, day_club])
        grand_total   += day_total
        grand_owner   += day_owner
        grand_club    += day_club
        grand_sessions += cnt

        # Отдельный лист на день
        ws_day = wb.create_sheet(title=day_key)
        day_headers = ["№", "Игра", "Аккаунт", "Часы",
                       f"Итого ({CURRENCY})", f"Владелец ({CURRENCY})", f"Клуб ({CURRENCY})"]
        ws_day.append(day_headers)
        for col in range(1, 8):
            cell = ws_day.cell(row=1, column=col)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            cell.border = thin

        for j, e in enumerate(sessions, 1):
            owner = e.get("owner_share", round(e["total"] * OWNER_SHARE, 2))
            club  = e.get("club_share",  round(e["total"] * CLUB_SHARE, 2))
            ws_day.append([j, e["pool"].upper(), e["acc_username"],
                           e["hours"], e["total"], owner, club])

        for col in [1, 2, 3, 4, 5, 6, 7]:
            ws_day.column_dimensions[get_column_letter(col)].width = 14

    # Итоговая строка сводки
    ws_sum.append(["ИТОГО", grand_sessions,
                   round(grand_total, 2), round(grand_owner, 2), round(grand_club, 2)])
    total_row = ws_sum.max_row
    for col in range(1, 6):
        cell = ws_sum.cell(row=total_row, column=col)
        cell.font = Font(bold=True)
        cell.fill = total_fill
        cell.alignment = center
        cell.border = thin

    for col in [1, 2, 3, 4, 5]:
        ws_sum.column_dimensions[get_column_letter(col)].width = 18

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
