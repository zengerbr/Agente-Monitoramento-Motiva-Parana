Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

Function IsAgentRunning()
  On Error Resume Next
  Set exec = shell.Exec("powershell.exe -NoProfile -ExecutionPolicy Bypass -Command ""if (Get-NetTCPConnection -LocalPort 8765 -State Listen -ErrorAction SilentlyContinue) { '1' } else { '0' }""")
  output = exec.StdOut.ReadAll
  IsAgentRunning = InStr(output, "1") > 0
  On Error GoTo 0
End Function

agentFolder = filesystem.GetParentFolderName(WScript.ScriptFullName)
starterScript = agentFolder & "\INICIAR_AGENTE.bat"
logsFolder = agentFolder & "\logs"
logFile = logsFolder & "\agent-discreto.log"

If Not filesystem.FolderExists(logsFolder) Then
  filesystem.CreateFolder(logsFolder)
End If

If IsAgentRunning() Then
  shell.Popup "O agente discreto ja esta rodando nesta maquina.", 4, "Agente de Monitoramento", 64
  WScript.Quit
End If

command = "cmd.exe /c """"" & starterScript & """"""

shell.Run command, 0, False
WScript.Sleep 5000

If IsAgentRunning() Then
  shell.Popup "Agente discreto iniciado. A interface local ja pode ser aberta.", 5, "Agente de Monitoramento", 64
Else
  shell.Popup "O agente nao iniciou na porta 8765. Verifique o arquivo logs\agent-discreto.log dentro da pasta do agente.", 9, "Agente de Monitoramento", 48
End If
