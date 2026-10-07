# Type a message into the open Feishu chat and send it (WSL: call via powershell.exe).
#   powershell.exe -ExecutionPolicy Bypass -File feishu_send.ps1 -Text "你好"
# Sends into whichever chat is open: screenshot first (feishu_shot.ps1) to confirm it is the test chat.
# Refuses unless Feishu really is the foreground window. -X/-Y: the input box in 2000px-wide screenshot coordinates.
param([Parameter(Mandatory = $true)][string]$Text, [int]$X = 1100, [int]$Y = 1043)
Add-Type -AssemblyName System.Windows.Forms
Add-Type @"
using System; using System.Runtime.InteropServices; using System.Text;
public class F {
  [DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
  [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
  [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
  [DllImport("user32.dll")] public static extern bool SetCursorPos(int x, int y);
  [DllImport("user32.dll")] public static extern void mouse_event(uint f, int dx, int dy, int data, int extra);
  [DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, int extra);
  [StructLayout(LayoutKind.Sequential)] public struct RECT { public int L,T,R,B; }
}
"@
[void][F]::SetProcessDPIAware()
$p = Get-Process Feishu | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "Feishu window not found"; exit 1 }
$h = $p.MainWindowHandle
[F]::keybd_event(0x12, 0, 0, 0); [void][F]::SetForegroundWindow($h); [F]::keybd_event(0x12, 0, 2, 0)
Start-Sleep -Milliseconds 500
if ([F]::GetForegroundWindow() -ne $h) { "Feishu is not in front; nothing sent"; exit 2 }
$r = New-Object F+RECT; [void][F]::GetWindowRect($h, [ref]$r)
$scale = 1.63
[void][F]::SetCursorPos([int]($r.L + $X * $scale), [int]($r.T + $Y * $scale)); Start-Sleep -Milliseconds 150
[F]::mouse_event(0x0002, 0, 0, 0, 0); [F]::mouse_event(0x0004, 0, 0, 0, 0); Start-Sleep -Milliseconds 300
Set-Clipboard -Value $Text
[System.Windows.Forms.SendKeys]::SendWait("^v"); Start-Sleep -Milliseconds 400
[System.Windows.Forms.SendKeys]::SendWait("{ENTER}")
"sent"
