from flask import send_file
from openpyxl import Workbook


def export_report():

    wb = Workbook()

    ws = wb.active

    ws.append([
        "資產",
        "風險值",
        "風險等級"
    ])


    filename = "Risk_Report.xlsx"

    wb.save(filename)


    return send_file(
        filename,
        as_attachment=True
    )