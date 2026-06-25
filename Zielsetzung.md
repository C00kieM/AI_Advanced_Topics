# Zielsetzung: Lokale Wetter-KI

## Produktziel

Das Projekt soll beantworten, wie sich eine DWD-Wetterprognose am konkreten lokalen Standort tatsaechlich auswirkt. Dazu werden DWD-Prognosen mit lokalen Messdaten aus InfluxDB verglichen. Aus den Abweichungen entsteht ein lokales Erfahrungsmodell, das der Chatbot verstaendlich erklaert.

## MVP

Der erste lauffaehige MVP ist eine End-to-end-Demo:

- lokale Messdaten aus InfluxDB lesen
- lokale 3-Jahres-CSV-DB fuer alle `wetterdaten-*` Measurements synchronisieren
- historische DWD-CDC-Daten fuer Temperatur, Niederschlag und Wind lokal synchronisieren
- DWD-MOSMIX-Prognosen abrufen, aus KMZ/KML parsen und archivieren
- InfluxDB ausschliesslich read-only nutzen; keine Schreibzugriffe in InfluxDB erlauben
- Datenstatus und veraltete Sensorwerte sichtbar melden
- DWD-Prognosen zeitlich mit lokalen Ist-Werten vergleichen
- erste Korrekturmodelle fuer Temperatur, Niederschlag und Wind trainieren, sobald genuegend Daten vorhanden sind
- Fragen ueber einen interaktiven CLI-Chat ohne LLM-Abhaengigkeit beantworten
- FastAPI-Endpunkte fuer spaetere UI- oder Automationsintegration bereitstellen
- lokale Desktop-GUI mit Weather-Ops-Terminal, Datenstatus, sicheren Job-Aktionen und Modellstatus bereitstellen

## Erfolgskriterien

- `weather-chat status` erkennt InfluxDB und DWD und meldet klar, wenn lokale Wetterdaten veraltet sind.
- `weather-chat sync-local-db` schreibt eine lokale CSV mit maximal 3 Jahren Wetterstationshistorie.
- `weather-chat sync-dwd-history` schreibt DWD-CDC-Historie in die feste Datei `data/dwd_weather_data.csv`.
- `weather-chat archive-dwd-forecast` archiviert MOSMIX- oder Fallback-DWD-Prognosen lokal als CSV mit Ausgabezeitpunkt und Gueltigkeitszeitpunkt.
- `weather-chat compare` kann Forecast-vs-Ist-Paare zeigen, sobald archivierte Prognosen und lokale Messwerte ueberlappen.
- `weather-chat train` bewertet lokale Korrekturmodelle gegen die rohe DWD-Prognose mit MAE/RMSE.
- Der Chatbot nennt DWD-Prognose, lokalen Datenstatus, Unsicherheit und Modellbelastbarkeit.
- `weather-gui` startet eine lokale Desktop-Oberflaeche mit Terminal, Statuskarten, Vergleichsstatus, Trainingsstatus und Job-Monitor.
- Lange Aktionen wie DWD-Historie-Sync, lokaler Cache-Sync, Forecast-Archivierung und Training laufen in der GUI als Hintergrundjobs.
- Lokale Laufzeitdaten und Cache-Artefakte bleiben aus Git heraus; versioniert werden nur Code, Tests, Doku und kleine Fixtures.

## Aktueller Stand

- Der MVP ist als CLI, FastAPI-Service und Desktop-GUI umgesetzt.
- InfluxDB bleibt read-only; Forecasts und DWD-Historie werden lokal in CSV-Dateien gehalten.
- `weather-chat compare` nutzt archivierte Forecasts und lokale Ist-Werte aus der lokalen CSV und zeigt aktuell verwertbare Forecast-vs-Ist-Paare.
- `weather-chat train` trainiert Modelle fuer Temperatur, Niederschlag und Windgeschwindigkeit, sobald genug Paare vorliegen, und schreibt lokale `.joblib`-Modelle.
- Die GUI ist ein lokaler Launcher mit sicherem App-Terminal. Es werden nur bekannte Slash-Kommandos ausgefuehrt, keine echte Betriebssystem-Shell.

## Ausbaustufen

- MOSMIX-Stationsauswahl ueber Koordinaten automatisch bestimmen.
- Browser/Web-UI oder erweiterte Desktop-Views mit Vergleichsgrafiken und Verlauf bauen.
- DWD-Warnungen als separaten Adapter einbinden.
- Saisonale Modelle und getrennte Modelle je Prognosehorizont trainieren.
- Betrieb ueber systemd oder Docker Compose automatisieren.
