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
        return {
            "ok": True,
            "agent_id": self.config.agent_id,
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

    def update_agent_id(self, agent_id: str) -> dict[str, Any]:
        normalized = normalize_agent_id(agent_id)
        update_dotenv_value("AGENT_ID", normalized)
        object.__setattr__(self.config, "agent_id", normalized)
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
                        self._send(200, agent.update_agent_id(str(payload.get("agent_id", ""))))
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
  <title>Agente de Monitoramento Motiva Parana</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f5f3fb;
      --panel: #ffffff;
      --text: #211833;
      --muted: #6d6384;
      --line: #e5def4;
      --primary: #5a31d6;
      --primary-dark: #4320aa;
      --ok: #11845b;
      --bad: #c6344a;
      --warn: #a66b00;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: Arial, Helvetica, sans-serif;
    }
    main {
      width: min(980px, calc(100% - 32px));
      margin: 0 auto;
      padding: 28px 0;
    }
    header {
      display: flex;
      align-items: flex-start;
      justify-content: space-between;
      gap: 18px;
      margin-bottom: 18px;
    }
    h1 {
      margin: 0 0 6px;
      font-size: 28px;
      line-height: 1.1;
    }
    p { margin: 0; color: var(--muted); }
    .pill {
      display: inline-flex;
      align-items: center;
      min-height: 34px;
      border-radius: 999px;
      padding: 7px 12px;
      background: #ece6fb;
      color: var(--primary-dark);
      font-weight: 700;
      white-space: nowrap;
    }
    .grid {
      display: grid;
      grid-template-columns: repeat(4, minmax(0, 1fr));
      gap: 12px;
      margin: 18px 0;
    }
    .card, .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 12px 32px rgba(67, 32, 170, 0.08);
    }
    .card { padding: 16px; min-height: 98px; }
    .card small {
      display: block;
      color: var(--muted);
      font-weight: 700;
      margin-bottom: 10px;
      text-transform: uppercase;
      font-size: 11px;
    }
    .card strong {
      display: block;
      font-size: 23px;
      line-height: 1.1;
      overflow-wrap: anywhere;
    }
    .panel { padding: 18px; margin-top: 12px; }
    .tabs {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      margin: 18px 0 6px;
    }
    .tab-button {
      background: #ece6fb;
      color: var(--primary-dark);
      min-height: 38px;
    }
    .tab-button.is-active {
      background: var(--primary);
      color: white;
    }
    .tab-panel[hidden] { display: none; }
    .actions {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 16px;
    }
    button, a.button {
      border: 0;
      border-radius: 8px;
      min-height: 42px;
      padding: 0 16px;
      background: var(--primary);
      color: white;
      font-weight: 700;
      cursor: pointer;
      text-decoration: none;
      display: inline-flex;
      align-items: center;
      justify-content: center;
    }
    button:hover, a.button:hover { background: var(--primary-dark); }
    button.secondary { background: #ece6fb; color: var(--primary-dark); }
    button.secondary:hover { background: #ddd2f7; }
    button.danger { background: #c6344a; }
    button.danger:hover { background: #a5283a; }
    button:disabled {
      cursor: wait;
      opacity: 0.65;
    }
    label {
      display: grid;
      gap: 8px;
      color: var(--muted);
      font-weight: 700;
      margin-top: 14px;
    }
    input {
      width: 100%;
      min-height: 42px;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 0 12px;
      color: var(--text);
      font: inherit;
      font-weight: 600;
      background: white;
    }
    .note {
      margin-top: 10px;
      padding: 12px;
      border-radius: 8px;
      background: #f7f3ff;
      border: 1px solid var(--line);
      color: var(--muted);
    }
    .steps {
      margin: 12px 0 0;
      padding-left: 20px;
      color: var(--text);
      line-height: 1.55;
    }
    code {
      background: #f0eafa;
      color: var(--primary-dark);
      border-radius: 5px;
      padding: 2px 5px;
    }
    .status-ok { color: var(--ok); }
    .status-bad { color: var(--bad); }
    .status-warn { color: var(--warn); }
    .log {
      margin-top: 14px;
      padding: 14px;
      border-radius: 8px;
      background: #171421;
      color: #f7f2ff;
      min-height: 130px;
      overflow: auto;
      font-size: 13px;
      line-height: 1.45;
      white-space: pre-wrap;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      margin-top: 12px;
    }
    th, td {
      text-align: left;
      border-bottom: 1px solid var(--line);
      padding: 11px 8px;
      vertical-align: top;
    }
    th {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0;
    }
    @media (max-width: 760px) {
      header { display: block; }
      .pill { margin-top: 12px; }
      .grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 460px) {
      .grid { grid-template-columns: 1fr; }
      button, a.button { width: 100%; }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>Agente de Monitoramento</h1>
        <p>Controle local dos pings enviados ao Supabase.</p>
      </div>
      <span class="pill" id="agentId">Carregando...</span>
    </header>

    <section class="grid" aria-label="Resumo">
      <article class="card">
        <small>Automático</small>
        <strong id="scheduler">-</strong>
      </article>
      <article class="card">
        <small>Ciclo atual</small>
        <strong id="running">-</strong>
      </article>
      <article class="card">
        <small>Próximo ping</small>
        <strong id="nextPing">-</strong>
      </article>
      <article class="card">
        <small>Último resultado</small>
        <strong id="lastSummary">-</strong>
      </article>
    </section>

    <nav class="tabs" aria-label="Seções do agente">
      <button class="tab-button is-active" type="button" data-tab="control">Controle</button>
      <button class="tab-button" type="button" data-tab="settings">Configurações</button>
      <button class="tab-button" type="button" data-tab="instructions">Instruções</button>
    </nav>

    <section class="panel tab-panel" id="tab-control">
      <h2>Controle</h2>
      <p>Ligar pausa/retoma os pings automáticos. Forçar ping executa uma validação imediata.</p>
      <div class="actions">
        <button type="button" id="startButton">Ligar automático</button>
        <button type="button" class="danger" id="stopButton">Desligar automático</button>
        <button type="button" class="secondary" id="forceButton">Forçar ping agora</button>
        <button type="button" class="secondary" id="refreshButton">Atualizar status</button>
      </div>
      <div class="log" id="log">Carregando status...</div>
    </section>

    <section class="panel tab-panel" id="tab-settings" hidden>
      <h2>Configurações</h2>
      <p>Use um ID diferente em cada máquina para identificar quem respondeu ao ping.</p>
      <label>
        ID do agente
        <input id="agentIdInput" type="text" autocomplete="off" placeholder="maquina-vpn-01">
      </label>
      <div class="actions">
        <button type="button" id="saveConfigButton">Salvar ID</button>
      </div>
      <p class="note" id="configMessage">Use letras, números, ponto, hífen ou underline. Exemplo: <code>maquina-vpn-02</code>.</p>
    </section>

    <section class="panel tab-panel" id="tab-instructions" hidden>
      <h2>Instruções</h2>
      <ol class="steps">
        <li>Deixe a VPN/rede da empresa conectada nesta máquina.</li>
        <li>Com o automático ligado, o agente pinga nas janelas fechadas, como <code>10:00</code>, <code>10:30</code> e <code>11:00</code>.</li>
        <li>Use <strong>Forçar ping agora</strong> quando quiser validar imediatamente todos os equipamentos.</li>
        <li>O painel principal lê o resultado salvo no Supabase. Máquinas sem agente instalado apenas visualizam o status.</li>
        <li>Para rodar discreto, use o arquivo <code>iniciar-agente-discreto.vbs</code>.</li>
        <li>Para abrir esta tela sem digitar endereço, use <code>abrir-interface-agente.vbs</code>.</li>
      </ol>
      <p class="note">A rota <code>/health</code> é técnica e mostra JSON. A interface amigável fica em <code>http://127.0.0.1:8765/</code>.</p>
    </section>

    <section class="panel">
      <h2>Detalhes</h2>
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
  </main>

  <script>
    const nodes = {
      agentId: document.getElementById("agentId"),
      scheduler: document.getElementById("scheduler"),
      running: document.getElementById("running"),
      nextPing: document.getElementById("nextPing"),
      lastSummary: document.getElementById("lastSummary"),
      startedAt: document.getElementById("startedAt"),
      now: document.getElementById("now"),
      sourceMode: document.getElementById("sourceMode"),
      interval: document.getElementById("interval"),
      timeout: document.getElementById("timeout"),
      log: document.getElementById("log"),
      agentIdInput: document.getElementById("agentIdInput"),
      saveConfigButton: document.getElementById("saveConfigButton"),
      configMessage: document.getElementById("configMessage"),
      startButton: document.getElementById("startButton"),
      stopButton: document.getElementById("stopButton"),
      forceButton: document.getElementById("forceButton"),
      refreshButton: document.getElementById("refreshButton"),
    };

    function formatDate(value) {
      if (!value) return "-";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString("pt-BR");
    }

    function setBusy(isBusy) {
      nodes.startButton.disabled = isBusy;
      nodes.stopButton.disabled = isBusy;
      nodes.forceButton.disabled = isBusy;
      nodes.refreshButton.disabled = isBusy;
    }

    function renderStatus(status) {
      const last = status.last_run;
      nodes.agentId.textContent = status.agent_id || "-";
      nodes.agentIdInput.value = status.agent_id || "";
      nodes.scheduler.textContent = status.scheduler_enabled ? "Ligado" : "Desligado";
      nodes.scheduler.className = status.scheduler_enabled ? "status-ok" : "status-bad";
      nodes.running.textContent = status.running_cycle ? "Pingando" : "Livre";
      nodes.running.className = status.running_cycle ? "status-warn" : "status-ok";
      nodes.nextPing.textContent = status.next_scheduled_ping ? formatDate(status.next_scheduled_ping) : "Pausado";
      nodes.lastSummary.textContent = last ? `${last.online_count} online / ${last.offline_count} offline` : "Sem execução";
      nodes.startedAt.textContent = formatDate(status.started_at);
      nodes.now.textContent = formatDate(status.now);
      nodes.sourceMode.textContent = status.config?.source_mode || "-";
      nodes.interval.textContent = `${status.config?.interval_minutes || "-"} minutos`;
      nodes.timeout.textContent = `${status.config?.ping_timeout_ms || "-"} ms, ${status.config?.ping_attempts || "-"} tentativa(s)`;
      nodes.log.textContent = JSON.stringify(status, null, 2);
    }

    async function requestStatus() {
      const response = await fetch("/api/status", { cache: "no-store" });
      if (!response.ok) throw new Error(`Status ${response.status}`);
      const status = await response.json();
      renderStatus(status);
      return status;
    }

    async function postAction(path) {
      setBusy(true);
      try {
        const response = await fetch(path, { method: "POST", cache: "no-store" });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.message || `Erro ${response.status}`);
        if (path.includes("force")) {
          await requestStatus();
        } else {
          renderStatus(payload);
        }
      } catch (error) {
        nodes.log.textContent = `Erro: ${error.message}`;
      } finally {
        setBusy(false);
      }
    }

    async function saveConfig() {
      setBusy(true);
      nodes.configMessage.textContent = "Salvando...";
      try {
        const response = await fetch("/api/config", {
          method: "POST",
          cache: "no-store",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ agent_id: nodes.agentIdInput.value }),
        });
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.message || `Erro ${response.status}`);
        renderStatus(payload);
        nodes.configMessage.textContent = "ID salvo no .env e aplicado no agente em execução.";
      } catch (error) {
        nodes.configMessage.textContent = `Erro: ${error.message}`;
      } finally {
        setBusy(false);
      }
    }

    function showTab(name) {
      document.querySelectorAll(".tab-button").forEach((button) => {
        button.classList.toggle("is-active", button.dataset.tab === name);
      });
      document.querySelectorAll(".tab-panel").forEach((panel) => {
        panel.hidden = panel.id !== `tab-${name}`;
      });
    }

    nodes.startButton.addEventListener("click", () => postAction("/api/start"));
    nodes.stopButton.addEventListener("click", () => postAction("/api/stop"));
    nodes.forceButton.addEventListener("click", () => postAction("/api/force-ping"));
    nodes.saveConfigButton.addEventListener("click", saveConfig);
    nodes.refreshButton.addEventListener("click", () => requestStatus().catch((error) => {
      nodes.log.textContent = `Erro: ${error.message}`;
    }));
    document.querySelectorAll(".tab-button").forEach((button) => {
      button.addEventListener("click", () => showTab(button.dataset.tab));
    });

    requestStatus().catch((error) => {
      nodes.log.textContent = `Erro ao carregar status: ${error.message}`;
    });
    window.setInterval(() => {
      if (!nodes.forceButton.disabled) requestStatus().catch(() => {});
    }, 10000);
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
