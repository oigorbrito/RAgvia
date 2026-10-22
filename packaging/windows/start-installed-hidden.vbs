Option Explicit

Dim shell, fso, baseDir, psScript
Dim configFile, configDir, projectDir
Dim stream, line, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

baseDir = fso.GetParentFolderName(WScript.ScriptFullName)
psScript = fso.BuildPath(baseDir, "start-installed.ps1")

configDir = shell.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\RagVDesktop"
configFile = fso.BuildPath(configDir, "config.ini")

If Not fso.FileExists(configFile) Then
    MsgBox "Configuracao nao encontrada: " & configFile, vbCritical, "RagV Desktop"
    WScript.Quit 1
End If

projectDir = ""

Set stream = fso.OpenTextFile(configFile, 1)

Do Until stream.AtEndOfStream
    line = Trim(stream.ReadLine)

    If LCase(Left(line, 12)) = "projectpath=" Then
        projectDir = Trim(Mid(line, 13))
        Exit Do
    End If
Loop

stream.Close

If projectDir = "" Then
    MsgBox "ProjectPath nao configurado.", vbCritical, "RagV Desktop"
    WScript.Quit 1
End If

If Not fso.FileExists(fso.BuildPath(projectDir, "compose.yaml")) Then
    MsgBox "compose.yaml nao encontrado em: " & projectDir, vbCritical, "RagV Desktop"
    WScript.Quit 1
End If

command = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File " & _
          Chr(34) & psScript & Chr(34) & _
          " -ProjectPath " & Chr(34) & projectDir & Chr(34)

shell.Run command, 0, False
