"""Tick and Book - build the CMB route search test report (Excel) from the
saved Playwright route searches (16/11/2026).

    py e2e/tools/route_report.py <run-id> [output.xlsx]

Reads e2e/data/cmb-routes-2026-11-07.json (what to check, from the 7 Nov
booking test) and e2e/route-results/<run-id>/*.json (what Travelport offered).
Search only: nothing was selected, booked, paid or ticketed.
"""
import collections
import json
import pathlib
import sys
from datetime import datetime

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.table import Table, TableStyleInfo

E2E = pathlib.Path(__file__).resolve().parents[1]
run_id = sys.argv[1]
data = json.loads((E2E / "data" / "cmb-routes-2026-11-07.json").read_text(encoding="utf-8"))
res_dir = E2E / "route-results" / run_id
searches = {}
for f in sorted(res_dir.glob("*.json")):
    r = json.loads(f.read_text(encoding="utf-8"))
    searches[r["to"]] = r
date = data["date"]
out = pathlib.Path(sys.argv[2]) if len(sys.argv) > 2 else E2E / "route-report" / f"CMB-route-search-test-{date}.xlsx"
out.parent.mkdir(parents=True, exist_ok=True)

OFFERED_SAME = "Offered - same flight as 7 Nov"
OFFERED_OTHER = "Offered - other flight"
NOT_OFFERED = "Not offered in search"
SEARCH_FAILED = "Search failed"
NOT_SEARCHED = "Not searched"
RESULTS = [OFFERED_SAME, OFFERED_OTHER, NOT_OFFERED, SEARCH_FAILED, NOT_SEARCHED]

ARIAL = "Arial"
F_BASE = Font(name=ARIAL, size=10)
F_BOLD = Font(name=ARIAL, size=10, bold=True)
F_HEAD = Font(name=ARIAL, size=10, bold=True, color="FFFFFF")
F_TITLE = Font(name=ARIAL, size=14, bold=True)
F_SUB = Font(name=ARIAL, size=10, italic=True, color="555555")
FILL_HEAD = PatternFill("solid", fgColor="1F4E78")
FILL_OK = PatternFill("solid", fgColor="E2EFDA")
FILL_OK2 = PatternFill("solid", fgColor="FFF2CC")
FILL_BAD = PatternFill("solid", fgColor="FCE4D6")
THIN = Side(style="thin", color="BFBFBF")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def money(v):
    return round(float(v)) if isinstance(v, (int, float)) else None


def flights_of(offer):
    return [x.strip() for x in (offer.get("flights") or "").replace(" ", "").split("+") if x.strip()]


# ---- one row per flight from the 7 Nov list --------------------------------
rows, fare_rows = [], []
for f in data["flights"]:
    s = searches.get(f["to"])
    base = [f["no"], f["to"], f["city"], f["country"], f["airline"], f["code"], f["channel"], f["flight_7nov"], f["result_7nov"]]
    if not s:
        rows.append(base + [NOT_SEARCHED, 0] + [None] * 17)
        continue
    if s["status"] != 200 and "NO OFFERS FOUND" in (s.get("error") or "").upper():
        # Travelport's own answer "no fares for this route/date", not a fault.
        rows.append(base + [NOT_OFFERED, 0] + [None] * 16 +
                    ["Travelport found no fares at all on this route for " + date + " (NO OFFERS FOUND FOR THE "
                     "CHANNEL). The website shows this as 'temporarily unavailable' instead of 'no flights'."])
        continue
    if s["status"] != 200:
        rows.append(base + [SEARCH_FAILED, 0] + [None] * 16 + [f"HTTP {s['status']}: {s.get('error', '')}"[:200]])
        continue
    mine = [o for o in s["offers"] if o["code"] == f["code"] and o["channel"] == f["channel"]]
    if not mine:
        rows.append(base + [NOT_OFFERED, 0] + [None] * 16 +
                    ["Travelport did not offer this airline with this content on this route for " + date])
        continue
    same = bool(f["flight_7nov"]) and any(f["flight_7nov"] in flights_of(o) for o in mine)
    best = min(mine, key=lambda o: o["price"] if isinstance(o.get("price"), (int, float)) else 1e15)
    fo = (best.get("fare_options") or [{}])[0]
    other_attrs = "; ".join(a for a in (fo.get("attributes") or "").split("; ")
                            if a and not a.startswith(("CarryOn", "SeatAssignment", "Meals")))
    rows.append(base + [
        OFFERED_SAME if same else OFFERED_OTHER, len(mine), best["flights"], best["route"],
        (best.get("departure") or "").replace("T", " ")[:16], (best.get("arrival") or "").replace("T", " ")[:16],
        best.get("stops"), (best.get("duration") or "").replace("PT", "").lower(), fo.get("brand") or "",
        money(best.get("price")), fo.get("bags") or "", fo.get("carry_on") or "", fo.get("seat") or "",
        fo.get("meals") or "", fo.get("change") or "", fo.get("cancel") or "", other_attrs,
        "Yes" if best.get("bookable") else "No", "",
    ])
    for opt in best.get("fare_options") or []:
        fare_rows.append([f["no"], f["to"], f["city"], f["airline"], f["code"], f["channel"], best["flights"],
                          opt.get("brand") or "", opt.get("cabin") or "", money(opt.get("price")), opt.get("bags") or "",
                          opt.get("carry_on") or "", opt.get("seat") or "", opt.get("meals") or "",
                          opt.get("change") or "", opt.get("cancel") or "", opt.get("booking_class") or "",
                          opt.get("fare_basis") or "", opt.get("attributes") or ""])

