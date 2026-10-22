Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

repo = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))
script = fso.BuildPath(repo, "scripts\start-ragv-desktop.ps1")

If Not fso.FileExists(script) Then
    MsgBox "Inicializador RagV nao encontrado: " & script, 16, "RagV Desktop"
    WScript.Quit 1
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """"

shell.Run command, 0, False
