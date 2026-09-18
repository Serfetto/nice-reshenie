"""Разбор форматов источников на образцах реальных сообщений."""
from datetime import datetime

from services.ingest.adapters.orbital import tle_apsides, tle_epoch
from services.ingest.adapters.swpc_alerts import parse_message
from services.ingest.adapters.swpc_text import parse_3day, parse_rsga

EXTENDED_WARNING = """Space Weather Message Code: WARPX1
Serial Number: 562
Issue Time: 2024 May 16 1422 UTC

EXTENDED WARNING: Proton 10MeV Integral Flux above 10pfu expected
Extension to Serial Number: 561
Valid From: 2024 May 13 1328 UTC
Now Valid Until: 2024 May 16 2359 UTC
Warning Condition: Persistence
Predicted NOAA Scale: S1 - Minor
"""

SUMMARY = """Space Weather Message Code: SUMPX1
Serial Number: 112
Issue Time: 2024 May 17 1137 UTC

SUMMARY: Proton Event 10MeV Integral Flux exceeded 10pfu
Begin Time: 2024 May 13 1400 UTC
Maximum Time: 2024 May 14 0520 UTC
End Time: 2024 May 16 1455 UTC
Maximum 10MeV Flux: 121 pfu
NOAA Scale: S2 - Moderate
"""

THREE_DAY = """:Product: 05110030three_day_forecast.txt
:Issued: 2024 May 11 0030 UTC
A. NOAA Geomagnetic Activity Observation and Forecast

NOAA Kp index breakdown May 11-May 13 2024

             May 11       May 12       May 13
00-03UT       8.00 (G4)    4.67 (G1)    3.33
03-06UT       7.67 (G4)    5.67 (G2)    3.33
06-09UT       7.00 (G3)    4.67 (G1)    3.67
09-12UT       6.67 (G3)    4.00         3.33
12-15UT       6.00 (G2)    3.67         3.33
15-18UT       5.67 (G2)    2.67         3.33
18-21UT       4.67 (G1)    2.67         3.67
21-00UT       4.33         3.67         3.67

B. NOAA Solar Radiation Activity Observation and Forecast

Solar Radiation Storm Forecast for May 11-May 13 2024

              May 11  May 12  May 13
S1 or greater   99%     75%     60%
"""

RSGA = """:Product: 0607RSGA.txt
:Issued: 2024 Jun 07 2200 UTC
III.  Event probabilities 08 Jun-10 Jun
Class M    50/50/50
Class X    10/10/10
Proton     10/10/10
PCAF       green
"""

ISS_TLE = ("1 25544U 98067A   24160.00880372  .00016717  00000+0  30306-3 0  9994",
           "2 25544  51.6394 350.0290 0005480 250.8542 239.9994 15.49938457456917")


def test_extended_warning():
    m = parse_message(EXTENDED_WARNING)
    assert m["code"] == "WARPX1" and m["serial"] == 562
    assert m["issued_at"] == datetime(2024, 5, 16, 14, 22)
    assert m["msg_type"] == "EXTENDED WARNING"
    assert m["extension_of"] == 561
    assert m["valid_from"] == datetime(2024, 5, 13, 13, 28)
    assert m["valid_to"] == datetime(2024, 5, 16, 23, 59)


def test_summary():
    m = parse_message(SUMMARY)
    assert m["begin_time"] == datetime(2024, 5, 13, 14, 0)
    assert m["max_time"] == datetime(2024, 5, 14, 5, 20)
    assert m["end_time"] == datetime(2024, 5, 16, 14, 55)
    assert m["max_flux"] == 121.0
    assert m["scale"].startswith("S2")


def test_three_day():
    p = parse_3day(THREE_DAY)
    assert p["issued"] == datetime(2024, 5, 11, 0, 30)
    assert len(p["kp"]) == 24
    first = p["kp"][0]
    assert first[0] == datetime(2024, 5, 11, 0) and first[2] == 8.0
    assert (datetime(2024, 5, 12, 3), datetime(2024, 5, 12, 6), 5.67) in p["kp"]
    assert [x[2] for x in p["s1"]] == [99.0, 75.0, 60.0]


def test_rsga():
    p = parse_rsga(RSGA)
    assert p["issued"] == datetime(2024, 6, 7, 22, 0)
    assert p["probs"]["prob_proton"][0] == (datetime(2024, 6, 8), datetime(2024, 6, 9), 10.0)
    assert p["probs"]["prob_m"][2][2] == 50.0


def test_tle_epoch_and_apsides():
    ep = tle_epoch(*ISS_TLE)
    assert ep.date() == datetime(2024, 6, 8).date()
    per, apo = tle_apsides(ISS_TLE[1])
    assert 400 < per < 430 and 400 < apo < 440