HEAD = ["No.", "To", "City", "Country", "Airline", "Code", "GDS / NDC", "7 Nov flight", "7 Nov result",
        "27 Nov search result", "Options found", "Cheapest flight(s)", "Route", "Departure", "Arrival", "Stops",
        "Duration", "Fare (brand)", "Price (LKR)", "Checked bags", "Carry-on", "Seat choice", "Meals",
        "Change fee", "Cancel fee", "Other fare rules", "Bookable", "Note"]
assert all(len(r) == len(HEAD) for r in rows), {len(r) for r in rows}

# ---- other airlines offered (not in the 7 Nov list) -------------------------
known = {(f["to"], f["code"], f["channel"]) for f in data["flights"]}
dest_info = {f["to"]: (f["no"], f["city"], f["country"]) for f in data["flights"]}
new_rows = []
for to, s in searches.items():
    groups = collections.defaultdict(list)
    for o in s.get("offers", []):
        if (to, o["code"], o["channel"]) not in known:
            groups[(o["code"], o["channel"])].append(o)
    for (code, ch), offers in sorted(groups.items()):
        best = min(offers, key=lambda o: o["price"] if isinstance(o.get("price"), (int, float)) else 1e15)
        no, city, country = dest_info[to]
        new_rows.append([no, to, city, country, best["airline"], code, ch, len(offers), best["flights"], best["route"],
                         money(best.get("price")), ((best.get("fare_options") or [{}])[0]).get("brand") or ""])
new_rows.sort()

# ---- per search --------------------------------------------------------------
search_rows = []
for to, (no, city, country) in sorted(dest_info.items(), key=lambda x: x[1][0]):
    s = searches.get(to)
    if not s:
        search_rows.append([no, to, city, country, "not searched", None, None, None, None, None, ""])
        continue
    offers = s.get("offers", [])
    search_rows.append([no, to, city, country, s["status"], s.get("seconds"), len(offers),
                        sum(1 for o in offers if o["channel"] == "GDS"), sum(1 for o in offers if o["channel"] == "NDC"),
                        len({o["code"] for o in offers}), s.get("error", "")])

# ---- write the workbook --------------------------------------------------------
wb = Workbook()
wb.calculation.fullCalcOnLoad = True


def sheet(title, head, body, widths, table_name, fills_col=None):
    ws = wb.create_sheet(title)
    ws.append(head)
    for r in body:
        ws.append(r)
    for c in ws[1]:
        c.font, c.fill, c.alignment, c.border = F_HEAD, FILL_HEAD, Alignment(wrap_text=True, vertical="center"), BOX
    for row in ws.iter_rows(min_row=2):
        for c in row:
            c.font, c.border = F_BASE, BOX
            c.alignment = Alignment(vertical="top", wrap_text=isinstance(c.value, str) and len(c.value) > 30)
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    ws.freeze_panes = "A2"
    if body:
        ref = f"A1:{get_column_letter(len(head))}{len(body) + 1}"
        t = Table(displayName=table_name, ref=ref)
        t.tableStyleInfo = TableStyleInfo(name="TableStyleLight9", showRowStripes=False)
        ws.add_table(t)
    if fills_col:
        for row in ws.iter_rows(min_row=2, min_col=fills_col, max_col=fills_col):
            c = row[0]
            c.fill = FILL_OK if c.value == OFFERED_SAME else FILL_OK2 if c.value == OFFERED_OTHER else FILL_BAD
    return ws


