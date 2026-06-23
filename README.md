# Wetter-KI MVP

Lokaler Wetter-KI-MVP mit:

- InfluxDB-Anbindung fuer lokale Wetterstationsdaten
- DWD MOSMIX OpenData Adapter fuer KMZ/KML-Stationsprognosen
- JSON-Fallback fuer `https://dwd.api.bund.dev/`
- FastAPI Backend
- interaktivem CLI-Chat
- regelbasiertem lokalen Chat ohne LLM-Abhaengigkeit
- erster ML-Schicht fuer DWD-vs-lokal-Korrekturen

## Schnellstart

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
Copy-Item .env.example .env
```

Danach `.env` lokal ausfuellen. Der InfluxDB-Token gehoert nur in `.env` und niemals ins Repo.

```powershell
weather-chat status
weather-chat sync-local-db
weather-chat sync-dwd-history
weather-chat archive-dwd-forecast
weather-chat chat
weather-gui
uvicorn weather_ai.api:app --reload
```

Der Chat funktioniert ohne LLM-Provider. Er nutzt lokale Messdaten, DWD-Prognosen und den ML-/Datenstatus, um eine nachvollziehbare Antwort zu erzeugen.

## Desktop-GUI

Der lokale Desktop-Launcher startet das FastAPI-Backend ohne blockierenden Startup-Sync und oeffnet die Weather-Ops-Oberflaeche in einem nativen Fenster:

```powershell
weather-gui
```

Die GUI enthaelt ein sicheres App-Terminal mit Freitext-Chat und bekannten Slash-Kommandos: `/status`, `/sync-local`, `/sync-dwd`, `/archive`, `/compare`, `/train` und `/clear`. Es wird keine echte Betriebssystem-Shell freigegeben. Lange Aktionen laufen als Hintergrundjobs und koennen im Job-Monitor verfolgt werden.

## Lokale CSV-DB

Beim Programmstart synchronisiert der MVP eine lokale CSV-Datei mit allen `wetterdaten-*` Measurements aus InfluxDB:

```powershell
LOCAL_CACHE_PATH=data/local_weather_history.csv
LOCAL_CACHE_RETENTION_DAYS=1095
LOCAL_CACHE_SYNC_ON_STARTUP=true
```

Die CSV enthaelt `time`, `measurement`, `field` und `value`. Bei jedem Start werden Zeilen aelter als 3 Jahre entfernt und neue InfluxDB-Zeilen seit dem letzten Cache-Zeitpunkt ergaenzt. Manuell kann der Sync so gestartet werden:

```powershell
weather-chat sync-local-db
```

## Historische DWD CDC-Daten und Forecasts

Der MVP kann historische DWD-CDC-Messwerte fuer Temperatur, Niederschlag und Wind lokal als lange CSV ablegen:

```powershell
DWD_CDC_BASE_URL=http://opendata.dwd.de/climate_environment/CDC
DWD_HISTORICAL_STATION_IDS=02667
DWD_HISTORICAL_RESOLUTION=10_minutes
DWD_HISTORICAL_PARAMETERS=air_temperature,precipitation,wind
DWD_HISTORICAL_RETENTION_DAYS=1095
DWD_DATA_PATH=data/dwd_weather_data.csv
```

Manueller Sync:

```powershell
weather-chat sync-dwd-history
```

Die feste DWD-Datei ist `data/dwd_weather_data.csv`. Sie enthaelt historische CDC-Messwerte und lokal archivierte MOSMIX-Prognosen in einer gemeinsamen Struktur.

## InfluxDB ist Read-only

Diese App schreibt niemals in InfluxDB. InfluxDB wird nur gelesen. DWD-Prognosen werden lokal in `data/dwd_weather_data.csv` archiviert:

```powershell
weather-chat archive-dwd-forecast
```

Der alte Influx-Write-Pfad ist im Code hart blockiert. Auch versehentliche Aufrufe von `InfluxClient.write_forecasts(...)` werfen sofort einen Fehler. Produktiv gibt es nur zwei feste Daten-Dateien: `data/local_weather_history.csv` und `data/dwd_weather_data.csv`.

## DWD MOSMIX

Primaere Forecast-Quelle ist `MOSMIX_STATION_ID` ueber DWD OpenData:

```powershell
MOSMIX_STATION_ID=G005
MOSMIX_PRODUCT=MOSMIX_L
MOSMIX_BASE_URL=http://opendata.dwd.de
```

`MOSMIX_L` liefert mehr Parameter und wird 4x taeglich aktualisiert. `MOSMIX_S` ist kleiner und wird haeufiger aktualisiert. Wenn keine MOSMIX-Station gesetzt ist, nutzt der MVP den bisherigen `dwd.api.bund.dev`-Adapter mit `DWD_STATION_ID`.

## Wichtiger Datenstand

Die bisher geprueften lokalen Wetterdaten fuer `wetterdaten-gl-fw-1` enden am 16.04.2026. Der MVP meldet diesen Zustand in `weather-chat status` bewusst als Blocker, bis wieder aktuelle lokale Messwerte vorliegen.
