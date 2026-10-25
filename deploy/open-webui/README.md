# Open WebUI — aktueller Betrieb und historisches Archiv

Open WebUI unter `https://chat.wolfinisoftware.de` nutzt **ai-provider-service
direkt** über dessen OpenAI-kompatible `/v1`-API. LiteLLM wurde im August 2026
auf beiden Produktionshosts entfernt. Der frühere LiteLLM-/Postgres-Stack ist
stillgelegt und darf aus diesen Beispielen nicht erneut installiert werden.

## Aktuelle Deployment- und Upgrade-Anleitung

Die gepflegte Betriebsanleitung liegt im Repository `wolfini_de_web`:

- [Open WebUI: Deployment Notes, Bind-Mounts und Upgrade-Verfahren](https://github.com/haraldweiss/wolfini_de_web/blob/main/OPEN_WEBUI_FIXES_2026-07-23.md#4-deployment-notes)
- [Betriebsregeln, insbesondere Open WebUI-Upgrades (§3.15)](https://github.com/haraldweiss/wolfini_de_web/blob/main/AGENTS.md)
- [Upgrade-Verifikation](https://github.com/haraldweiss/wolfini_de_web/blob/main/scripts/open-webui/verify-upgrade.sh)

Die dort dokumentierte Installation läuft als rootful Podman-Container auf
`ionos-vps` im Netzwerk `search-net`, mit `/opt/open-webui/owui.env`, dem
Datenverzeichnis `/opt/open-webui-data` und den erforderlichen gepatchten
Bind-Mounts. Vor einem Upgrade die dortigen Regenerations- und Prüfschritte
befolgen; die alten Quadlets bilden diese Installation nicht ab.

Provider-Zugänge und Modell-Routing werden im ai-provider-service verwaltet.
Open-WebUI-Verbindungen und Tokens gemäß der gepflegten Betriebsanleitung
aktualisieren; persistierte Einstellungen in `webui.db` können Env-Werte
überschreiben. Keine LiteLLM-Master-Keys oder LiteLLM-Modelllisten mehr anlegen.

## Historisches LiteLLM-Archiv

[`archive/litellm-stack/`](./archive/litellm-stack/) enthält die früheren
LiteLLM-, Postgres- und Open-WebUI-Quadlets sowie deren Config-/Env-Beispiele
als technische Referenz. Die Dateien enden auf `.example`; die Quadlets
enthalten keinen `[Install]`-Abschnitt. Sie sind keine installierbaren Quadlets
und werden von einem `*.container`-Glob nicht erfasst.

Die alten Installations-, Spend-Tracking- und Update-Kommandos wurden entfernt.
Keine Dateien aus dem Archiv nach `/etc/containers/systemd/` kopieren oder für
den laufenden Stack umbenennen. Für die ursprünglichen Installationsschritte
bei Bedarf die Git-Historie konsultieren.

`open-webui.network` bleibt als Abhängigkeit des vorhandenen
[SearXNG-Quadlet-Beispiels](../searxng/README.md) erhalten. Es beschreibt nicht
das aktuelle Produktionsnetz `search-net`. Die übrigen Dateien in diesem
Verzeichnis (Apache-Beispiele, Ollama-Guard, Ops-Helfer und Tools) sind separate
Referenzen und ersetzen die aktuelle Betriebsanleitung nicht.
