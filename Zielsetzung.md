# Zielsetzung: Lokale Wetter-KI

## Produktziel

Das Projekt soll beantworten, wie sich eine DWD-Wetterprognose am konkreten lokalen Standort tatsaechlich auswirkt. Dazu werden DWD-Prognosen mit lokalen Messdaten aus InfluxDB verglichen. Aus den Abweichungen entsteht ein lokales Erfahrungsmodell, das der Chatbot verstaendlich erklaert.

## MVP

Der erste lauffaehige MVP ist eine End-to-end-Demo:

- lokale Messdaten aus InfluxDB lesen
- lokale 3-Jahres-CSV-DB fuer alle `wetterdaten-*` Measurements synchronisieren
- historische DWD-CDC-Daten fuer Temperatur, Niederschlag und Wind lokal synchronisieren
- Strunde-Pegelwerte lokal als 3-Jahres-CSV synchronisieren
- DWD-MOSMIX-Prognosen abrufen, aus KMZ/KML parsen und archivieren
- InfluxDB ausschliesslich read-only nutzen; keine Schreibzugriffe in InfluxDB erlauben
- Datenstatus und veraltete Sensorwerte sichtbar melden
- DWD-Prognosen zeitlich mit lokalen Ist-Werten vergleichen
- erste Korrekturmodelle fuer Temperatur, Niederschlag und Wind trainieren, sobald genuegend Daten vorhanden sind
- Regen/Pegel-Zusammenhang fuer die Strunde aus lokalem Niederschlag und Pegelanstieg auswerten
- Fragen ueber einen interaktiven CLI-Chat ohne LLM-Abhaengigkeit beantworten
- FastAPI-Endpunkte fuer interne Desktop-GUI und Automationsintegration bereitstellen
- lokale Python-Desktop-GUI mit Weather-Ops-Terminal, Chat-Ansicht, Datenstatus, sicheren Job-Aktionen und Modellstatus bereitstellen

## Erfolgskriterien

- `weather-chat status` erkennt InfluxDB und DWD und meldet klar, wenn lokale Wetterdaten veraltet sind.
- `weather-chat sync-local-db` schreibt eine lokale CSV mit maximal 3 Jahren Wetterstationshistorie.
- `weather-chat sync-dwd-history` schreibt DWD-CDC-Historie in die feste Datei `data/dwd_weather_data.csv`.
- `weather-chat sync-strunde` schreibt Strunde-Pegelwerte in die feste Datei `data/strunde_water_level.csv` und bleibt bei InfluxDB-Ausfall mit vorhandenem Cache nutzbar.
- `weather-chat archive-dwd-forecast` archiviert MOSMIX- oder Fallback-DWD-Prognosen lokal als CSV mit Ausgabezeitpunkt und Gueltigkeitszeitpunkt.
- `weather-chat compare` kann Forecast-vs-Ist-Paare zeigen, sobald archivierte Prognosen und lokale Messwerte ueberlappen.
- `weather-chat train` bewertet lokale Korrekturmodelle gegen die rohe DWD-Prognose mit MAE/RMSE.
- Der Chatbot nennt DWD-Prognose, lokalen Datenstatus, Unsicherheit und Modellbelastbarkeit.
- Der Chatbot beantwortet aktuelle, historische und vorsichtige zukuenftige Fragen zum Strunde-Pegel und sagt klar, wenn Pegel- oder Niederschlagsdaten fehlen.
- `weather-gui` startet die lokale Desktop-Oberflaeche mit Terminal, Chat-Ansicht, Statuskarten, Vergleichsstatus, Trainingsstatus und Job-Monitor.
- Die grafische Bedienung erfolgt nur ueber die Python-Desktop-GUI; es gibt keine separate Browser-Website als Nutzeroberflaeche.
- Lange Aktionen wie DWD-Historie-Sync, lokaler Cache-Sync, Forecast-Archivierung und Training laufen in der GUI als Hintergrundjobs.
- Lokale Laufzeitdaten und Cache-Artefakte bleiben aus Git heraus; versioniert werden nur Code, Tests, Doku und kleine Fixtures.
- Strunde-Prognosen extrapolieren keine Pegelhoehe, wenn fuer den Zeitraum keine DWD-Niederschlagsprognose gespeichert ist.

## Aktueller Stand

- Der MVP ist als CLI, internes FastAPI-Backend und Python-Desktop-GUI umgesetzt.
- InfluxDB bleibt read-only; Forecasts und DWD-Historie werden lokal in CSV-Dateien gehalten.
- `weather-chat compare` nutzt archivierte Forecasts und lokale Ist-Werte aus der lokalen CSV und zeigt aktuell verwertbare Forecast-vs-Ist-Paare.
- `weather-chat train` trainiert Modelle fuer Temperatur, Niederschlag und Windgeschwindigkeit, sobald genug Paare vorliegen, und schreibt lokale `.joblib`-Modelle.
- Strunde-Fragen laufen ueber den regelbasierten Chat. Der lokale Strunde-Cache, `/sync-strunde`, GUI-Statuskarte und Job-Endpunkt sind umgesetzt.
- Die GUI ist ein lokaler Launcher mit sicherem Chat-/App-Terminal. Slash-Kommandos und natuerliche Aktionswuensche laufen ueber `/chat`; es werden nur bekannte App-Aktionen ausgefuehrt, keine echte Betriebssystem-Shell.
- Das FastAPI-Backend serviert die GUI nur, wenn `weather-gui` sie explizit mit internem Start-Token aktiviert. Standardmaessig gibt es keinen Browser-Webseiten-Einstieg.

## Ausbaustufen

- MOSMIX-Stationsauswahl ueber Koordinaten automatisch bestimmen.
- Erweiterte Desktop-Views mit Vergleichsgrafiken und Verlauf bauen.
- DWD-Warnungen als separaten Adapter einbinden.
- Saisonale Modelle und getrennte Modelle je Prognosehorizont trainieren.
- Betrieb ueber systemd oder Docker Compose automatisieren.
