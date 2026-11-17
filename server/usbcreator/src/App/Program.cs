using System.Runtime.InteropServices;
using Avalonia;
using Avalonia.Controls.ApplicationLifetimes;
using Avalonia.Themes.Fluent;

namespace Netdispatch.UsbCreator.App;

internal static class Program
{
    [STAThread]
    public static int Main(string[] args)
    {
        if (args.Length > 0 && args[0] == "--cli")
        {
            // A Windows GUI program has no console of its own: borrow the one it was started from.
            AttachConsole(-1);
            return Cli.RunAsync(args[1..]).GetAwaiter().GetResult();
        }
        return BuildAvaloniaApp().StartWithClassicDesktopLifetime(args);
    }

    public static AppBuilder BuildAvaloniaApp() =>
        AppBuilder.Configure<CreatorApp>().UsePlatformDetect().LogToTrace();

    [DllImport("kernel32.dll")]
    private static extern bool AttachConsole(int processId);
}

internal sealed class CreatorApp : Application
{
    public override void Initialize() => Styles.Add(new FluentTheme());

    public override void OnFrameworkInitializationCompleted()
    {
        if (ApplicationLifetime is IClassicDesktopStyleApplicationLifetime desktop)
        {
            desktop.MainWindow = new MainWindow(desktop.Args ?? []);
        }
        base.OnFrameworkInitializationCompleted();
    }
}
