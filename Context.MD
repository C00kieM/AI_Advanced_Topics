# Projektkontext: Wetter-KI MVP

## Umgebung

- Repository: `AI_Advanced_Topics`
- Sprache/Stack: Python 3.11+, FastAPI, Typer CLI, scikit-learn
- Zielbetrieb: lokaler Server im Netzwerk der Wetterstation und InfluxDB
- Secrets: ausschliesslich lokal ueber `.env`; keine Tokens in Code, Doku oder Git

## InfluxDB

- URL: `http://192.168.31.217:8086`
- Org: `63a16fe229625e74`
- Bucket: `iot`
- Primaeres Measurement: `wetterdaten-gl-fw-2`
- Vergleichs-Measurements:
  - `wetterdaten-gl-fw-2`
  - `wetterdaten-gl-gronau`
  - `wetterdaten-gl-mitte`
  - `wetterdaten-untereschbach-neichen`

Gefundene Felder fuer `wetterdaten-gl-fw-1`:

- `Lufttemperatur`
- `Luftfeuchtigkeit`
- `Luftdruck`
- `Niederschlag`
- `wind_speed`
- `wind_direction`
- `LichtIntensitaet`
- `UVIndex`
- `eui`

## Datenstatus

Bei der Planung wurde festgestellt, dass `wetterdaten-gl-fw-1` zuletzt am 16.04.2026 Werte geliefert hat. Die Diagnose hat danach `wetterdaten-gl-fw-2` als aktuellere lokale Quelle gefunden; lokal ist deshalb `LOCAL_MEASUREMENT=wetterdaten-gl-fw-2` gesetzt. Die Implementierung enthaelt weiterhin eine Diagnosefunktion, die veraltete lokale Daten explizit meldet und alternative Wetter-Measurements pruefbar macht.

## DWD-Datenquelle

Primaere MVP-Quelle ist DWD OpenData MOSMIX ueber `MOSMIX_STATION_ID`, `MOSMIX_PRODUCT` und `MOSMIX_BASE_URL`. Der Client laedt die aktuelle KMZ-Datei einer Einzelstation, extrahiert das KML und erzeugt daraus `ForecastPoint`s.

Der JSON-Endpunkt `https://dwd.api.bund.dev/` beziehungsweise der konfigurierbare Proxy-Endpunkt `DWD_BASE_URL` bleibt als Fallback vorhanden, wenn keine MOSMIX-Station konfiguriert ist.

Aktuell gemappte MOSMIX-Parameter:

- `TTT` -> Temperatur
- `Td` -> Taupunkt
- `DD` -> Windrichtung
- `FF` -> Windgeschwindigkeit
- `FX1`, `FX3` -> Windboeen
- `RR1c`, `RR3c` -> Niederschlag
- `Neff` -> effektive Bewoelkung
- `PPPP` -> Luftdruck
- `Rad1h` -> Globalstrahlung
- `SunD1` -> Sonnenscheindauer

Referenzen:

- https://github.com/bundesAPI/dwd-api
- https://www.dwd.de/EN/ourservices/opendata/opendata.html
- https://www.dwd.de/EN/ourservices/met_application_mosmix/met_application_mosmix.html
- https://www.dwd.de/EN/ourservices/met_application_mosmix/mosmix_kml_format_description.pdf?__blob=publicationFile&v=4
- https://www.dwd.de/DE/leistungen/met_verfahren_mosmix/mosmix_parameteruebersicht.pdf?__blob=publicationFile&v=4

## Chatbot

Der MVP verwendet keinen LLM-Provider. Der Chat ist regelbasiert und kombiniert lokale Messwerte, DWD-Prognosekontext, Datenalter, Forecast-vs-Ist-Vergleiche und Modellstatus zu einer transparenten deutschen Antwort.

## Offene Punkte

- Aktuelle Ursache fuer den Datenstillstand der lokalen Wetterstation klaeren.
- `MOSMIX_STATION_ID`, `LOCAL_LAT` und `LOCAL_LON` lokal final konfigurieren. Lokal ist aktuell `10513` als Koeln/Bonn-Referenzstation gesetzt.
- Nach Start der DWD-Archivierung genuegend Forecast-vs-Ist-Paare sammeln, bevor ML-Ergebnisse als belastbar gelten.
