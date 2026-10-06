"""Example Elen plugin: weather from Open-Meteo (free, no API key).

Install:  cp -r examples/plugins/weather ~/.config/elen/plugins/
Config:   [plugins.weather]
          city = "Tel Aviv"
Restart:  systemctl --user restart elen
"""

import httpx

from elen.plugins import Plugin, ToolResult, tool


class WeatherPlugin(Plugin):
    name = "weather"
    description = "Weather forecast."

    async def setup(self):
        # Runs once at start. Raise an exception here to disable the plugin with a message.
        self.default_city = self.config.get("city", "")

    def prompt_hint(self):
        # One line added to Elen's system prompt.
        return f"default city: {self.default_city}" if self.default_city else ""

    async def _geocode(self, client, city):
        r = await client.get(
            "https://geocoding-api.open-meteo.com/v1/search", params={"name": city, "count": 1}
        )
        r.raise_for_status()
        results = r.json().get("results") or []
        if not results:
            raise ValueError(f"Unknown city '{city}'")
        return results[0]

    @tool(
        "Get the current weather and today's forecast for a city, and show it on screen.",
        params={"city": "string: city name (empty = the user's default city)"},
        risk="read",
    )
    async def get_weather(self, city: str = ""):
        city = city or self.default_city
        if not city:
            return {"error": "No city given and no default city set."}
        async with httpx.AsyncClient(timeout=20) as client:
            place = await self._geocode(client, city)
            r = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"],
                    "longitude": place["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 1,
                    "timezone": "auto",
                },
            )
            r.raise_for_status()
            data = r.json()
        cur, day = data["current"], data["daily"]
        result = {
            "place": f"{place['name']}, {place.get('country', '')}",
            "temperature_c": cur["temperature_2m"],
            "humidity_percent": cur["relative_humidity_2m"],
            "wind_kmh": cur["wind_speed_10m"],
            "max_c": day["temperature_2m_max"][0],
            "min_c": day["temperature_2m_min"][0],
            "rain_chance_percent": day["precipitation_probability_max"][0],
        }
        visual = {
            "type": "stats",
            "title": f"Weather · {place['name']}",
            "items": [
                {"label": "Now", "value": round(result["temperature_c"]), "unit": "°C"},
                {"label": "High / Low", "value": f"{round(result['max_c'])}/{round(result['min_c'])}", "unit": "°C"},
                {"label": "Humidity", "value": result["humidity_percent"], "unit": "%", "percent": result["humidity_percent"]},
                {"label": "Rain chance", "value": result["rain_chance_percent"], "unit": "%", "percent": result["rain_chance_percent"]},
            ],
        }
        return ToolResult(data=result, visual=visual, summary=f"Weather for {result['place']}")
