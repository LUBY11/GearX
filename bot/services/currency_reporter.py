from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Dict

import httpx
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from config import CurrencyReportConfig

log = logging.getLogger(__name__)


@dataclass(slots=True)
class CurrencyReportResult:
    text: str
    header: str
    quote_currency: str
    values: Dict[str, float]
    timestamp: dt.datetime

    def to_embed_dict(self) -> Dict[str, Any]:
        fields = []
        for code, amount in self.values.items():
            fields.append({
                "name": f"{code} → {self.quote_currency}",
                "value": f"**1 {code} ≈ {amount:,.2f} {self.quote_currency}**",
                "inline": False,
            })
            fields.append({
                "name": "\u200b",
                "value": "\u200b",
                "inline": False,
            })
        return {
            "title": "환율 리포트",
            "description": f"{self.header}\n기준 통화: 1 {self.quote_currency}",
            "color": 0x2ecc71,
            "timestamp": self.timestamp.isoformat(),
            "fields": fields,
            "footer": {
                "text": "자동 환율 업데이트",
            },
        }


class CurrencyReporter:
    """Fetches FX data and renders a short Discord-friendly report."""

    def __init__(self, config: CurrencyReportConfig):
        self.config = config

    async def build_report(self) -> CurrencyReportResult | None:
        if not self.config.currencies:
            return None

        rates = await self._fetch_rates()
        if not rates:
            return None
        return self._render_report(rates)

    async def _fetch_rates(self) -> Dict[str, float]:
        base_code = self.config.quote_currency.upper()
        symbols = ",".join(code.upper() for code in self.config.currencies)
        url = self.config.api_url.format(
            base=base_code,
            quote=base_code,
            symbols=symbols,
            api_key=self.config.api_key or "",
        )

        params: Dict[str, str] = {}
        if "{base}" not in self.config.api_url:
            params["base"] = base_code
        if "{symbols}" not in self.config.api_url and symbols:
            params["symbols"] = symbols
        if self.config.api_key and "{api_key}" not in self.config.api_url:
            params["access_key"] = self.config.api_key

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                response = await client.get(url, params=params or None)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPError:
            log.exception("Failed to fetch FX rates from %s", self.config.api_url)
            return {}

        rates = payload.get("rates")
        if not isinstance(rates, dict):
            log.warning("Currency API payload missing rates: %s", payload)
            return {}
        cleaned: Dict[str, float] = {}
        for code in self.config.currencies:
            value = rates.get(code.upper())
            if isinstance(value, (int, float)) and value > 0:
                cleaned[code.upper()] = float(value)
        return cleaned

    def _render_report(self, rates: Dict[str, float]) -> CurrencyReportResult:
        tz = self._resolve_timezone()
        now = dt.datetime.now(tz)
        header = now.strftime(f"%Y-%m-%d %H:%M {self.config.timezone} 기준 환율")
        lines = [
            f"{header}",
            f"기준 통화: 1 {self.config.quote_currency.upper()}",
            "",
        ]

        display_values: Dict[str, float] = {}
        for code in self.config.currencies:
            rate = rates.get(code.upper())
            if rate is None or rate == 0:
                continue
            quote_value = 1 / rate
            display_values[code.upper()] = quote_value
            lines.append(f"- 1 {code.upper()} ≈ {quote_value:,.2f} {self.config.quote_currency.upper()}")

        return CurrencyReportResult(
            text="\n".join(lines),
            header=header,
            quote_currency=self.config.quote_currency.upper(),
            values=display_values,
            timestamp=now,
        )

    def _resolve_timezone(self) -> dt.tzinfo:
        try:
            return ZoneInfo(self.config.timezone)
        except ZoneInfoNotFoundError:
            fallback_hours = 9 if self.config.timezone in {"Asia/Seoul", "KST"} else 0
            log.warning(
                "Timezone %s not found. Falling back to UTC%+d.",
                self.config.timezone,
                fallback_hours,
            )
            return dt.timezone(dt.timedelta(hours=fallback_hours))
