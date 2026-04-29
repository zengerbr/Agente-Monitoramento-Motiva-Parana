from __future__ import annotations

import argparse
import json
import os
import platform
import re
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_key: str
    equipment_table: str
    equipment_id_column: str
    equipment_ip_column: str
    equipment_name_column: str
    equipment_active_column: str | None
    results_table: str
    agent_id: str
    interval_minutes: int
    ping_timeout_ms: int
    ping_attempts: int
    timezone_name: str
    http_host: str
    http_port: int

    @classmethod
    def from_env(cls) -> "Config":
        load_dotenv()

        supabase_url = os.getenv("SUPABASE_URL", "").rstrip("/")
        supabase_key = os.getenv("SUPABASE_KEY", "")
        if not supabase_url or not supabase_key:
            raise SystemExit("Configure SUPABASE_URL e SUPABASE_KEY no arquivo .env.")

        interval_minutes = env_int("PING_INTERVAL_MINUTES", 30)
        if interval_minutes <= 0 or 60 % interval_minutes != 0:
            raise SystemExit("PING_INTERVAL_MINUTES precisa dividir 60. Exemplos validos: 5, 10, 15, 30.")

        agent_id = os.getenv("AGENT_ID") or socket.gethostname()

        return cls(
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            equipment_table=os.getenv("EQUIPMENT_TABLE", "equipamentos"),
            equipment_id_column=os.getenv("EQUIPMENT_ID_COLUMN", "id"),
            equipment_ip_column=os.getenv("EQUIPMENT_IP_COLUMN", "ip"),
            equipment_name_column=os.getenv("EQUIPMENT_NAME_COLUMN", "nome"),
            equipment_active_column=os.getenv("EQUIPMENT_ACTIVE_COLUMN", "ativo") or None,
            results_table=os.getenv("RESULTS_TABLE", "monitoramento_ping_resultados"),
            agent_id=agent_id,
            interval_minutes=interval_minutes,
            ping_timeout_ms=env_int("PING_TIMEOUT_MS", 1500),
            ping_attempts=env_int("PING_ATTEMPTS", 2),
            timezone_name=os.getenv("TIMEZONE", "America/Sao_Paulo"),
            http_host=os.getenv("HTTP_HOST", "127.0.0.1"),
            http_port=env_int("HTTP_PORT", 8765),
        )


