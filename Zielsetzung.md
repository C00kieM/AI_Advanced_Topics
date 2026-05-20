# Zielsetzung: Lokale Wetter-KI

## Produktziel

Das Projekt soll beantworten, wie sich eine DWD-Wetterprognose am konkreten lokalen Standort tatsaechlich auswirkt. Dazu werden DWD-Prognosen mit lokalen Messdaten aus InfluxDB verglichen. Aus den Abweichungen entsteht ein lokales Erfahrungsmodell, das der Chatbot verstaendlich erklaert.

## MVP

Der erste lauffaehige MVP ist eine End-to-end-Demo:

- lokale Messdaten aus InfluxDB lesen
- DWD-MOSMIX-Prognosen abrufen, aus KMZ/KML parsen und archivieren
- Datenstatus und veraltete Sensorwerte sichtbar melden
- DWD-Prognosen zeitlich mit lokalen Ist-Werten vergleichen
- erste Korrekturmodelle fuer Temperatur, Niederschlag und Wind trainieren, sobald genuegend Daten vorhanden sind
- Fragen ueber einen interaktiven CLI-Chat ohne LLM-Abhaengigkeit beantworten
- FastAPI-Endpunkte fuer spaetere UI- oder Automationsintegration bereitstellen

## Erfolgskriterien

- `weather-chat status` erkennt InfluxDB und DWD und meldet klar, wenn lokale Wetterdaten veraltet sind.
- `weather-chat ingest-dwd` archiviert MOSMIX- oder Fallback-DWD-Prognosen mit Ausgabezeitpunkt und Gueltigkeitszeitpunkt.
- `weather-chat compare` kann Forecast-vs-Ist-Paare zeigen, sobald archivierte Prognosen und lokale Messwerte ueberlappen.
- `weather-chat train` bewertet lokale Korrekturmodelle gegen die rohe DWD-Prognose mit MAE/RMSE.
- Der Chatbot nennt DWD-Prognose, lokalen Datenstatus, Unsicherheit und Modellbelastbarkeit.

## Ausbaustufen

- MOSMIX-Stationsauswahl ueber Koordinaten automatisch bestimmen.
- Web-UI mit Chat, Vergleichsgrafiken und Datenstatus bauen.
- DWD-Warnungen als separaten Adapter einbinden.
- Saisonale Modelle und getrennte Modelle je Prognosehorizont trainieren.
- Betrieb ueber systemd oder Docker Compose automatisieren.
