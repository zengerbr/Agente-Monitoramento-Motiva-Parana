Set shell = CreateObject("WScript.Shell")
Set filesystem = CreateObject("Scripting.FileSystemObject")

agentFolder = filesystem.GetParentFolderName(WScript.ScriptFullName)
shell.CurrentDirectory = agentFolder
shell.Run "python agent.py run", 0, False
