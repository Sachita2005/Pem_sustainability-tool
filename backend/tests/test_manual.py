"""Manual entry: storage in Excel, validation, locking, hand-typed rows, and the combined view.

Entries are written to a temp folder (manual.STORAGE_ROOT is patched), never to the real source
folder. The 'combined' tests read the real workbooks; the expected values were checked by hand.
"""
import datetime as dt
import sys

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from pem.domains import manual

# The real entry workbooks as they were when this test module was loaded: whether each exists,
# and when it was last written. The tests must leave them exactly so.
_REAL_FILES_BEFORE = {
    p: (manual.storage_path(p).stat().st_mtime_ns if manual.storage_path(p).is_file() else None)
    for p in manual.PARAMETERS
}


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(manual, "STORAGE_ROOT", tmp_path)
    app = FastAPI()
    app.include_router(manual.router)
    return TestClient(app)


def _post(client, parameter="diesel", **body):
    payload = {"plant": "TCOE", "month": "2026-04", "value": 12.5, **body}
    return client.post(f"/api/manual/{parameter}", json=payload)


def _hand_file(tmp_path, parameter, rows, header=("Plant", "Month (YYYY-MM)", "Value", "Unit", "Note", "Entered at", "Entry ID")):
    folder = tmp_path / manual.PARAMETERS[parameter]["folder"]
    folder.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.append(list(header))
    for row in rows:
        ws.append(list(row))
    path = folder / manual.PARAMETERS[parameter]["file"]
    wb.save(path)
    return path


# --------------------------------------------------------------------------- parameters

def test_parameters_lists_four_without_tbal(client):
    data = client.get("/api/manual/parameters").json()["parameters"]
    assert [p["id"] for p in data] == ["diesel", "manpower", "revenue", "afforestation"]
    units = {p["id"]: p["unit"] for p in data}
    assert units == {"diesel": "KL", "manpower": "headcount", "revenue": "₹ Cr", "afforestation": "No."}
    for p in data:
        ids = [pl["id"] for pl in p["plants"]]
        assert "TBAL" not in ids and len(ids) == 7


# --------------------------------------------------------------------------- add / replace / delete

def test_add_creates_file_and_lists(client, tmp_path):
    res = _post(client, note="  from meter  ")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["replaced"] is False and body["entry"]["fy"] == "FY26-27" and body["entry"]["note"] == "from meter"
    path = tmp_path / "diesel" / "Diesel - manual entries.xlsx"
    assert path.is_file()
    ws = load_workbook(path).active
    assert [c.value for c in ws[1]] == manual.HEADERS
    assert [ws.cell(2, c).value for c in (1, 2, 3, 4)] == ["TCOE", "2026-04", 12.5, "KL"]
    listed = client.get("/api/manual/diesel").json()
    assert listed["fileExists"] and len(listed["entries"]) == 1
    assert listed["entries"][0]["id"] == body["entry"]["id"]


def test_plant_spelling_and_filters(client):
    assert _post(client, plant="TASL-Nagpur", month="2026-04").status_code == 200
    assert _post(client, plant="TCOE-Hyd", month="2025-05").status_code == 200
    assert client.get("/api/manual/diesel?plant=ngp").json()["entries"][0]["plant"] == "TASL-NGP"
    assert [e["plant"] for e in client.get("/api/manual/diesel?fy=FY25-26").json()["entries"]] == ["TCOE"]
    assert client.get("/api/manual/diesel?fy=banana").status_code == 400


def test_second_entry_same_plant_month_replaces(client, tmp_path):
    first = _post(client, value=1).json()
    second = _post(client, value=2, note="fixed").json()
    assert second["replaced"] is True and second["entry"]["id"] == first["entry"]["id"]
    entries = client.get("/api/manual/diesel").json()["entries"]
    assert len(entries) == 1 and entries[0]["value"] == 2 and entries[0]["note"] == "fixed"
    assert load_workbook(tmp_path / "diesel" / "Diesel - manual entries.xlsx").active.max_row == 2


