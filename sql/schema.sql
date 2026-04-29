create table if not exists public.monitoramento_ping_resultados (
  id bigint generated always as identity primary key,
  equipamento_id text not null,
  equipamento_nome text,
  ip inet not null,
  agente_id text not null,
  janela_inicio timestamptz not null,
  sucesso boolean not null,
  latencia_ms integer,
  erro text,
  motivo text not null default 'scheduled',
  criado_em timestamptz not null default now(),
  unique (equipamento_id, agente_id, janela_inicio, motivo)
);

create index if not exists idx_ping_resultados_janela
  on public.monitoramento_ping_resultados (janela_inicio desc);

create index if not exists idx_ping_resultados_equipamento_janela
  on public.monitoramento_ping_resultados (equipamento_id, janela_inicio desc);

create or replace view public.monitoramento_status_atual as
with ultima_janela as (
  select max(janela_inicio) as janela_inicio
  from public.monitoramento_ping_resultados
),
resumo as (
  select
    r.equipamento_id,
    max(r.equipamento_nome) as equipamento_nome,
    max(r.ip::text) as ip,
    r.janela_inicio,
    bool_or(r.sucesso) as online,
    count(*) as total_respostas,
    count(*) filter (where r.sucesso) as sucessos,
    count(distinct r.agente_id) as agentes_respondendo,
    min(r.latencia_ms) filter (where r.sucesso) as melhor_latencia_ms,
    max(r.criado_em) as atualizado_em
  from public.monitoramento_ping_resultados r
  join ultima_janela u on u.janela_inicio = r.janela_inicio
  group by r.equipamento_id, r.janela_inicio
)
select *
from resumo;

comment on table public.monitoramento_ping_resultados is
  'Resultados de ping enviados pelos agentes instalados em maquinas com acesso a VPN/rede.';

comment on view public.monitoramento_status_atual is
  'Status consolidado da ultima janela: online quando qualquer agente teve sucesso.';

