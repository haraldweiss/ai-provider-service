# Open WebUI Tools (versionierter Backup-Snapshot)

Open WebUI lädt Tools aus seiner DB (`/opt/open-webui/data/webui.db`),
nicht aus dem Dateisystem. Die Dateien in diesem Verzeichnis sind
**Quell-Snapshots** für Versionierung und Restore.

## Aktuelle Tools

| Datei | Was es macht | API-Key nötig |
|---|---|---|
| [`weather.py`](./weather.py) | Aktuelles Wetter + 1–5-Tage-Vorhersage über OpenWeatherMap | ja (Valve `api_key`) |

## Deployen eines Tools

1. In Open WebUI: **Workspace → Tools → +** (Create new tool)
2. Inhalt der `.py`-Datei einfügen, **Save**
3. Im Tool auf **Valves** (Zahnrad) klicken, API-Key + ggf. Defaults setzen
4. **(Optional, Single-User)** Access Control → Private
5. Im Chat: Tools-Picker (+ neben Modell-Dropdown) → Tool aktivieren

## Updaten

Open WebUI hat keinen "from-file"-Import. Nach Source-Änderung in diesem
Repo: Tool in der UI öffnen, Code ersetzen, Save. Valves bleiben erhalten.

## Backup-Strategie

Tools sind im SQLite-Backup von `/opt/open-webui/data/webui.db` mit drin
(siehe Backup-Section in `../README.md`). Dieser Repo-Snapshot ist
zusätzlich für Code-Review, Diff-Historie und Disaster-Recovery, falls
die SQLite mal nicht wiederherstellbar ist.
