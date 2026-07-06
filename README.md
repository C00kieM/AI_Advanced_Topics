# Wetter-KI MVP

Lokaler Wetter-KI-MVP mit:

- InfluxDB-Anbindung fuer lokale Wetterstationsdaten
- DWD MOSMIX OpenData Adapter fuer KMZ/KML-Stationsprognosen
- JSON-Fallback fuer `https://dwd.api.bund.dev/`
- internes FastAPI Backend fuer CLI, Desktop-GUI und Automatisierung
- interaktivem CLI-Chat
- lokaler Python-Desktop-GUI per `weather-gui`
- regelbasiertem lokalen Chat ohne LLM-Abhaengigkeit
- erster ML-Schicht fuer DWD-vs-lokal-Korrekturen
- Strunde-Pegelcache mit Regen/Pegel-Korrelation und vorsichtiger Pegelprognose

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
weather-chat sync-strunde
weather-chat archive-dwd-forecast
weather-chat chat
weather-gui
```

Der Chat funktioniert ohne LLM-Provider. Er nutzt lokale Messdaten, DWD-Prognosen und den ML-/Datenstatus, um eine nachvollziehbare Antwort zu erzeugen.

## Desktop-GUI

Der lokale Desktop-Launcher ist der einzige grafische Einstieg. Er startet das FastAPI-Backend intern ohne blockierenden Startup-Sync und oeffnet die Weather-Ops-Oberflaeche in einem nativen Python/pywebview-Fenster:

```powershell
weather-gui
```

Es gibt keine separate Browser-Website mehr. `weather_ai.api:create_app()` serviert standardmaessig nur die API. Die GUI-Assets werden erst durch `weather-gui` mit einem internen Start-Token freigeschaltet und sind fuer die Desktop-Sitzung gedacht.

Die GUI enthaelt eine Chat-zentrierte Admin View mit Freitext-Chat und bekannten Slash-Kommandos: `/info`, `/status`, `/sync-local`, `/sync-dwd`, `/sync-strunde`, `/archive`, `/compare`, `/train` und `/clear`. Es wird keine echte Betriebssystem-Shell freigegeben. Lange Aktionen laufen als Hintergrundjobs und koennen im Job-Monitor verfolgt werden. Die separate Chat-Ansicht ist bewusst hell und einfach gehalten: nur Chatverlauf, Eingabe und Senden-Button.

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

## Strunde-Pegel

Strunde-Pegelwerte werden getrennt von den Wetterstationsdaten in einer eigenen lokalen CSV gehalten:

```powershell
STRUNDE_MEASUREMENT=pegel-strunde
STRUNDE_LEVEL_FIELD=water_level_cm
STRUNDE_CACHE_PATH=data/strunde_water_level.csv
STRUNDE_CACHE_RETENTION_DAYS=1095
STRUNDE_RAIN_MEASUREMENTS=
```

Manueller Sync:

```powershell
weather-chat sync-strunde
```

Der Chat beantwortet Fragen wie „Wie hoch ist die Strunde aktuell?“, „Wie war der Pegel am 30.06.2026?“ oder „Was passiert morgen mit der Strunde?“. Die Prognose kombiniert den letzten Pegel, gespeicherte DWD-Niederschlagsprognosen und eine einfache Korrelation zwischen Niederschlag und spaeterem Pegelanstieg. Wenn InfluxDB offline ist, bleibt ein vorhandener Strunde-CSV-Cache nutzbar; ohne Pegelcache sagt der Chat klar, dass keine belastbare Pegelantwort moeglich ist.

## InfluxDB ist Read-only

Diese App schreibt niemals in InfluxDB. InfluxDB wird nur gelesen. DWD-Prognosen werden lokal in `data/dwd_weather_data.csv` archiviert:

```powershell
weather-chat archive-dwd-forecast
```

Der alte Influx-Write-Pfad ist im Code hart blockiert. Auch versehentliche Aufrufe von `InfluxClient.write_forecasts(...)` werfen sofort einen Fehler. Produktiv genutzte lokale Daten-Dateien sind `data/local_weather_history.csv`, `data/dwd_weather_data.csv` und `data/strunde_water_level.csv`.

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