def test_delete(client):
    a = _post(client, month="2026-04").json()["entry"]["id"]
    b = _post(client, month="2026-05").json()["entry"]["id"]
    assert client.delete(f"/api/manual/diesel/{a}").status_code == 200
    assert [e["id"] for e in client.get("/api/manual/diesel").json()["entries"]] == [b]
    assert client.delete(f"/api/manual/diesel/{a}").status_code == 404
    assert client.delete("/api/manual/revenue/nope").status_code == 404   # no file yet


def test_parameters_use_their_own_files(client, tmp_path):
    _post(client, "manpower", value=100)
    _post(client, "revenue", value=3.5)
    _post(client, "afforestation", value=50)
    assert (tmp_path / "man power" / "Manpower - manual entries.xlsx").is_file()
    assert (tmp_path / "revenue" / "Revenue - manual entries.xlsx").is_file()
    assert (tmp_path / "Afforestation" / "Afforestation - manual entries.xlsx").is_file()
    assert client.get("/api/manual/diesel").json()["entries"] == []


# --------------------------------------------------------------------------- validation

@pytest.mark.parametrize("plant", ["TBAL", "TBAL-Hyd", "Tata Boeing"])
def test_tbal_rejected(client, tmp_path, plant):
    res = _post(client, plant=plant)
    assert res.status_code == 400 and "TBAL" in res.json()["detail"]
    assert not (tmp_path / "diesel").exists()
    assert client.get("/api/manual/diesel?plant=TBAL").status_code == 400


@pytest.mark.parametrize("month", ["2026-13", "2026-4", "Apr-26", "", "2026-00", None, 202604])
def test_bad_month(client, month):
    res = _post(client, month=month)
    assert res.status_code == 400 and "YYYY-MM" in res.json()["detail"]


@pytest.mark.parametrize("value", [-1, "abc", None, "", "nan", True])
def test_bad_value(client, value):
    assert _post(client, value=value).status_code == 400


def test_unknown_plant_and_parameter(client):
    assert _post(client, plant="Mars").status_code == 400
    assert client.get("/api/manual/water").status_code == 404
    assert client.post("/api/manual/water", json={}).status_code == 404


def test_manpower_must_be_whole(client):
    assert _post(client, "manpower", value=10.5).status_code == 400
    assert _post(client, "manpower", value="10").status_code == 200


def test_zero_value_is_allowed(client):
    assert _post(client, value=0).status_code == 200


# --------------------------------------------------------------------------- file locked in Excel

def test_locked_file_on_save_gives_409(client, monkeypatch):
    assert _post(client).status_code == 200

    def boom(self, *args, **kwargs):
        raise PermissionError(13, "Permission denied")
    monkeypatch.setattr("openpyxl.workbook.workbook.Workbook.save", boom)
    res = _post(client, value=99)
    assert res.status_code == 409 and "close" in res.json()["detail"].lower()
    entry_id = client.get("/api/manual/diesel").json()["entries"][0]["id"]
    res = client.delete(f"/api/manual/diesel/{entry_id}")
    assert res.status_code == 409 and "Excel" in res.json()["detail"]


def test_replace_onto_open_file_gives_409_and_leaves_no_temp(client, tmp_path, monkeypatch):
    _post(client)
    from pathlib import Path

    def boom(self, target):
        raise PermissionError(32, "in use")
    monkeypatch.setattr(Path, "replace", boom)
    assert _post(client, value=5).status_code == 409
    assert [f.name for f in (tmp_path / "diesel").iterdir()] == ["Diesel - manual entries.xlsx"]


