Option Explicit

Dim shell, fso, appDir, scriptPath
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

appDir = fso.GetParentFolderName(WScript.ScriptFullName)
scriptPath = fso.BuildPath(appDir, "TTCA.pyw")

shell.CurrentDirectory = appDir
shell.Run "pythonw.exe """ & scriptPath & """", 0, False

Set fso = Nothing
Set shell = Nothing
