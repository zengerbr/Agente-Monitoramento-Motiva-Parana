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

If Not IsAgentRunning() Then
  shell.Run "cmd.exe /c """"" & starterScript & """""", 0, False
  WScript.Sleep 5000
End If

If IsAgentRunning() Then
  shell.Run "http://127.0.0.1:8765/", 1, False
Else
  shell.Popup "Nao foi possivel iniciar o agente. Verifique o arquivo logs\agent-discreto.log.", 9, "Agente de Monitoramento", 48
End If
