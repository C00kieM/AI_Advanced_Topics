# Projektkontext: Wetter-KI MVP

## Umgebung

- Repository: `AI_Advanced_Topics`
- Sprache/Stack: Python 3.11+, FastAPI, Typer CLI, pywebview, scikit-learn
- Zielbetrieb: lokaler Server im Netzwerk der Wetterstation und InfluxDB
- Secrets: ausschliesslich lokal ueber `.env`; keine Tokens in Code, Doku oder Git
- InfluxDB ist fuer diese App strikt read-only. Es duerfen niemals Daten in InfluxDB geschrieben werden.

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

Aktueller lokaler Datenstand vom 25.06.2026:

- `data/local_weather_history.csv`: ca. 349.947 Zeilen, Zeitraum 24.03.2024 bis 23.06.2026.
- `data/dwd_weather_data.csv`: ca. 1.574.901 Zeilen, davon 2.798 Forecast-Zeilen und ca. 1.572.103 historische DWD-Beobachtungen.
- Forecast-Archiv in der DWD-Datei reicht aktuell bis 30.05.2026.
- `weather-chat compare` findet aktuell 738 Forecast-vs-Ist-Paare.
- `weather-chat train` trainiert aktuell Modelle fuer Niederschlag, Temperatur und Windgeschwindigkeit.

## Lokale CSV-DB

Der MVP pflegt lokal `data/local_weather_history.csv` als kleine Rolling-Window-Datenbank fuer alle InfluxDB-Measurements mit Prefix `wetterdaten-`. Beim Start wird die Datei synchronisiert:

- Zeilen aelter als `LOCAL_CACHE_RETENTION_DAYS=1095` werden entfernt.
- Neue InfluxDB-Zeilen seit dem letzten Cache-Zeitpunkt werden ergaenzt.
- Duplikate werden ueber `time`, `measurement` und `field` zusammengefuehrt.
- Die Datei ist lokal und wird nicht versioniert.
- Vergleich und Training lesen bevorzugt diese lokale CSV, damit sie auch bei InfluxDB-Ausfall oder Netzwerk-Timeouts funktionieren.
- Fuer Vergleich/Training werden nur die Modellfelder Temperatur, Niederschlag und Windgeschwindigkeit aus der lokalen CSV geladen.

## Historische DWD CDC-Daten

Der `DwdHistoricalClient` laedt historische DWD-CDC-ZIP-Dateien aus `observations_germany/climate`, entpackt die Produkt-Textdateien und schreibt sie in die feste DWD-Datei `data/dwd_weather_data.csv`.

Lokaler Default:

- Station: `02667` Koeln/Bonn
- Aufloesung: `10_minutes`
- Parameter: `air_temperature`, `precipitation`, `wind`
- Retention: 1095 Tage

Die DWD-Datei enthaelt historische Messwerte und MOSMIX-Prognosen. Die Spalten sind `kind`, `time`, `station_id`, `dataset`, `field`, `value`, `quality`, `source_url`, `source`, `issued_at`, `valid_at`, `horizon_hours`, `unit`, `raw_name`.

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

## Lokales Forecast-Archiv

DWD/MOSMIX-Prognosen werden nicht in InfluxDB gespeichert. Sie werden lokal in `data/dwd_weather_data.csv` mit `kind=forecast` archiviert. Dadurch bleibt InfluxDB ausschliesslich Quelle fuer lokale Wetterstationsdaten.

Produktiv existieren genau zwei feste lokale Daten-Dateien:

- `data/local_weather_history.csv`
- `data/dwd_weather_data.csv`

Alte Zwischenartefakte wie `data/dwd_forecast_archive.csv`, `data/dwd_historical_weather.csv`, `*.tmp`, `.tmp-test-forecast-archive*.csv`, pytest-Cache-Verzeichnisse und Browser-Screenshot-Profile sind keine Projektartefakte und bleiben ignoriert beziehungsweise werden entfernt.

## Vergleich und Training

`WeatherService.latest_comparison_summary()` und `WeatherService.train()` lesen Forecasts aus `data/dwd_weather_data.csv` und lokale Ist-Werte aus `data/local_weather_history.csv`. Die Forecast-Zeilen stehen am Anfang der DWD-Datei; der Forecast-Reader bricht nach dem Forecast-Block ab und scannt dadurch nicht mehr die komplette grosse DWD-Historie.

Der Vergleich indexiert lokale Beobachtungen nach Variable und Zeit. Dadurch wird pro Forecast nur der naechste lokale Kandidat innerhalb der Toleranz gesucht, statt alle lokalen Zeilen zu scannen.

Training:

- Minimum: 24 Forecast-vs-Ist-Paare pro Variable.
- Features: rohe DWD-Prognose und Prognosehorizont.
- Modelle: lineare Regression und Random Forest; das beste Modell nach MAE wird gespeichert.
- Metriken: Punkte, Baseline-MAE/RMSE, Modell-MAE/RMSE und MAE-Verbesserung.
- Lokale Modellartefakte liegen in `.weather-ai-models/` und werden nicht versioniert.

## Desktop-GUI

`weather-gui` startet lokal einen FastAPI-Server und oeffnet eine pywebview-Desktop-Oberflaeche. Die GUI ist deutschsprachig und als Weather-Ops-Terminal aufgebaut:

- linke Seite: sicheres Terminal fuer Chat, Slash-Kommandos und Job-Logs
- rechte Seite: feste Statuskarten fuer Systemlage, InfluxDB, DWD, lokale CSV, Vergleich, Modelle, Warnungen und Jobs
- erlaubte Kommandos: `/status`, `/sync-local`, `/sync-dwd`, `/archive`, `/compare`, `/train`, `/clear`
- keine echte Betriebssystem-Shell in der GUI
- lange Aktionen laufen ueber den in-memory `JobManager` als Hintergrundjobs

Relevante GUI/API-Endpunkte:

- `GET /status`
- `POST /terminal/command`
- `POST /jobs/sync-local-db`
- `POST /jobs/sync-dwd-history`
- `POST /jobs/archive-dwd-forecast`
- `POST /jobs/train`
- `GET /jobs`
- `GET /jobs/{id}`

## Chatbot

Der MVP verwendet keinen LLM-Provider. Der Chat ist regelbasiert und kombiniert lokale Messwerte, DWD-Prognosekontext, Datenalter, Forecast-vs-Ist-Vergleiche und Modellstatus zu einer transparenten deutschen Antwort.

## Offene Punkte

- Aktuelle Ursache fuer den Datenstillstand der lokalen Wetterstation klaeren.
- `MOSMIX_STATION_ID`, `LOCAL_LAT` und `LOCAL_LON` lokal final konfigurieren. Lokal ist aktuell `10513` als Koeln/Bonn-Referenzstation gesetzt.
- Weiter Forecast-vs-Ist-Paare sammeln, damit ML-Ergebnisse ueber mehrere Wetterlagen, Jahreszeiten und Prognosehorizonte belastbarer werden.