class SupabaseRestClient:
    def __init__(self, config: Config) -> None:
        self.config = config

    def _request(self, method: str, path: str, body: Any | None = None) -> Any:
        url = f"{self.config.supabase_url}/rest/v1/{path}"
        data = None
        headers = {
            "apikey": self.config.supabase_key,
            "Authorization": f"Bearer {self.config.supabase_key}",
            "Content-Type": "application/json",
        }
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Prefer"] = "resolution=merge-duplicates,return=minimal"

        request = urllib.request.Request(url, data=data, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Erro Supabase {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Nao foi possivel conectar ao Supabase: {exc.reason}") from exc

        if not payload:
            return None
        return json.loads(payload.decode("utf-8"))

    def fetch_equipment(self) -> list[dict[str, Any]]:
        columns = [
            self.config.equipment_id_column,
            self.config.equipment_ip_column,
            self.config.equipment_name_column,
        ]
        if self.config.equipment_active_column:
            columns.append(self.config.equipment_active_column)

        query = {
            "select": ",".join(dict.fromkeys(columns)),
            "order": f"{self.config.equipment_id_column}.asc",
        }
        if self.config.equipment_active_column:
            query[self.config.equipment_active_column] = "eq.true"

        path = f"{self.config.equipment_table}?{urllib.parse.urlencode(query)}"
        rows = self._request("GET", path)
        return rows or []

    def upsert_results(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        params = urllib.parse.urlencode(
            {
                "on_conflict": "equipamento_id,agente_id,janela_inicio,motivo",
            }
        )
        self._request("POST", f"{self.config.results_table}?{params}", rows)


@dataclass(frozen=True)
class PingResult:
    success: bool
    latency_ms: int | None
    error: str | None


class Pinger:
    LATENCY_RE = re.compile(r"(?:time[=<]|tempo[=<])\s*(\d+)\s*ms", re.IGNORECASE)

    def __init__(self, timeout_ms: int, attempts: int) -> None:
        self.timeout_ms = timeout_ms
        self.attempts = attempts
        self.is_windows = platform.system().lower() == "windows"

    def ping(self, ip: str) -> PingResult:
        command = self._command(ip)
        timeout_seconds = max(1, (self.timeout_ms * self.attempts / 1000) + 2)

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return PingResult(False, None, "timeout")
        except OSError as exc:
            return PingResult(False, None, str(exc))

        output = f"{completed.stdout}\n{completed.stderr}"
        latency = self._latency(output)

        if completed.returncode == 0:
            return PingResult(True, latency, None)

        message = "sem resposta"
        cleaned = " ".join(output.split())
        if cleaned:
            message = cleaned[:300]
        return PingResult(False, latency, message)

    def _command(self, ip: str) -> list[str]:
        if self.is_windows:
            return ["ping", "-n", str(self.attempts), "-w", str(self.timeout_ms), ip]
        timeout_seconds = max(1, round(self.timeout_ms / 1000))
        return ["ping", "-c", str(self.attempts), "-W", str(timeout_seconds), ip]

    def _latency(self, output: str) -> int | None:
        matches = [int(match.group(1)) for match in self.LATENCY_RE.finditer(output)]
        if matches:
            return min(matches)
        return None


class MonitoringAgent:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.client = SupabaseRestClient(config)
        self.pinger = Pinger(config.ping_timeout_ms, config.ping_attempts)
        self.timezone = ZoneInfo(config.timezone_name)
        self.lock = threading.Lock()
        self.last_run: dict[str, Any] | None = None

    def run_forever(self) -> None:
        self._start_http_server()
        print(f"Agente {self.config.agent_id} iniciado.")
        print(f"Proximo ping automatico: {self.next_slot(datetime.now(self.timezone)).isoformat()}")

        while True:
            now = datetime.now(self.timezone)
            slot = self.next_slot(now)
            sleep_seconds = max(0.0, (slot - now).total_seconds())
            time.sleep(sleep_seconds)
            self.run_cycle(slot, reason="scheduled")

    def run_cycle(self, slot: datetime | None = None, reason: str = "manual") -> dict[str, Any]:
        if not self.lock.acquire(blocking=False):
            return {"ok": False, "message": "Ja existe um ciclo de ping em andamento."}

        try:
            started = datetime.now(self.timezone)
            window = slot or self.current_slot(started)
            equipment = self.client.fetch_equipment()
            rows = []

            for item in equipment:
                equipment_id = str(item.get(self.config.equipment_id_column, "")).strip()
                ip = str(item.get(self.config.equipment_ip_column, "")).strip()
                name = item.get(self.config.equipment_name_column)

                if not equipment_id or not ip:
                    continue

                result = self.pinger.ping(ip)
                rows.append(
                    {
                        "equipamento_id": equipment_id,
                        "equipamento_nome": str(name) if name is not None else None,
                        "ip": ip,
                        "agente_id": self.config.agent_id,
                        "janela_inicio": window.astimezone(timezone.utc).isoformat(),
                        "sucesso": result.success,
                        "latencia_ms": result.latency_ms,
                        "erro": result.error,
                        "motivo": reason,
                    }
                )

            self.client.upsert_results(rows)
            finished = datetime.now(self.timezone)
            summary = {
                "ok": True,
                "reason": reason,
                "window": window.isoformat(),
                "started_at": started.isoformat(),
                "finished_at": finished.isoformat(),
                "equipment_count": len(equipment),
                "results_count": len(rows),
                "online_count": sum(1 for row in rows if row["sucesso"]),
                "offline_count": sum(1 for row in rows if not row["sucesso"]),
            }
            self.last_run = summary
            print(json.dumps(summary, ensure_ascii=False))
            return summary
        finally:
            self.lock.release()

    def current_slot(self, now: datetime) -> datetime:
        minute = (now.minute // self.config.interval_minutes) * self.config.interval_minutes
        return now.replace(minute=minute, second=0, microsecond=0)

    def next_slot(self, now: datetime) -> datetime:
        current = self.current_slot(now)
        if current <= now.replace(second=0, microsecond=0):
            current += timedelta(minutes=self.config.interval_minutes)
        return current

    def _start_http_server(self) -> None:
        agent = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/health":
                    self._send(404, {"ok": False, "message": "Rota nao encontrada."})
                    return
                self._send(
                    200,
                    {
                        "ok": True,
                        "agent_id": agent.config.agent_id,
                        "last_run": agent.last_run,
                    },
                )

            def do_POST(self) -> None:
                if self.path != "/force-ping":
                    self._send(404, {"ok": False, "message": "Rota nao encontrada."})
                    return
                result = agent.run_cycle(reason="manual")
                self._send(200 if result.get("ok") else 409, result)

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer((self.config.http_host, self.config.http_port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"HTTP local em http://{self.config.http_host}:{self.config.http_port}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Agente de ping para equipamentos Motiva Parana.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("run", help="Roda o agente continuamente.")

    once = subparsers.add_parser("once", help="Executa um ciclo de ping imediato.")
    once.add_argument("--reason", default="manual", help="Motivo gravado no Supabase.")

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    config = Config.from_env()
    agent = MonitoringAgent(config)

    if args.command == "run":
        agent.run_forever()
        return 0

    if args.command == "once":
        result = agent.run_cycle(reason=args.reason)
        return 0 if result.get("ok") else 1

    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
