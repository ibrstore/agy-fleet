' Antigravity Fleet Hidden Watchdog Launcher
' Launches watchdog.bat in 100% hidden background mode (no taskbar or console window)
Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
ScriptDir = fso.GetParentFolderName(WScript.ScriptFullName)
RepoDir = fso.GetParentFolderName(ScriptDir)
WshShell.CurrentDirectory = RepoDir
WshShell.Run """" & ScriptDir & "\watchdog.bat""", 0, False
Set WshShell = Nothing