@pytest.mark.skipif(sys.platform != "win32", reason="needs Windows file-sharing semantics")
def test_really_locked_file_gives_409(client, tmp_path):
    """Hold the file open with no sharing, as Excel does, and check the app answers 409 not 500."""
    import ctypes
    from ctypes import wintypes
    _post(client)
    path = tmp_path / "diesel" / "Diesel - manual entries.xlsx"
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    handle = k32.CreateFileW(str(path), 0x80000000 | 0x40000000, 0, None, 3, 0x80, None)   # read+write, share none, OPEN_EXISTING
    assert handle not in (None, wintypes.HANDLE(-1).value)
    try:
        res = _post(client, value=7)
        assert res.status_code == 409 and "Excel" in res.json()["detail"]
        assert client.get("/api/manual/diesel").status_code == 409
    finally:
        k32.CloseHandle(handle)
    assert _post(client, value=7).status_code == 200


# --------------------------------------------------------------------------- hand-typed rows

def test_hand_typed_rows(client, tmp_path):
    _hand_file(tmp_path, "diesel", [
        ("TASL Nagpur", dt.datetime(2026, 4, 1), "1,250.5", None, "typed by hand", None, None),   # date month, text number, no id
        ("Banglore", "Apr-26", 3, "KL", None, None, None),                                       # misspelt plant, 'Apr-26'
        ("TASL(MCA)-HYD", "2026/05", 4, None, None, None, "my-id"),                               # own id
        ("TBAL", "2026-04", 9, None, None, None, None),                                           # excluded plant: skipped
        ("TCOE", "next month", 9, None, None, None, None),                                        # unreadable month: skipped
        ("TSAL", "2026-04", -2, None, None, None, None),                                          # negative: skipped
        (None, None, None, None, None, None, None),                                               # blank row
    ])
    data = client.get("/api/manual/diesel").json()
    got = {(e["plant"], e["month"]): e for e in data["entries"]}
    assert set(got) == {("TASL-NGP", "2026-04"), ("TASL-BLR", "2026-04"), ("TASL-MCA", "2026-05")}
    assert got[("TASL-NGP", "2026-04")]["value"] == 1250.5
    assert got[("TASL-MCA", "2026-05")]["id"] == "my-id"
    assert len(data["notes"]) == 3 and any("TBAL" in n for n in data["notes"])
    # derived ids are stable across requests and usable for delete
    again = {(e["plant"], e["month"]): e["id"] for e in client.get("/api/manual/diesel").json()["entries"]}
    assert again[("TASL-NGP", "2026-04")] == got[("TASL-NGP", "2026-04")]["id"]
    assert client.delete(f"/api/manual/diesel/{got[('TASL-NGP', '2026-04')]['id']}").status_code == 200
    left = {(e["plant"], e["month"]) for e in client.get("/api/manual/diesel").json()["entries"]}
    assert left == {("TASL-BLR", "2026-04"), ("TASL-MCA", "2026-05")}


def test_add_replaces_hand_typed_row_and_keeps_other_rows(client, tmp_path):
    path = _hand_file(tmp_path, "revenue", [("TCOE", dt.datetime(2026, 4, 15), 10, None, None, None, None),
                                            ("TLMAL", "2026-04", 5, None, None, None, None)],
                      header=("Plant", "Month", "Value", "Unit", "Note", "Entered at", "Entry ID"))
    res = _post(client, "revenue", plant="TCOE", month="2026-04", value=11)
    assert res.status_code == 200 and res.json()["replaced"] is True
    entries = {e["plant"]: e["value"] for e in client.get("/api/manual/revenue").json()["entries"]}
    assert entries == {"TCOE": 11.0, "TLMAL": 5.0}
    assert load_workbook(path).active.max_row == 3


def test_hand_typed_duplicates_collapse_on_replace(client, tmp_path):
    path = _hand_file(tmp_path, "diesel", [("TCOE", "2026-04", 1, None, None, None, None),
                                           ("TCOE", "2026-04", 2, None, None, None, None),
                                           ("TSAL", "2026-04", 3, None, None, None, None)])
    _post(client, plant="TCOE", month="2026-04", value=9)
    ws = load_workbook(path).active
    assert [(ws.cell(r, 1).value, ws.cell(r, 3).value) for r in range(2, ws.max_row + 1)] == [("TCOE", 9), ("TSAL", 3)]


