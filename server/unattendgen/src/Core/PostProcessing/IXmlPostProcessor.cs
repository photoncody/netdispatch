using System.Xml;

namespace Netdispatch.UnattendGen.PostProcessing;

/// <summary>
/// Changes upstream's generated XML in place. This is how UnattendHost adds behavior that
/// upstream does not have (for example Windows Server support) without editing upstream.
/// Each post-processor should do one small thing and have its own unit test.
/// </summary>
public interface IXmlPostProcessor
{
    bool AppliesTo(HostConfig config);

    void Apply(XmlDocument doc, HostConfig config);
}

public static class PostProcessorChain
{
    /// <summary>
    /// Every post-processor, in the order they run. This list is the only place that defines
    /// the order. Phase 1 has none; Windows Server support (phase 2) adds them here.
    /// </summary>
    public static IReadOnlyList<IXmlPostProcessor> Default { get; } = [];
}
