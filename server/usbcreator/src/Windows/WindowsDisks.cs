using System.ComponentModel;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Text.Json;
using Microsoft.Win32.SafeHandles;
using Netdispatch.UsbCreator.Core;

namespace Netdispatch.UsbCreator.Windows;

public sealed record DiskVolume(string? Label, string? Path, string? DriveLetter);

public sealed record UsbDisk(int Number, string Name, long Size, string Bus, string Style, List<DiskVolume> Volumes)
{
    public DiskVolume? Volume(string label) =>
        Volumes.FirstOrDefault(v => string.Equals(v.Label, label, StringComparison.OrdinalIgnoreCase));

    /// <summary>The stick's partitions, when this disk is a netdispatch stick.</summary>
    public StickRoots? Roots =>
        Volume(StickRoots.BootLabel)?.Path is { } boot && Volume(StickRoots.DataLabel)?.Path is { } data
            ? new StickRoots(boot, data)
            : null;

    public string Display =>
        $"Disk {Number}: {Name} ({Size / 1_000_000_000.0:0.#} GB{(Bus == "USB" ? "" : ", " + Bus)})";
}

/// <summary>USB disks through Windows' Storage module (PowerShell), and the two raw
/// writes a stick needs: the boot template into the boot partition and SYSLINUX's MBR
/// boot code. Needs an elevated process.</summary>
public static class WindowsDisks
{
    /// <summary>USB disks, plus virtual disks (VHD/VHDX) when <paramref name="allowVirtual"/>
    /// (for testing). System and boot disks never.</summary>
    public static async Task<List<UsbDisk>> ListAsync(bool allowVirtual, CancellationToken ct = default)
    {
        var script = $$"""
            $virtual = ${{(allowVirtual ? "true" : "false")}}
            $out = foreach ($d in Get-Disk) {
              if ($d.IsSystem -or $d.IsBoot) { continue }
              if (-not ($d.BusType -eq 'USB' -or ($virtual -and $d.BusType -eq 'File Backed Virtual'))) { continue }
              $vols = @(Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue |
                ForEach-Object {
                  $v = $_ | Get-Volume -ErrorAction SilentlyContinue
                  $p = @($_.AccessPaths | Where-Object { $_ -like '\\?\Volume*' })[0]
                  [pscustomobject]@{ label = $v.FileSystemLabel; path = $p; drive_letter = [string]$_.DriveLetter }
                })
              [pscustomobject]@{ number = $d.Number; name = $d.FriendlyName; size = $d.Size; bus = [string]$d.BusType;
                                 style = [string]$d.PartitionStyle; volumes = $vols }
            }
            ConvertTo-Json -Depth 4 -Compress -InputObject @($out)
            """;
        return ParseJson<List<UsbDisk>>(await PowerShell.RunAsync(script, ct).ConfigureAwait(false), "disk list");
    }