def test_file_without_header_gives_409_on_write_and_note_on_read(client, tmp_path):
    folder = tmp_path / "diesel"
    folder.mkdir()
    wb = Workbook()
    wb.active.append(["just", "some", "text"])
    wb.save(folder / "Diesel - manual entries.xlsx")
    assert client.get("/api/manual/diesel").json()["notes"]
    assert _post(client).status_code == 409


# --------------------------------------------------------------------------- combined (real workbooks)

def _cell(data, plant, month):
    plant_row = next(p for p in data["plants"] if p["plant"] == plant)
    return next((c for c in plant_row["months"] if c["month"] == month), None)


def test_combined_diesel_workbook_values(client):
    data = client.get("/api/manual/diesel/combined").json()
    assert data["unit"] == "KL" and data["workbookGranularity"] == "monthly"
    assert [p["plant"] for p in data["plants"]] == manual.MANUAL_PLANTS   # no TBAL
    # 'Diesel consumption data' (calculation workbook), 'Total Consumption' rows, columns for Apr-25:
    # TLMAL row 8 = 1.32, TASL-Nagpur row 28 = 34.45, TASL- Banglore row 33 = 3.14
    assert _cell(data, "TLMAL", "2025-04")["value"] == 1.32
    assert _cell(data, "TASL-NGP", "2025-04")["value"] == 34.45
    assert _cell(data, "TASL-BLR", "2025-04")["value"] == 3.14   # sheet spells it 'TASL- Banglore'
    cell = _cell(data, "TASL-NGP", "2025-04")
    assert cell["source"] == "workbook" and cell["workbookSource"] == "calculation" and cell["manualValue"] is None
    # FY26-27 header dates are Excel's misreading of 'Apr-26'; the month must still be 2026-04
    assert _cell(data, "TASL-NGP", "2026-04")["value"] == 27.55
    # a 0 total over blank components (NGP May-26) is 'no data', not a measured zero
    assert _cell(data, "TASL-NGP", "2026-05") is None
    assert not any("TBAL" in p["plant"] for p in data["plants"])


def test_combined_fy_and_plant_filters(client):
    data = client.get("/api/manual/diesel/combined?plant=TLMAL&fy=FY25-26").json()
    assert [p["plant"] for p in data["plants"]] == ["TLMAL"]
    months = [c["month"] for c in data["plants"][0]["months"]]
    assert months == [f"2025-{m:02d}" for m in range(4, 13)] + ["2026-01", "2026-02", "2026-03"]
    assert client.get("/api/manual/diesel/combined?plant=TBAL").status_code == 400


def test_combined_manual_takes_precedence(client):
    _post(client, plant="TLMAL", month="2025-04", value=99.5, note="corrected")
    _post(client, plant="TLMAL", month="2027-01", value=7)     # a month the workbook does not have
    data = client.get("/api/manual/diesel/combined?plant=TLMAL").json()
    cell = _cell(data, "TLMAL", "2025-04")
    assert cell["value"] == 99.5 and cell["source"] == "manual"
    assert cell["workbookValue"] == 1.32 and cell["manualValue"] == 99.5 and cell["note"] == "corrected" and cell["entryId"]
    new = _cell(data, "TLMAL", "2027-01")
    assert new["source"] == "manual" and new["workbookValue"] is None and new["workbookSource"] is None
    assert _cell(data, "TLMAL", "2025-05")["source"] == "workbook"
    # deleting the entry hands the cell back to the workbook
    client.delete(f"/api/manual/diesel/{cell['entryId']}")
    again = _cell(client.get("/api/manual/diesel/combined?plant=TLMAL").json(), "TLMAL", "2025-04")
    assert again["source"] == "workbook" and again["value"] == 1.32


