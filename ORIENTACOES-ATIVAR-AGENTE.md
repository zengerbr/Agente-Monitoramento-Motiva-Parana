# Orientacoes para ativar o agente

Este agente deve rodar em uma maquina com acesso a VPN/rede da empresa.

## Antes de ativar

Confira estes pontos:

- Python instalado na maquina.
- A pasta do agente copiada para a maquina.
- Arquivo `.env` preenchido dentro da pasta do agente.
- VPN/rede da empresa conectada.
- Internet liberada para acessar o Supabase.

Em cada maquina, altere o identificador no `.env`:

```env
AGENT_ID=maquina-vpn-01
```

Em outra maquina, use outro nome:

```env
AGENT_ID=maquina-vpn-02
```

Isso permite saber qual maquina respondeu cada ping.

## Modo 1: iniciar normal pelo PowerShell

Abra o PowerShell e entre na pasta do agente:

```powershell
cd "C:\Users\Junior\Documents\01_Projetos Codex\Agente-Monitoramento-Motiva-Parana"
```

Inicie o agente:

```powershell
python agent.py run
```

Quando estiver funcionando, deve aparecer algo parecido com:

```text
HTTP local em http://127.0.0.1:8765
Agente maquina-vpn-01 iniciado.
Proximo ping automatico: ...
```

Para testar se ele esta ativo, abra:

```text
http://127.0.0.1:8765/health
```

Se aparecer `"ok": true`, o agente esta rodando.

Para abrir a tela amigavel do agente, acesse:

```text
http://127.0.0.1:8765/
```

Ou de dois cliques no arquivo:

```text
abrir-interface-agente.vbs
```

Nessa tela voce consegue:

- ver se o agente esta ligado;
- ver se existe ping em andamento;
- ver a proxima janela automatica;
- ver o ultimo resultado;
- ligar ou desligar os pings automaticos;
- forcar um ping imediato.
- alterar o ID do agente na aba `Configuracoes`.

## Modo 2: iniciar discreto sem deixar PowerShell aberto

Dentro da pasta do agente existe o arquivo:

```text
iniciar-agente-discreto.vbs
```

Para iniciar o agente de forma discreta, de dois cliques nesse arquivo.

Ele executa:

```powershell
python agent.py run
```

mas sem deixar janela do PowerShell aberta.

Para confirmar que funcionou, abra:

```text
http://127.0.0.1:8765/health
```

Ou abra a tela de controle:

```text
http://127.0.0.1:8765/
```

## Modo 3: iniciar automaticamente pelo Agendador de Tarefas

Esse e o modo recomendado para deixar o agente em producao.

### Criar a tarefa

1. Abra o menu Iniciar do Windows.
2. Pesquise por `Agendador de Tarefas`.
3. Clique em `Criar Tarefa...`.

Na aba `Geral`:

1. Nome: `Agente Monitoramento Motiva Parana`.
2. Marque `Executar estando o usuario conectado ou nao`.
3. Marque `Executar com privilegios mais altos`.

Na aba `Disparadores`:

1. Clique em `Novo...`.
2. Em `Iniciar a tarefa`, escolha `Ao fazer logon`.
3. Clique em `OK`.

Na aba `Acoes`:

1. Clique em `Novo...`.
2. Em `Acao`, deixe `Iniciar um programa`.
3. Em `Programa/script`, coloque o caminho do Python.

Exemplo:

```text
C:\Users\Junior\AppData\Local\Programs\Python\Python313\python.exe
```

4. Em `Adicionar argumentos`, coloque:

```text
agent.py run
```

5. Em `Iniciar em`, coloque a pasta do agente:

```text
C:\Users\Junior\Documents\01_Projetos Codex\Agente-Monitoramento-Motiva-Parana
```

6. Clique em `OK`.

Na aba `Condicoes`:

- Se for notebook, desmarque `Iniciar a tarefa somente se o computador estiver ligado a energia eletrica`.

Na aba `Configuracoes`:

Marque:

- `Permitir que a tarefa seja executada sob demanda`.
- `Executar a tarefa o mais rapido possivel apos uma inicializacao agendada perdida`.
- `Se a tarefa falhar, reiniciar a cada: 1 minuto`.
- `Tentar reiniciar ate: 3 vezes`.

Desmarque:

- `Parar a tarefa se ela for executada por mais de...`.

Depois clique em `OK`. O Windows pode pedir a senha do usuario.

### Testar a tarefa

1. No Agendador de Tarefas, encontre `Agente Monitoramento Motiva Parana`.
2. Clique com o botao direito.
3. Clique em `Executar`.
4. Abra:

```text
http://127.0.0.1:8765/health
```

Se aparecer `"ok": true`, a tarefa iniciou o agente.

Tambem e possivel validar pela tela:

```text
http://127.0.0.1:8765/
```

## Tela de controle local

Com o agente rodando, abra:

```text
http://127.0.0.1:8765/
```

Essa tela so existe na maquina onde o agente esta instalado.

Para abrir sem digitar endereco no navegador, de dois cliques:

```text
abrir-interface-agente.vbs
```

O botao `Ligar automatico` retoma os pings nas janelas configuradas, como `10:00`, `10:30`, `11:00`.

O botao `Desligar automatico` pausa os pings automaticos, mas nao fecha o agente. Assim voce ainda consegue religar pela propria tela.

O botao `Forcar ping agora` executa um ping imediato em todos os equipamentos e grava o resultado no Supabase.

Na aba `Configuracoes`, voce pode alterar o `ID do agente`. Esse valor e salvo no arquivo `.env` e aplicado no agente em execucao.

Na aba `Instrucoes`, a propria interface mostra um resumo de uso.

## Forcar ping pelo painel

Com o agente rodando, o botao `Pingar todos` do painel chama:

```text
http://127.0.0.1:8765/force-ping
```

Essa rota precisa ser chamada pelo painel como `POST`. Abrir esse endereco direto no navegador pode mostrar erro ou rota nao encontrada, e isso e normal.

## Parar o agente

Se estiver rodando no PowerShell, pressione:

```text
Ctrl + C
```

Se estiver rodando pelo `.vbs` ou pelo Agendador de Tarefas, encerre pelo Gerenciador de Tarefas procurando por `python.exe`, ou finalize a tarefa pelo Agendador.
