Set shell = CreateObject("WScript.Shell")

Function IsAgentRunning()
  On Error Resume Next
  Set exec = shell.Exec("powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ""if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { '1' } else { '0' }""")
  output = exec.StdOut.ReadAll
  IsAgentRunning = InStr(output, "1") > 0
  On Error GoTo 0
End Function

If Not IsAgentRunning() Then
  shell.Popup "Nenhum agente discreto ativo foi encontrado nesta maquina.", 5, "Agente de Monitoramento", 64
  WScript.Quit
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ""$connections = Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue; foreach ($connection in $connections) { Stop-Process -Id $connection.OwningProcess -Force -ErrorAction SilentlyContinue }"""
shell.Run command, 0, True
WScript.Sleep 1000

If IsAgentRunning() Then
  shell.Popup "Nao foi possivel encerrar o agente automaticamente. Tente novamente ou feche pelo Gerenciador de Tarefas.", 7, "Agente de Monitoramento", 48
Else
  shell.Popup "Agente discreto encerrado.", 5, "Agente de Monitoramento", 64
End If