def test_combined_revenue(client):
    data = client.get("/api/manual/revenue/combined").json()
    assert data["unit"] == "₹ Cr"
    # 'TADL Revenue Summary': row 'TASL-NGP' Apr-25 = 25.13, row 'TASL-MCA' Apr-25 = 11.94
    assert _cell(data, "TASL-NGP", "2025-04")["value"] == 25.13
    assert _cell(data, "TASL-MCA", "2025-04")["value"] == 11.94
    assert _cell(data, "TASL-NGP", "2026-03")["value"] == 80.0     # text '80' in the sheet
    assert {c["fy"] for p in data["plants"] for c in p["months"]} == {"FY25-26"}
    assert not any("TADL" == p["plant"] for p in data["plants"])    # the sheet's company total row is not a plant


def test_combined_afforestation_uses_entry_workbook_only_where_calc_is_blank(client):
    data = client.get("/api/manual/afforestation/combined").json()
    assert data["unit"] == "No."
    # calculation workbook 'Afforestation sheet' TASL-HYD Apr-25 total = 450 (200 trees + 250 shrubs)
    hyd = _cell(data, "TASL-HYD", "2025-04")
    assert hyd["value"] == 450 and hyd["workbookSource"] == "calculation"
    # TASL-NGP has no calculation-workbook entries; the entry workbook has 325 for Jun-25
    ngp = _cell(data, "TASL-NGP", "2025-06")
    assert ngp["value"] == 325 and ngp["workbookSource"] == "entry"
    # older FY blocks of that sheet are not monthly plantings and are left out
    assert {c["fy"] for p in data["plants"] for c in p["months"]} == {"FY25-26"}
    assert any("standing stock" in n for n in data["notes"])


def test_combined_manpower_is_yearly_only(client):
    data = client.get("/api/manual/manpower/combined").json()
    assert data["workbookGranularity"] == "yearly" and data["unit"] == "headcount"
    ngp = next(p for p in data["plants"] if p["plant"] == "TASL-NGP")
    # 'TASL-NGP Sustainability Summary', row 'Employees': FY24-25 = 1917, FY25-26 = 2116
    yearly = {y["fy"]: y["value"] for y in ngp["yearly"]}
    assert yearly["FY24-25"] == 1917 and yearly["FY25-26"] == 2116
    assert ngp["months"] == []                      # the yearly figure is not spread over months
    assert any("not spread" in n for n in data["notes"])
    mca = next(p for p in data["plants"] if p["plant"] == "TASL-MCA")
    assert {y["fy"]: y["value"] for y in mca["yearly"]}["FY25-26"] == 1033
    _post(client, "manpower", plant="TASL-NGP", month="2026-06", value=2200)
    ngp = next(p for p in client.get("/api/manual/manpower/combined").json()["plants"] if p["plant"] == "TASL-NGP")
    assert [(c["month"], c["value"], c["source"]) for c in ngp["months"]] == [("2026-06", 2200.0, "manual")]


def test_combined_survives_missing_workbooks(client, monkeypatch):
    monkeypatch.setattr(manual.workbooks, "calculation_workbook", lambda: None)
    monkeypatch.setattr(manual.workbooks, "entry_workbook", lambda: None)
    _post(client, "revenue", plant="TCOE", month="2026-04", value=3)
    for parameter in manual.PARAMETERS:
        res = client.get(f"/api/manual/{parameter}/combined")
        assert res.status_code == 200
    data = client.get("/api/manual/revenue/combined").json()
    assert _cell(data, "TCOE", "2026-04")["source"] == "manual"
    assert any("not found" in n for n in data["notes"])


def test_real_source_folder_not_touched():
    import pem.domains.manual as m
    assert m.STORAGE_ROOT is None   # outside the client fixture the module points at the real folder...
    # ...and these tests neither created nor changed a manual file there. (The files themselves
    # may exist: they are created the first time someone saves an entry from the website.)
    for parameter in m.PARAMETERS:
        path = m.storage_path(parameter)
        now = path.stat().st_mtime_ns if path.is_file() else None
        assert now == _REAL_FILES_BEFORE[parameter], f"{path.name} was created or modified by the tests"
