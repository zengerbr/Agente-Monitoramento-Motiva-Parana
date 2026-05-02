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
agentScript = agentFolder & "\agent.py"
portablePythonw = agentFolder & "\python-portatil\pythonw.exe"
portablePython = agentFolder & "\python-portatil\python.exe"

If IsAgentRunning() Then
  shell.Popup "O agente discreto ja esta rodando nesta maquina.", 4, "Agente de Monitoramento", 64
  WScript.Quit
End If

If filesystem.FileExists(portablePythonw) Then
  command = """" & portablePythonw & """ """ & agentScript & """ run"
ElseIf filesystem.FileExists(portablePython) Then
  command = """" & portablePython & """ """ & agentScript & """ run"
Else
  command = "cmd.exe /c ""cd /d """"" & agentFolder & """"" && (python """"" & agentScript & """"" run || py -3 """"" & agentScript & """"" run)"""
End If

shell.Run command, 0, False
WScript.Sleep 1800

If IsAgentRunning() Then
  shell.Popup "Agente discreto iniciado. A interface local ja pode ser aberta.", 5, "Agente de Monitoramento", 64
Else
  shell.Popup "O acionamento foi enviado, mas o agente ainda nao respondeu na porta 8765. Aguarde alguns segundos e tente abrir a interface.", 7, "Agente de Monitoramento", 48
End If
