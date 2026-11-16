"""Tick and Book - turn the CMB route booking test Excel (7 Nov 2026) into the
list of routes / airlines / GDS-NDC flights the search tests check
(e2e/data/cmb-routes-2026-11-07.json).

    py e2e/tools/excel_to_routes.py "<path>/CMB-route-booking-test-2026-11-07.xlsx"

Only the route, country, airline, GDS/NDC, flight number and the 7 Nov result
are copied. PNRs, ticket numbers, payment and card details are left out.
"""
import json, pathlib, sys

import openpyxl

src = pathlib.Path(sys.argv[1])
out = pathlib.Path(__file__).resolve().parent.parent / "data" / "cmb-routes-2026-11-07.json"
wb = openpyxl.load_workbook(src, data_only=True)
rows = []
for sheet in ("Ticketed", "Not ticketed"):
    ws = wb[sheet]
    head = [c.value for c in ws[1]]
    col = {name: head.index(name) for name in head if name}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if not r[col["To"]]:
            continue
        result = "Ticketed" if sheet == "Ticketed" else (r[col["Result"]] or "Not ticketed")
        rows.append({
            "no": int(r[col["No."]]), "to": r[col["To"]], "city": r[col["City"]], "country": r[col["Country"]],
            "airline": r[col["Airline"]], "code": r[col["Code"]], "channel": r[col["GDS / NDC"]],
            "flight_7nov": r[col["Flight"]] or "", "result_7nov": result,
        })
rows.sort(key=lambda x: (x["no"], x["airline"], x["channel"]))
out.write_text(json.dumps({
    "source": src.name, "origin": "CMB", "date": "2026-11-27", "adults": 1, "trip": "one-way",
    "flights": rows,
}, indent=1, ensure_ascii=False), encoding="utf-8")
gds = sum(1 for x in rows if x["channel"] == "GDS"); ndc = sum(1 for x in rows if x["channel"] == "NDC")
print(f"{len(rows)} flights ({gds} GDS, {ndc} NDC), {len({x['to'] for x in rows})} destinations -> {out}")