ws_f = sheet("Flights", HEAD, rows,
             [5, 6, 13, 12, 20, 6, 9, 11, 22, 26, 9, 18, 14, 16, 16, 6, 9, 22, 12, 22, 11, 12, 10, 26, 26, 30, 9, 40],
             "Flights", fills_col=10)
for row in ws_f.iter_rows(min_row=2, min_col=19, max_col=19):
    row[0].number_format = "#,##0"
not_rows = [r for r in rows if r[9] not in (OFFERED_SAME, OFFERED_OTHER)]
sheet("Not offered", HEAD[:10] + ["Note"], [r[:10] + [r[27]] for r in not_rows],
      [5, 6, 13, 12, 20, 6, 9, 11, 22, 24, 60], "NotOffered")
sheet("New in search", ["No.", "To", "City", "Country", "Airline", "Code", "GDS / NDC", "Options found",
                        "Cheapest flight(s)", "Route", "Price (LKR)", "Fare (brand)"], new_rows,
      [5, 6, 13, 12, 22, 6, 9, 9, 18, 14, 12, 22], "NewInSearch")
sheet("Fares (bags, seats, meals)", ["No.", "To", "City", "Airline", "Code", "GDS / NDC", "Flight(s)", "Fare (brand)",
                                     "Cabin", "Price (LKR)", "Checked bags", "Carry-on", "Seat choice", "Meals",
                                     "Change fee", "Cancel fee", "Booking class", "Fare basis", "All fare rules"],
      fare_rows, [5, 6, 13, 20, 6, 9, 18, 22, 10, 12, 22, 11, 12, 10, 26, 26, 8, 14, 50], "Fares")
sheet("Searches", ["No.", "To", "City", "Country", "HTTP status", "Seconds", "Travelport options", "GDS options",
                   "NDC options", "Airlines", "Error"], search_rows, [5, 6, 13, 12, 10, 9, 11, 10, 10, 9, 50], "Searches")

# ---- Summary (formulas over the Flights sheet) ----------------------------------
ws = wb.active
ws.title = "Summary"
ws.column_dimensions["A"].width = 44
for col in "BCD":
    ws.column_dimensions[col].width = 14
searched_at = min((s.get("searched_at") or "" for s in searches.values()), default="")
ws["A1"] = f"CMB route search test: {len(dest_info)} destinations, every airline from the 7 Nov test"
ws["A1"].font = F_TITLE
ws["A2"] = (f"Tick and Book - Travelport pre-production - SEARCH ONLY - CMB one-way, 1 adult, "
            f"flights on {datetime.fromisoformat(date).strftime('%d %b %Y')} - searched "
            f"{searched_at[:16].replace('T', ' ')} UTC")
ws["A3"] = ("Each route was searched once on the website the way a customer does it. Nothing was selected, "
            "booked, paid or ticketed (Tick and Book rule: bookings are made by a person, by hand).")
for c in ("A2", "A3"):
    ws[c].font = F_SUB

R = "Flights!$J:$J"   # 27 Nov search result
CH = "Flights!$G:$G"  # GDS / NDC
AL = "Flights!$F:$F"  # airline code
N7 = "Flights!$I:$I"  # 7 Nov result


def table_head(row, *labels):
    for i, lab in enumerate(labels):
        c = ws.cell(row=row, column=1 + i, value=lab)
        c.font, c.fill, c.border = F_HEAD, FILL_HEAD, BOX


def put(row, label, *formulas, fmt=None, bold=False):
    a = ws.cell(row=row, column=1, value=label)
    a.font, a.border = (F_BOLD if bold else F_BASE), BOX
    for i, fx in enumerate(formulas):
        c = ws.cell(row=row, column=2 + i, value=fx)
        c.font, c.border = (F_BOLD if bold else F_BASE), BOX
        if fmt and i == len(formulas) - 1:
            c.number_format = fmt


row = 5
table_head(row, "27 Nov search result", "Count")
first = row + 1
for lab in RESULTS:
    row += 1
    put(row, lab, f'=COUNTIF({R},"{lab}")')
row += 1
put(row, "Total tested", f"=SUM(B{first}:B{row - 1})", bold=True)
total_row = row
row += 1
put(row, "Offered (either)", f"=B{first}+B{first + 1}", bold=True)
row += 1
put(row, "Offered %", f"=IF(B{total_row}=0,0,B{row - 1}/B{total_row})", fmt="0.0%", bold=True)

row += 2
table_head(row, "Content", "Tested", "Offered", "Offered %")
for ch in ("GDS", "NDC"):
    row += 1
    put(row, ch, f'=COUNTIF({CH},"{ch}")',
        f'=COUNTIFS({CH},"{ch}",{R},"{OFFERED_SAME}")+COUNTIFS({CH},"{ch}",{R},"{OFFERED_OTHER}")',
        f"=IF(B{row}=0,0,C{row}/B{row})", fmt="0.0%")
