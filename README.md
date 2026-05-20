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
weather-chat chat
uvicorn weather_ai.api:app --reload
```

Der Chat funktioniert ohne LLM-Provider. Er nutzt lokale Messdaten, DWD-Prognosen und den ML-/Datenstatus, um eine nachvollziehbare Antwort zu erzeugen.

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
