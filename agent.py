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
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


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


def update_dotenv_value(key: str, value: str, path: Path = Path(".env")) -> None:
    lines = []
    found = False

    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()

    updated = []
    for line in lines:
        if line.strip().startswith(f"{key}="):
            updated.append(f"{key}={value}")
            found = True
        else:
            updated.append(line)

    if not found:
        if updated and updated[-1].strip():
            updated.append("")
        updated.append(f"{key}={value}")

    path.write_text("\n".join(updated) + "\n", encoding="utf-8")


def normalize_agent_id(value: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_.-]+", "-", value.strip())
    normalized = normalized.strip("-._")
    if not normalized:
        raise ValueError("Informe um ID valido para o agente.")
    if len(normalized) > 80:
        raise ValueError("O ID do agente deve ter no maximo 80 caracteres.")
    return normalized

AGENT_AUTO_ID_RE = re.compile(r"^maquina-(\d{2})(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?$")


def normalize_agent_suffix(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value.strip().lower())
    normalized = "".join(char for char in normalized if not unicodedata.combining(char))
    normalized = re.sub(r"[^a-z0-9]+", "-", normalized)
    normalized = normalized.strip("-")
    if len(normalized) > 40:
        normalized = normalized[:40].strip("-")
    return normalized


def compose_agent_id(auto_id: str, suffix: str = "") -> str:
    suffix = normalize_agent_suffix(suffix)
    return f"{auto_id}-{suffix}" if suffix else auto_id


def split_agent_id(agent_id: str) -> tuple[str, str]:
    match = AGENT_AUTO_ID_RE.match(agent_id.strip().lower())
    if not match:
        return "", normalize_agent_suffix(agent_id)
    base = f"maquina-{match.group(1)}"
    suffix = agent_id[len(base):].lstrip("-")
    return base, normalize_agent_suffix(suffix)