    /// <summary>Erase the disk and lay out a stick: MBR, NDDATA (exFAT) filling the disk,
    /// then the active boot partition at the end, written from the hub's template.</summary>
    /// <param name="template">Gets the boot partition's head for its size (MiB) and first sector.</param>
    public static async Task<StickRoots> PrepareAsync(
        int disk, int bootMegabytes, byte[] mbrBootCode, Func<int, long, Task<byte[]>> template, IProgress<string>? log, CancellationToken ct)
    {
        log?.Report($"Erasing disk {disk} and creating the partitions");
        var script = $$"""
            $n = {{disk}}
            $d = Get-Disk -Number $n
            if ($d.IsSystem -or $d.IsBoot) { throw 'refusing to erase the system disk' }
            if ($d.IsOffline) { Set-Disk -Number $n -IsOffline $false }
            if ($d.IsReadOnly) { Set-Disk -Number $n -IsReadOnly $false }
            if ($d.PartitionStyle -ne 'RAW') { Clear-Disk -Number $n -RemoveData -RemoveOEM -Confirm:$false }
            Initialize-Disk -Number $n -PartitionStyle MBR
            $bootBytes = {{bootMegabytes}}MB
            $dataSize = [math]::Floor(($d.Size - $bootBytes - 4MB) / 1MB) * 1MB
            if ($dataSize -lt 64MB) { throw 'the disk is too small' }
            $data = New-Partition -DiskNumber $n -Size $dataSize -MbrType IFS
            $boot = New-Partition -DiskNumber $n -UseMaximumSize -MbrType FAT32 -IsActive
            Format-Volume -Partition $data -FileSystem exFAT -NewFileSystemLabel NDDATA -Confirm:$false | Out-Null
            $boot = Get-Partition -DiskNumber $n -PartitionNumber $boot.PartitionNumber
            $data = Get-Partition -DiskNumber $n -PartitionNumber $data.PartitionNumber
            ConvertTo-Json -Compress -InputObject ([pscustomobject]@{
              boot_offset = $boot.Offset; boot_size = $boot.Size
              boot_path = @($boot.AccessPaths | Where-Object { $_ -like '\\?\Volume*' })[0]
              data_path = @($data.AccessPaths | Where-Object { $_ -like '\\?\Volume*' })[0]
            })
            """;
        var layout = ParseJson<PreparedLayout>(await PowerShell.RunAsync(script, ct).ConfigureAwait(false), "partition layout");
        var sizeMb = (int)(layout.BootSize / StickSizing.MiB);
        log?.Report($"Writing the boot partition ({sizeMb} MiB)");
        var head = await template(sizeMb, layout.BootOffset / 512).ConfigureAwait(false);
        WriteVolumeHead(layout.BootPath, head);
        log?.Report("Writing the BIOS boot code");
        WriteMbrBootCode(disk, mbrBootCode);
        await WaitForLabelAsync(disk, StickRoots.BootLabel, ct).ConfigureAwait(false);
        return new StickRoots(layout.BootPath, layout.DataPath);
    }

    /// <summary>Rewrite an existing stick's boot partition from a new template (the hub's
    /// boot files changed). Its image files are copied again afterwards.</summary>
    public static async Task RewriteBootAsync(int disk, Func<int, long, Task<byte[]>> template, IProgress<string>? log, CancellationToken ct)
    {
        var script = $$"""
            $p = Get-Partition -DiskNumber {{disk}} | Where-Object { ($_ | Get-Volume -ErrorAction SilentlyContinue).FileSystemLabel -eq 'NDBOOT' } | Select-Object -First 1
            if (-not $p) { throw 'this stick has no NDBOOT partition' }
            ConvertTo-Json -Compress -InputObject ([pscustomobject]@{
              boot_offset = $p.Offset; boot_size = $p.Size
              boot_path = @($p.AccessPaths | Where-Object { $_ -like '\\?\Volume*' })[0]; data_path = '' })
            """;
        var layout = ParseJson<PreparedLayout>(await PowerShell.RunAsync(script, ct).ConfigureAwait(false), "boot partition");
        var sizeMb = (int)(layout.BootSize / StickSizing.MiB);
        log?.Report($"Rewriting the boot partition ({sizeMb} MiB)");
        WriteVolumeHead(layout.BootPath, await template(sizeMb, layout.BootOffset / 512).ConfigureAwait(false));
        await WaitForLabelAsync(disk, StickRoots.BootLabel, ct).ConfigureAwait(false);
    }

    private static T ParseJson<T>(string json, string what)
    {
        try
        {
            return JsonSerializer.Deserialize<T>(json, HubJson.Options) ?? throw new IOException($"PowerShell returned no {what}");
        }
        catch (JsonException e)
        {
            throw new IOException($"PowerShell returned an unreadable {what}: {e.Message}", e);
        }
    }

    private sealed record PreparedLayout(long BootOffset, long BootSize, string BootPath, string DataPath);

    private static async Task WaitForLabelAsync(int disk, string label, CancellationToken ct)
    {
        for (var i = 0; i < 30; i++)
        {
            var disks = await ListAsync(allowVirtual: true, ct).ConfigureAwait(false);
            if (disks.FirstOrDefault(d => d.Number == disk)?.Volume(label) is not null)
            {
                return;
            }
            await Task.Delay(1000, ct).ConfigureAwait(false);
        }
        throw new IOException($"Windows did not mount the {label} partition");
    }

    // ------------------------------------------------------------------ raw writes

    private const uint FsctlLockVolume = 0x00090018;
    private const uint FsctlDismountVolume = 0x00090020;
    private const uint FsctlUnlockVolume = 0x0009001C;
    private const int Align = 4096;

