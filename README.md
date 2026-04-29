# Agente de Monitoramento Motiva Parana

Agente complementar do projeto `Painel-Manutencao-Motiva-Parana`.

Ele roda em uma maquina conectada a VPN/rede da empresa, busca os equipamentos no Supabase, executa ping nos horarios fechados de 30 em 30 minutos (`10:00`, `10:30`, `11:00`...) e grava o resultado no banco.

A redundancia funciona porque varias maquinas podem rodar o mesmo agente. O equipamento deve ser considerado online quando qualquer agente conseguir pingar dentro da janela. Ele so deve ser considerado offline quando nao existir sucesso registrado pelos agentes naquela janela.

## Arquivos

- `agent.py`: servico principal do agente.
- `.env.example`: exemplo de configuracao.
- `sql/schema.sql`: tabelas e views sugeridas para o Supabase.

## Requisitos

- Python 3.10 ou superior.
- Maquina com acesso a VPN/rede onde os IPs respondem.
- URL e chave do Supabase.

O agente nao precisa instalar pacotes externos.

## Configuracao

Crie um arquivo `.env` baseado no exemplo:

```env
SUPABASE_URL=https://seu-projeto.supabase.co
SUPABASE_KEY=sua-chave-service-role-ou-chave-com-permissao

EQUIPMENT_TABLE=equipamentos
EQUIPMENT_ID_COLUMN=id
EQUIPMENT_IP_COLUMN=ip
EQUIPMENT_NAME_COLUMN=nome
EQUIPMENT_ACTIVE_COLUMN=ativo

RESULTS_TABLE=monitoramento_ping_resultados
AGENT_ID=maquina-vpn-01
PING_INTERVAL_MINUTES=30
PING_TIMEOUT_MS=1500
PING_ATTEMPTS=2
TIMEZONE=America/Sao_Paulo

HTTP_HOST=127.0.0.1
HTTP_PORT=8765
```

Se a tabela de equipamentos do seu painel tiver outros nomes de colunas, ajuste as variaveis `EQUIPMENT_*`.

## Banco de dados

Execute o conteudo de `sql/schema.sql` no Supabase SQL Editor.

Depois, adapte a tabela de equipamentos usada pelo agente. Por padrao ele procura:

- tabela: `equipamentos`
- id: `id`
- ip: `ip`
- nome: `nome`
- ativo: `ativo`

## Rodar o agente

```powershell
python agent.py run
```

Ele faz um ping automaticamente na proxima janela fechada. Exemplo: se iniciar `10:08`, o primeiro ciclo automatico sera `10:30`.

## Forcar ping imediato

Pelo terminal:

```powershell
python agent.py once --reason manual
```

Ou com o agente rodando, via HTTP local:

```powershell
Invoke-RestMethod -Method Post http://127.0.0.1:8765/force-ping
```

Tambem da para consultar um estado simples:

```powershell
Invoke-RestMethod http://127.0.0.1:8765/health
```

## Como o painel deve calcular online/offline

Use a view `monitoramento_status_atual` como fonte do painel:

- `online = true`: pelo menos um agente conseguiu pingar o equipamento na janela atual/mais recente.
- `online = false`: houve registros para a janela, mas nenhum agente conseguiu pingar.
- sem linha recente: nenhum agente registrou resultado recentemente; isso indica problema no monitoramento ou agentes parados.

Para evitar falso offline no minuto exato da virada da janela, o painel pode mostrar a ultima janela concluida ou aguardar alguns minutos de tolerancia.