ws.cell(row=row + 1, column=1, value=f"7 Nov list: {sum(1 for f in data['flights'] if f['channel'] == 'GDS')} GDS + "
        f"{sum(1 for f in data['flights'] if f['channel'] == 'NDC')} NDC = {len(data['flights'])} flights "
        f"(from {data['source']})").font = F_SUB
row += 1

row += 2
table_head(row, "Airline", "Tested", "Offered", "Offered %")
airlines = collections.Counter((f["airline"], f["code"]) for f in data["flights"])
for (name, code), _n in airlines.most_common():
    row += 1
    put(row, f"{name} ({code})", f'=COUNTIF({AL},"{code}")',
        f'=COUNTIFS({AL},"{code}",{R},"{OFFERED_SAME}")+COUNTIFS({AL},"{code}",{R},"{OFFERED_OTHER}")',
        f"=IF(B{row}=0,0,C{row}/B{row})", fmt="0.0%")

row += 2
table_head(row, "Compared with the 7 Nov booking test", "Count")
for lab, fx in (
    ("Ticketed on 7 Nov, offered again now", f'=COUNTIFS({N7},"Ticketed",{R},"{OFFERED_SAME}")+COUNTIFS({N7},"Ticketed",{R},"{OFFERED_OTHER}")'),
    ("Ticketed on 7 Nov, NOT offered now", f'=COUNTIFS({N7},"Ticketed",{R},"{NOT_OFFERED}")'),
    ("Not ticketed on 7 Nov, offered now", f'=COUNTIFS({N7},"<>Ticketed",{R},"{OFFERED_SAME}")+COUNTIFS({N7},"<>Ticketed",{R},"{OFFERED_OTHER}")'),
    ("Not ticketed on 7 Nov, still not offered", f'=COUNTIFS({N7},"<>Ticketed",{R},"{NOT_OFFERED}")'),
    ("Other airlines offered now (sheet 'New in search')", f"=COUNTA('New in search'!$A:$A)-1"),
):
    row += 1
    put(row, lab, fx)

ok_secs = [s.get("seconds") for s in searches.values() if s.get("status") == 200 and s.get("seconds")]
row += 2
ws.cell(row=row, column=1, value="Notes").font = F_BOLD
notes = [
    "Seat maps and paid extras (seats, bags, meals to buy) are only available after a flight is selected and a "
    "booking is started, so they were not fetched. What each fare includes (checked bags, carry-on, seat choice, "
    "meals, change and cancel fees) is taken from the search and listed on 'Flights' and 'Fares (bags, seats, meals)'.",
    "'Offered - other flight': the airline was offered on the route with this content (GDS/NDC), but not the 7 Nov "
    "flight number (schedules change). 'Not offered': Travelport returned no option for that airline and content.",
    "Travelport pre-production (test system). Searches only count towards look-to-book on the live system.",
    f"Search time: {min(ok_secs):.1f} to {max(ok_secs):.1f} seconds, average {sum(ok_secs) / len(ok_secs):.1f} s "
    f"({len(ok_secs)} of {len(dest_info)} searches answered)." if ok_secs else "No search answered.",
    "Counts on this sheet are formulas over the 'Flights' sheet; Excel calculates them when the file opens.",
]
for n in notes:
    row += 1
    c = ws.cell(row=row, column=1, value=n)
    c.font, c.alignment = F_BASE, Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=4)
    ws.row_dimensions[row].height = 42
ws["B5"].comment = Comment("Counted from column J ('27 Nov search result') on the Flights sheet.", "Tick and Book")

wb.save(out)

# ---- the same counts in Python, to check the formulas ---------------------------
cnt = collections.Counter(r[9] for r in rows)
by_ch = {ch: (sum(1 for r in rows if r[6] == ch), sum(1 for r in rows if r[6] == ch and r[9] in (OFFERED_SAME, OFFERED_OTHER)))
         for ch in ("GDS", "NDC")}
print(json.dumps({"file": str(out), "results": {k: cnt.get(k, 0) for k in RESULTS}, "total": len(rows),
                  "gds_tested_offered": by_ch["GDS"], "ndc_tested_offered": by_ch["NDC"],
                  "ticketed_7nov_not_offered": sum(1 for r in rows if r[8] == "Ticketed" and r[9] == NOT_OFFERED),
                  "new_airline_rows": len(new_rows), "fare_rows": len(fare_rows),
                  "searches_ok": len(ok_secs)}, indent=1))