@dataclass(frozen=True)
class Config:
    supabase_url: str
    supabase_key: str
    source_mode: str
    app_state_table: str
    app_state_row_id: str
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

        source_mode = os.getenv("SOURCE_MODE", "app_state").strip().lower()
        if source_mode not in {"app_state", "table"}:
            raise SystemExit("SOURCE_MODE precisa ser app_state ou table.")

        agent_id = os.getenv("AGENT_ID") or socket.gethostname()

        return cls(
            supabase_url=supabase_url,
            supabase_key=supabase_key,
            source_mode=source_mode,
            app_state_table=os.getenv("APP_STATE_TABLE", "app_state"),
            app_state_row_id=os.getenv("APP_STATE_ROW_ID", "main"),
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
        if self.config.source_mode == "app_state":
            return self.fetch_app_state_equipment()

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

    def fetch_app_state_equipment(self) -> list[dict[str, Any]]:
        row_id = urllib.parse.quote(self.config.app_state_row_id, safe="")
        table = urllib.parse.quote(self.config.app_state_table, safe="")
        rows = self._request("GET", f"{table}?id=eq.{row_id}&select=data&limit=1")
        data = rows[0].get("data") if rows else {}
        equipment = data.get("equipments") if isinstance(data, dict) else []
        if not isinstance(equipment, list):
            return []
        return equipment

    def upsert_results(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        params = urllib.parse.urlencode(
            {
                "on_conflict": "equipamento_id,agente_id,janela_inicio,motivo",
            }
        )
        self._request("POST", f"{self.config.results_table}?{params}", rows)

    def fetch_recent_agent_ids(self, minutes: int = 30) -> list[str]:
        since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        table = urllib.parse.quote(self.config.results_table, safe="")
        query = urllib.parse.urlencode(
            {
                "select": "agente_id,criado_em",
                "criado_em": f"gte.{since}",
                "order": "criado_em.desc",
                "limit": "500",
            }
        )
        rows = self._request("GET", f"{table}?{query}") or []
        agent_ids: list[str] = []
        seen: set[str] = set()
        for row in rows:
            agent_id = str(row.get("agente_id", "")).strip()
            if agent_id and agent_id not in seen:
                seen.add(agent_id)
                agent_ids.append(agent_id)
        return agent_ids


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
        self.timezone = self._load_timezone(config.timezone_name)
        self.lock = threading.Lock()
        self.last_run: dict[str, Any] | None = None
        self.scheduler_enabled = True
        self.started_at = datetime.now(self.timezone)
        self.active_agent_ids: list[str] = []
        self._ensure_agent_identity()

    def _ensure_agent_identity(self) -> None:
        hostname = socket.gethostname().strip().lower()
        current_auto = os.getenv("AGENT_AUTO_ID", "").strip().lower()
        current_host = os.getenv("AGENT_MACHINE_HOST", "").strip().lower()
        current_suffix = normalize_agent_suffix(os.getenv("AGENT_NAME_SUFFIX", ""))

        legacy_base, legacy_suffix = split_agent_id(self.config.agent_id)
        if not current_suffix and legacy_suffix and not legacy_suffix.startswith("vpn"):
            current_suffix = legacy_suffix

        try:
            self.active_agent_ids = self.client.fetch_recent_agent_ids()
        except Exception as exc:
            self.active_agent_ids = []
            print(f"Nao foi possivel consultar agentes ativos: {exc}")

        if not current_auto or current_host != hostname:
            current_auto = self._next_auto_agent_id(self.active_agent_ids)
            update_dotenv_value("AGENT_AUTO_ID", current_auto)
            update_dotenv_value("AGENT_MACHINE_HOST", hostname)
            update_dotenv_value("AGENT_NAME_SUFFIX", current_suffix)

        final_id = compose_agent_id(current_auto, current_suffix)
        update_dotenv_value("AGENT_ID", final_id)
        object.__setattr__(self.config, "agent_id", final_id)

    def _next_auto_agent_id(self, active_agent_ids: list[str]) -> str:
        used = set()
        for agent_id in active_agent_ids:
            match = AGENT_AUTO_ID_RE.match(agent_id.strip().lower())
            if match:
                used.add(int(match.group(1)))
        for number in range(1, 100):
            if number not in used:
                return f"maquina-{number:02d}"
        return "maquina-99"

    def _agent_identity(self) -> dict[str, str]:
        auto_id, suffix = split_agent_id(self.config.agent_id)
        return {
            "auto_id": auto_id or os.getenv("AGENT_AUTO_ID", ""),
            "suffix": suffix or normalize_agent_suffix(os.getenv("AGENT_NAME_SUFFIX", "")),
            "final_id": self.config.agent_id,
        }
    def _load_timezone(self, timezone_name: str) -> timezone:
        try:
            return ZoneInfo(timezone_name)
        except ZoneInfoNotFoundError:
            local_timezone = datetime.now().astimezone().tzinfo
            if local_timezone is None:
                return timezone.utc
            print(f"Fuso {timezone_name} indisponivel no Python. Usando fuso local da maquina.")
            return local_timezone

    def run_forever(self) -> None:
        self._start_http_server()
        print(f"Agente {self.config.agent_id} iniciado.")
        print(f"Proximo ping automatico: {self.next_slot(datetime.now(self.timezone)).isoformat()}")

        while True:
            now = datetime.now(self.timezone)
            slot = self.next_slot(now)
            sleep_seconds = max(0.0, (slot - now).total_seconds())
            time.sleep(sleep_seconds)
            if not self.scheduler_enabled:
                print(f"Ping automatico pausado para a janela {slot.isoformat()}.")
                continue
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
                equipment_id, ip, name = self._equipment_fields(item)

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

    def get_status(self) -> dict[str, Any]:
        now = datetime.now(self.timezone)
        running = self.lock.locked()
        try:
            self.active_agent_ids = self.client.fetch_recent_agent_ids()
        except Exception:
            pass
        identity = self._agent_identity()
        return {
            "ok": True,
            "agent_id": self.config.agent_id,
            "agent_identity": identity,
            "active_agents": self.active_agent_ids,
            "scheduler_enabled": self.scheduler_enabled,
            "running_cycle": running,
            "started_at": self.started_at.isoformat(),
            "now": now.isoformat(),
            "next_scheduled_ping": self.next_slot(now).isoformat() if self.scheduler_enabled else None,
            "last_run": self.last_run,
            "config": {
                "interval_minutes": self.config.interval_minutes,
                "ping_timeout_ms": self.config.ping_timeout_ms,
                "ping_attempts": self.config.ping_attempts,
                "supabase_url": self.config.supabase_url,
                "source_mode": self.config.source_mode,
            },
        }

    def enable_scheduler(self) -> dict[str, Any]:
        self.scheduler_enabled = True
        return self.get_status()

    def disable_scheduler(self) -> dict[str, Any]:
        self.scheduler_enabled = False
        return self.get_status()

    def update_agent_id(self, agent_id: str = "", suffix: str = "") -> dict[str, Any]:
        identity = self._agent_identity()
        auto_id = identity["auto_id"] or self._next_auto_agent_id(self.active_agent_ids)
        next_suffix = normalize_agent_suffix(suffix if suffix != "" else agent_id)
        final_id = compose_agent_id(auto_id, next_suffix)
        update_dotenv_value("AGENT_AUTO_ID", auto_id)
        update_dotenv_value("AGENT_NAME_SUFFIX", next_suffix)
        update_dotenv_value("AGENT_ID", final_id)
        object.__setattr__(self.config, "agent_id", final_id)
        return self.get_status()

    def _equipment_fields(self, item: dict[str, Any]) -> tuple[str, str, str | None]:
        if self.config.source_mode == "app_state":
            equipment_id = str(item.get("id", "")).strip()
            ip = str(item.get("ip", "")).strip()
            name_parts = [
                str(item.get("identification", "")).strip(),
                str(item.get("equipmentName", "")).strip(),
            ]
            name = " - ".join(part for part in name_parts if part) or None
            return equipment_id, ip, name

        equipment_id = str(item.get(self.config.equipment_id_column, "")).strip()
        ip = str(item.get(self.config.equipment_ip_column, "")).strip()
        raw_name = item.get(self.config.equipment_name_column)
        name = str(raw_name) if raw_name is not None else None
        return equipment_id, ip, name

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
                path = urllib.parse.urlparse(self.path).path
                if path == "/":
                    self._send_html(agent.render_dashboard())
                    return
                if path in {"/health", "/api/status"}:
                    self._send(200, agent.get_status())
                    return
                if path != "/health":
                    self._send(404, {"ok": False, "message": "Rota nao encontrada."})
                    return

            def do_POST(self) -> None:
                path = urllib.parse.urlparse(self.path).path
                if path in {"/api/start", "/start"}:
                    self._send(200, agent.enable_scheduler())
                    return
                if path in {"/api/stop", "/stop"}:
                    self._send(200, agent.disable_scheduler())
                    return
                if path == "/api/config":
                    payload = self._read_json()
                    try:
                        self._send(200, agent.update_agent_id(str(payload.get("agent_id", "")), str(payload.get("agent_suffix", ""))))
                    except ValueError as exc:
                        self._send(400, {"ok": False, "message": str(exc)})
                    return
                if path not in {"/force-ping", "/api/force-ping"}:
                    self._send(404, {"ok": False, "message": "Rota nao encontrada."})
                    return
                result = agent.run_cycle(reason="manual")
                self._send(200 if result.get("ok") else 409, result)

            def do_OPTIONS(self) -> None:
                self.send_response(204)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "Content-Type")
                self.end_headers()

            def _read_json(self) -> dict[str, Any]:
                length = int(self.headers.get("Content-Length", "0") or "0")
                if length <= 0:
                    return {}
                raw = self.rfile.read(length).decode("utf-8", errors="replace")
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    return {}
                return payload if isinstance(payload, dict) else {}

            def log_message(self, format: str, *args: Any) -> None:
                return

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_html(self, html: str) -> None:
                data = html.encode("utf-8")
                self.send_response(200)
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        server = ThreadingHTTPServer((self.config.http_host, self.config.http_port), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"HTTP local em http://{self.config.http_host}:{self.config.http_port}")

    def render_dashboard(self) -> str:
        return """<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Agente de Monitoramento</title>
  <style>
    :root {
      --bg: #f5f3fb;
      --panel: #ffffff;
      --panel-soft: #f8f5ff;
      --text: #211833;
      --muted: #6d6384;
      --line: #e5def4;
      --primary: #5a31d6;
      --primary-dark: #4320aa;
      --ok: #11845b;
      --bad: #c6344a;
      --warn: #d88716;
      --shadow: 0 18px 42px rgba(67, 32, 170, 0.12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: linear-gradient(180deg, #fbfaff 0%, var(--bg) 100%);
      color: var(--text);
      font-family: Arial, Helvetica, sans-serif;
    }
    main { width: min(1120px, calc(100% - 28px)); margin: 0 auto; padding: 24px 0; }
    header, .panel, .metric-card { background: var(--panel); border: 1px solid var(--line); border-radius: 18px; box-shadow: var(--shadow); }
    header { display: grid; grid-template-columns: minmax(0, 1fr) auto; gap: 16px; align-items: center; padding: 18px; margin-bottom: 14px; }
    h1, h2, h3, p { margin: 0; }
    h1 { font-size: 28px; line-height: 1.1; }
    h2 { font-size: 22px; margin-bottom: 6px; }
    h3 { font-size: 15px; }
    p, .muted { color: var(--muted); }
    .agent-badge { display: grid; gap: 4px; justify-items: end; }
    .agent-badge span { color: var(--muted); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .08em; }
    .agent-badge strong { padding: 9px 12px; border-radius: 999px; background: #ece6fb; color: var(--primary-dark); overflow-wrap: anywhere; }
    .toolbar { display: flex; flex-wrap: wrap; gap: 10px; margin-top: 14px; }
    button { border: 0; border-radius: 12px; min-height: 42px; padding: 0 16px; background: var(--primary); color: white; font-weight: 800; cursor: pointer; }
    button:hover { background: var(--primary-dark); }
    button.secondary { background: #ece6fb; color: var(--primary-dark); }
    button.secondary:hover { background: #ddd2f7; }
    button.danger { background: #c6344a; }
    button:disabled { cursor: wait; opacity: .65; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-bottom: 14px; }
    .metric-card { padding: 16px; min-height: 116px; display: grid; align-content: space-between; }
    .metric-card small { color: var(--muted); font-size: 11px; font-weight: 800; text-transform: uppercase; letter-spacing: .07em; }
    .metric-card strong { font-size: 24px; line-height: 1.1; overflow-wrap: anywhere; }
    .metric-card span { color: var(--muted); font-weight: 700; }
    .panel { padding: 18px; margin-top: 14px; }
    .status-dot { display: inline-flex; align-items: center; gap: 7px; }
    .status-dot::before { content: ""; width: 9px; height: 9px; border-radius: 999px; background: currentColor; box-shadow: 0 0 0 5px color-mix(in srgb, currentColor 14%, transparent); }
    .status-ok { color: var(--ok); }
    .status-bad { color: var(--bad); }
    .status-warn { color: var(--warn); }
    .status-list { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px; margin-top: 14px; }
    .status-item { padding: 14px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel-soft); }
    .status-item small { display: block; color: var(--muted); font-weight: 800; text-transform: uppercase; font-size: 11px; margin-bottom: 6px; }
    .agent-row { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
    .agent-chip { padding: 7px 10px; border-radius: 999px; background: #ece6fb; color: var(--primary-dark); font-weight: 800; font-size: 13px; }
    .identity-grid { display: grid; grid-template-columns: minmax(180px, .55fr) minmax(0, 1fr) auto; gap: 12px; align-items: end; margin-top: 14px; }
    label { display: grid; gap: 7px; color: var(--muted); font-size: 12px; font-weight: 800; text-transform: uppercase; letter-spacing: .04em; }
    input { width: 100%; min-height: 42px; border: 1px solid var(--line); border-radius: 12px; padding: 0 12px; color: var(--text); background: white; font: inherit; font-weight: 700; }
    input[readonly] { background: #f1ecfb; color: var(--primary-dark); }
    .final-name { margin-top: 12px; padding: 12px; border: 1px solid var(--line); border-radius: 14px; background: var(--panel-soft); color: var(--muted); }
    .final-name strong { color: var(--text); }
    .message { margin-top: 10px; color: var(--muted); }
    table { width: 100%; border-collapse: collapse; margin-top: 12px; }
    th, td { text-align: left; border-bottom: 1px solid var(--line); padding: 11px 8px; vertical-align: top; }
    th { color: var(--muted); font-size: 12px; text-transform: uppercase; }
    .steps { margin: 12px 0 0; padding-left: 20px; color: var(--text); line-height: 1.55; }
    code { background: #f0eafa; color: var(--primary-dark); border-radius: 5px; padding: 2px 5px; }
    @media (max-width: 850px) { header, .identity-grid { grid-template-columns: 1fr; } .agent-badge { justify-items: start; } .grid, .status-list { grid-template-columns: repeat(2, minmax(0, 1fr)); } }
    @media (max-width: 520px) { main { width: min(100% - 18px, 1120px); } .grid, .status-list { grid-template-columns: 1fr; } button { width: 100%; } }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Agente de Monitoramento</h1>
        <p>Controle local dos pings enviados ao Supabase.</p>
        <div class="toolbar">
          <button type="button" id="startButton">Ligar automatico</button>
          <button type="button" class="danger" id="stopButton">Desligar automatico</button>
          <button type="button" class="secondary" id="forceButton">Forcar ping agora</button>
          <button type="button" class="secondary" id="refreshButton">Atualizar</button>
        </div>
      </div>
      <div class="agent-badge">
        <span>Agente ativo</span>
        <strong id="agentId">Carregando...</strong>
      </div>
    </header>

    <section class="grid" aria-label="Resumo">
      <article class="metric-card"><small>Automatico</small><strong id="scheduler">-</strong><span id="schedulerHint">-</span></article>
      <article class="metric-card"><small>Ciclo atual</small><strong id="running">-</strong><span id="runningHint">-</span></article>
      <article class="metric-card"><small>Proximo ping</small><strong id="nextPing">-</strong><span>Janela programada</span></article>
      <article class="metric-card"><small>Ultimo monitoramento</small><strong id="lastSummary">-</strong><span id="lastFinished">-</span></article>
    </section>

    <section class="panel">
      <h2>Identificacao do agente</h2>
      <p>O ID automatico evita nomes repetidos entre maquinas. Edite somente o complemento para identificar o local.</p>
      <div class="identity-grid">
        <label>ID automatico<input id="agentAutoIdInput" type="text" readonly></label>
        <label>Identificacao da maquina<input id="agentSuffixInput" type="text" autocomplete="off" placeholder="sertaneja"></label>
        <button type="button" id="saveConfigButton">Salvar nome</button>
      </div>
      <div class="final-name">Nome final do agente: <strong id="finalAgentName">-</strong></div>
      <p class="message" id="configMessage">Use apenas letras minusculas, numeros e hifen. Maiusculas, acentos e simbolos sao ajustados automaticamente.</p>
    </section>

    <section class="panel">
      <h2>Monitoramento</h2>
      <div class="status-list">
        <div class="status-item"><small>Equipamentos</small><strong id="equipmentCount">-</strong></div>
        <div class="status-item"><small>Online no ultimo ping</small><strong class="status-ok" id="onlineCount">-</strong></div>
        <div class="status-item"><small>Offline no ultimo ping</small><strong class="status-bad" id="offlineCount">-</strong></div>
      </div>
      <table>
        <tbody>
          <tr><th>Iniciado em</th><td id="startedAt">-</td></tr>
          <tr><th>Agora</th><td id="now">-</td></tr>
          <tr><th>Fonte de equipamentos</th><td id="sourceMode">-</td></tr>
          <tr><th>Intervalo</th><td id="interval">-</td></tr>
          <tr><th>Timeout</th><td id="timeout">-</td></tr>
        </tbody>
      </table>
    </section>

    <section class="panel">
      <h2>Agentes vistos recentemente</h2>
      <p>Lista baseada nos resultados enviados nos ultimos 30 minutos.</p>
      <div class="agent-row" id="activeAgents"><span class="agent-chip">Carregando...</span></div>
    </section>

    <section class="panel">
      <h2>Instrucoes</h2>
      <ol class="steps">
        <li>Deixe a VPN/rede da empresa conectada nesta maquina.</li>
        <li>Com o automatico ligado, o agente pinga em janelas como <code>10:00</code>, <code>10:30</code> e <code>11:00</code>.</li>
        <li>Use <strong>Forcar ping agora</strong> para validar imediatamente todos os equipamentos.</li>
        <li>A rota <code>/health</code> continua disponivel para uso tecnico em JSON.</li>
      </ol>
    </section>
  </main>

  <script>
    const nodes = {
      agentId: document.getElementById("agentId"), agentAutoIdInput: document.getElementById("agentAutoIdInput"), agentSuffixInput: document.getElementById("agentSuffixInput"), finalAgentName: document.getElementById("finalAgentName"), configMessage: document.getElementById("configMessage"),
      scheduler: document.getElementById("scheduler"), schedulerHint: document.getElementById("schedulerHint"), running: document.getElementById("running"), runningHint: document.getElementById("runningHint"), nextPing: document.getElementById("nextPing"), lastSummary: document.getElementById("lastSummary"), lastFinished: document.getElementById("lastFinished"),
      equipmentCount: document.getElementById("equipmentCount"), onlineCount: document.getElementById("onlineCount"), offlineCount: document.getElementById("offlineCount"), activeAgents: document.getElementById("activeAgents"),
      startedAt: document.getElementById("startedAt"), now: document.getElementById("now"), sourceMode: document.getElementById("sourceMode"), interval: document.getElementById("interval"), timeout: document.getElementById("timeout"),
      startButton: document.getElementById("startButton"), stopButton: document.getElementById("stopButton"), forceButton: document.getElementById("forceButton"), refreshButton: document.getElementById("refreshButton"), saveConfigButton: document.getElementById("saveConfigButton"),
    };
    function sanitizeSuffix(value) { return String(value || "").toLowerCase().normalize("NFD").replace(/[\\u0300-\\u036f]/g, "").replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 40).replace(/-+$/g, ""); }
    function composeName() { const base = nodes.agentAutoIdInput.value || "maquina-01"; const suffix = sanitizeSuffix(nodes.agentSuffixInput.value); return suffix ? `${base}-${suffix}` : base; }
    function syncFinalName() { nodes.agentSuffixInput.value = sanitizeSuffix(nodes.agentSuffixInput.value); nodes.finalAgentName.textContent = composeName(); }
    function formatDate(value) { if (!value) return "-"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString("pt-BR"); }
    function formatShort(value) { if (!value) return "-"; const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" }); }
    function setBusy(isBusy) { [nodes.startButton,nodes.stopButton,nodes.forceButton,nodes.refreshButton,nodes.saveConfigButton].forEach((button) => button.disabled = isBusy); }
    function renderAgents(items, current) { const agents = Array.isArray(items) && items.length ? items : [current].filter(Boolean); nodes.activeAgents.innerHTML = agents.map((id) => `<span class="agent-chip">${id === current ? "Atual: " : ""}${id}</span>`).join("") || '<span class="agent-chip">Sem registros recentes</span>'; }
    function renderStatus(status) {
      const last = status.last_run || null; const identity = status.agent_identity || {};
      nodes.agentId.textContent = status.agent_id || "-"; nodes.agentAutoIdInput.value = identity.auto_id || "maquina-01"; nodes.agentSuffixInput.value = sanitizeSuffix(identity.suffix || ""); syncFinalName();
      nodes.scheduler.textContent = status.scheduler_enabled ? "Ligado" : "Desligado"; nodes.scheduler.className = status.scheduler_enabled ? "status-dot status-ok" : "status-dot status-bad"; nodes.schedulerHint.textContent = status.scheduler_enabled ? "Pings automaticos ativos" : "Pings automaticos pausados";
      nodes.running.textContent = status.running_cycle ? "Pingando" : "Livre"; nodes.running.className = status.running_cycle ? "status-dot status-warn" : "status-dot status-ok"; nodes.runningHint.textContent = status.running_cycle ? "Ciclo em andamento" : "Pronto para novo ciclo";
      nodes.nextPing.textContent = status.next_scheduled_ping ? formatShort(status.next_scheduled_ping) : "Pausado";
      nodes.lastSummary.textContent = last ? `${last.online_count} online / ${last.offline_count} offline` : "Sem execucao"; nodes.lastFinished.textContent = last ? `Finalizado em ${formatDate(last.finished_at)}` : "Nenhum ping registrado nesta sessao";
      nodes.equipmentCount.textContent = last ? last.equipment_count : "-"; nodes.onlineCount.textContent = last ? last.online_count : "-"; nodes.offlineCount.textContent = last ? last.offline_count : "-";
      nodes.startedAt.textContent = formatDate(status.started_at); nodes.now.textContent = formatDate(status.now); nodes.sourceMode.textContent = status.config?.source_mode || "-"; nodes.interval.textContent = `${status.config?.interval_minutes || "-"} minutos`; nodes.timeout.textContent = `${status.config?.ping_timeout_ms || "-"} ms, ${status.config?.ping_attempts || "-"} tentativa(s)`;
      renderAgents(status.active_agents, status.agent_id);
    }
    async function requestStatus() { const response = await fetch("/api/status", { cache: "no-store" }); if (!response.ok) throw new Error(`Status ${response.status}`); const status = await response.json(); renderStatus(status); return status; }
    async function postAction(path) { setBusy(true); try { const response = await fetch(path, { method: "POST", cache: "no-store" }); const payload = await response.json(); if (!response.ok) throw new Error(payload.message || `Erro ${response.status}`); if (path.includes("force")) await requestStatus(); else renderStatus(payload); } catch (error) { nodes.configMessage.textContent = `Erro: ${error.message}`; } finally { setBusy(false); } }
    async function saveConfig() { setBusy(true); nodes.configMessage.textContent = "Salvando..."; try { syncFinalName(); const response = await fetch("/api/config", { method: "POST", cache: "no-store", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent_suffix: nodes.agentSuffixInput.value }) }); const payload = await response.json(); if (!response.ok) throw new Error(payload.message || `Erro ${response.status}`); renderStatus(payload); nodes.configMessage.textContent = "Nome salvo no .env e aplicado no agente em execucao."; } catch (error) { nodes.configMessage.textContent = `Erro: ${error.message}`; } finally { setBusy(false); } }
    nodes.agentSuffixInput.addEventListener("input", syncFinalName); nodes.startButton.addEventListener("click", () => postAction("/api/start")); nodes.stopButton.addEventListener("click", () => postAction("/api/stop")); nodes.forceButton.addEventListener("click", () => postAction("/api/force-ping")); nodes.refreshButton.addEventListener("click", () => requestStatus().catch((error) => nodes.configMessage.textContent = `Erro: ${error.message}`)); nodes.saveConfigButton.addEventListener("click", saveConfig);
    requestStatus().catch((error) => { nodes.configMessage.textContent = `Erro ao carregar status: ${error.message}`; }); window.setInterval(() => { if (!nodes.forceButton.disabled) requestStatus().catch(() => {}); }, 10000);
  </script>
</body>
</html>"""

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







