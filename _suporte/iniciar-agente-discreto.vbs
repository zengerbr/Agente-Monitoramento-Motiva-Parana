Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

agentFolder = filesystem.GetParentFolderName(WScript.ScriptFullName)
agentScript = agentFolder & "\agent.py"
portablePythonw = agentFolder & "\python-portatil\pythonw.exe"
portablePython = agentFolder & "\python-portatil\python.exe"

If filesystem.FileExists(portablePythonw) Then
  command = """" & portablePythonw & """ """ & agentScript & """ run"
ElseIf filesystem.FileExists(portablePython) Then
  command = """" & portablePython & """ """ & agentScript & """ run"
Else
  command = "cmd.exe /c ""cd /d """"" & agentFolder & """"" && (python """"" & agentScript & """"" run || py -3 """"" & agentScript & """"" run)"""
End If

shell.Run command, 0, False