    /// <summary>Write <paramref name="head"/> at the start of a partition. Windows refuses raw
    /// writes to a mounted volume, so the volume is locked and dismounted first; it mounts
    /// again, as FAT32, once the handle is closed.</summary>
    private static void WriteVolumeHead(string volumePath, byte[] head)
    {
        // \\?\Volume{guid}\ -> \\.\Volume{guid} (the volume device, not its root directory).
        var device = @"\\.\" + volumePath.TrimEnd('\\')[4..];
        using var handle = File.OpenHandle(device, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite);
        Ioctl(handle, FsctlLockVolume, "lock the boot partition");
        Ioctl(handle, FsctlDismountVolume, "dismount the boot partition");
        // Sector-aligned writes only; the padding lands in clusters the FAT marks free.
        var padded = new byte[(head.Length + Align - 1) / Align * Align];
        head.CopyTo(padded, 0);
        RandomAccess.Write(handle, padded, 0);
        RandomAccess.FlushToDisk(handle);
        Ioctl(handle, FsctlUnlockVolume, "unlock the boot partition");
    }

    /// <summary>Replace bytes 0-439 of the disk's first sector (the boot code), keeping the
    /// disk signature and partition table.</summary>
    private static void WriteMbrBootCode(int disk, byte[] code)
    {
        using var handle = File.OpenHandle($@"\\.\PhysicalDrive{disk}", FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite);
        var sector = new byte[Align];
        if (RandomAccess.Read(handle, sector, 0) < 512)
        {
            throw new IOException("could not read the disk's first sector");
        }
        code.AsSpan(0, 440).CopyTo(sector);
        RandomAccess.Write(handle, sector, 0);
        RandomAccess.FlushToDisk(handle);
    }

    private static void Ioctl(SafeFileHandle handle, uint code, string what)
    {
        if (!DeviceIoControl(handle, code, IntPtr.Zero, 0, IntPtr.Zero, 0, out _, IntPtr.Zero))
        {
            throw new IOException($"could not {what}: {new Win32Exception(Marshal.GetLastWin32Error()).Message}");
        }
    }

    [DllImport("kernel32.dll", SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static extern bool DeviceIoControl(
        SafeFileHandle device, uint code, IntPtr inBuffer, uint inSize, IntPtr outBuffer, uint outSize, out uint returned, IntPtr overlapped);
}

internal static class PowerShell
{
    /// <summary>Run a script in Windows PowerShell and return its standard output. The script
    /// goes in as -EncodedCommand: with -Command -, PowerShell runs standard input line by line
    /// and multi-line blocks never run.</summary>
    public static async Task<string> RunAsync(string script, CancellationToken ct)
    {
        var full = "$ErrorActionPreference = 'Stop'\n$ProgressPreference = 'SilentlyContinue'\n" + script;
        var encoded = Convert.ToBase64String(System.Text.Encoding.Unicode.GetBytes(full));
        var psi = new ProcessStartInfo("powershell.exe", "-NoProfile -NonInteractive -ExecutionPolicy Bypass -EncodedCommand " + encoded)
        {
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
        };
        using var proc = Process.Start(psi) ?? throw new IOException("could not start PowerShell");
        var stdout = proc.StandardOutput.ReadToEndAsync(ct);
        var stderr = proc.StandardError.ReadToEndAsync(ct);
        await proc.WaitForExitAsync(ct).ConfigureAwait(false);
        var output = (await stdout.ConfigureAwait(false)).Trim();
        // Only the exit code counts: PowerShell also writes progress records (#< CLIXML)
        // to standard error, for example while it loads the Storage module.
        if (proc.ExitCode != 0)
        {
            throw new IOException("PowerShell: " + ErrorText(await stderr.ConfigureAwait(false), proc.ExitCode));
        }
        return output;
    }

    /// <summary>The messages in PowerShell's standard error, without CLIXML markup.</summary>
    private static string ErrorText(string stderr, int exitCode)
    {
        if (stderr.StartsWith("#< CLIXML", StringComparison.Ordinal))
        {
            var errors = System.Text.RegularExpressions.Regex.Matches(stderr, "<S S=\"Error\">(.*?)</S>")
                .Select(m => System.Net.WebUtility.HtmlDecode(m.Groups[1].Value).Replace("_x000D__x000A_", " ", StringComparison.Ordinal).Trim());
            stderr = string.Join(" ", errors);
        }
        stderr = stderr.Trim();
        return stderr.Length > 0 ? stderr : $"exit code {exitCode}";
    }
}
