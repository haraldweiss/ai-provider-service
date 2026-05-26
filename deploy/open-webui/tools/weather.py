"""
title: Wetter (OpenWeatherMap)
author: harald
required_open_webui_version: 0.4.0
description: Aktuelles Wetter + Vorhersage über OpenWeatherMap.

Open WebUI-Tools werden in der DB gespeichert, nicht aus dem Dateisystem
geladen. Diese Datei ist ein versionierter Backup/Quellen-Snapshot. Zum
Deployen den Inhalt in der WebUI unter Workspace → Tools → New einfügen,
dann unter Valves den OpenWeatherMap-API-Key eintragen.

Free-Tier-Limit von OpenWeatherMap: 60 Calls/min, 1M/Monat — für persönlichen
Gebrauch mehr als genug.
"""

import requests
from pydantic import BaseModel, Field
from collections import Counter, defaultdict


class Tools:
    class Valves(BaseModel):
        api_key: str = Field(
            default="",
            description="OpenWeatherMap API key (https://home.openweathermap.org/api_keys)",
        )
        units: str = Field(
            default="metric",
            description="metric | imperial | standard",
        )
        lang: str = Field(
            default="de",
            description="Response language code (ISO 639-1)",
        )

    def __init__(self):
        self.valves = self.Valves()

    def _ow(self, path: str, params: dict) -> dict:
        if not self.valves.api_key:
            raise RuntimeError(
                "OpenWeatherMap API key nicht gesetzt "
                "(Tool → Valves → api_key)."
            )
        params = {
            **params,
            "appid": self.valves.api_key,
            "units": self.valves.units,
            "lang": self.valves.lang,
        }
        r = requests.get(
            f"https://api.openweathermap.org/data/2.5/{path}",
            params=params,
            timeout=10,
        )
        r.raise_for_status()
        return r.json()

    def get_current_weather(self, location: str) -> str:
        """
        Hole das aktuelle Wetter für einen Ort.

        :param location: Stadtname, optional mit Ländercode (z.B. "Dortmund,DE")
        :return: Wetterbericht als Text
        """
        try:
            d = self._ow("weather", {"q": location})
        except Exception as e:
            return f"Fehler: {e}"
        m, w = d["main"], d["weather"][0]
        return (
            f"Wetter in {d['name']}, {d['sys']['country']} "
            f"(Stand {d['dt']} UTC):\n"
            f"  • {w['description'].capitalize()}\n"
            f"  • Temperatur: {m['temp']:.1f}°C, gefühlt {m['feels_like']:.1f}°C\n"
            f"  • Min/Max heute: {m['temp_min']:.1f}°C / {m['temp_max']:.1f}°C\n"
            f"  • Luftfeuchtigkeit: {m['humidity']}%\n"
            f"  • Wind: {d['wind']['speed']} m/s\n"
            f"  • Luftdruck: {m['pressure']} hPa"
        )

    def get_forecast(self, location: str, days: int = 3) -> str:
        """
        Hole die Wettervorhersage für die nächsten Tage (max. 5).

        :param location: Stadtname, optional mit Ländercode
        :param days: Anzahl Tage (1-5)
        :return: Vorhersage als Text, ein Tag pro Zeile
        """
        try:
            d = self._ow(
                "forecast",
                {"q": location, "cnt": min(max(days, 1), 5) * 8},
            )
        except Exception as e:
            return f"Fehler: {e}"
        by_day = defaultdict(list)
        for e in d["list"]:
            by_day[e["dt_txt"][:10]].append(e)
        lines = [f"Vorhersage {d['city']['name']}, {d['city']['country']}:"]
        for day in sorted(by_day)[:days]:
            temps = [e["main"]["temp"] for e in by_day[day]]
            top = Counter(
                e["weather"][0]["description"] for e in by_day[day]
            ).most_common(1)[0][0]
            lines.append(f"  {day}: {min(temps):.0f}–{max(temps):.0f}°C, {top}")
        return "\n".join(lines)
