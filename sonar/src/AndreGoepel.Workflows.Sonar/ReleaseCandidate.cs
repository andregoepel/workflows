namespace AndreGoepel.Workflows.Sonar;

internal record ReleaseCandidate(string Root, string Sha, long ReadinessRunId, string? Tag = null)
{
    public NuGetTarget Target { get; init; } = new("", 0, []);
}
