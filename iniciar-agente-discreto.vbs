Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

agentFolder = filesystem.GetParentFolderName(WScript.ScriptFullName)
command = "cmd.exe /c """ & agentFolder & "\INICIAR_AGENTE.bat"""
shell.Run command, 0, False
