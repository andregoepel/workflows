using System.Text.Json;
using AndreGoepel.Core;

namespace AndreGoepel.Workflows.Sonar;

internal sealed class ReleaseVerificationSession(
    HttpClient client,
    IGitHubClient github,
    IGitHubArtifacts artifacts,
    IReleaseReadiness readiness,
    AnalysisContract contract
) : IDisposable
{
    internal IGitHubClient GitHub { get; } = github;
    internal IGitHubArtifacts Artifacts { get; } = artifacts;
    internal IReleaseReadiness Readiness { get; } = readiness;
    internal AnalysisContract Contract { get; } = contract;

    internal static async Task<Result<ReleaseVerificationSession>> OpenAsync(
        NuGetRelease release,
        CancellationToken ct
    )
    {
        var root = release.Root;
        var policy = JsonSerializer.Deserialize<MainPolicy>(
            await File.ReadAllTextAsync(Path.Combine(root, "scripts/sonar/main-policy.json"), ct),
            JsonFormat.Options
        )!;
        var configuration = await MainCommand.ValidateConfigurationAsync(root, policy, ct);
        if (configuration.IsFailure)
            return Result.Fail<ReleaseVerificationSession>(configuration.Error);
        if (release.Target.Validate().IsFailure || release.Target.Repository != policy.Repository)
            return Result.Fail<ReleaseVerificationSession>(
                "The publication target differs from the reviewed main policy."
            );
        if (
            Environment.GetEnvironmentVariable("WORKFLOWS_SONAR_TOOL_SHA")
            != release.Target.VerifierRevision
        )
            return Result.Fail<ReleaseVerificationSession>(
                "The release verifier differs from the reviewed shared revision."
            );
        var contract = JsonSerializer.Deserialize<AnalysisContract>(
            await File.ReadAllTextAsync(Path.Combine(root, "scripts/sonar/contract.json"), ct),
            JsonFormat.Options
        )!;

        var githubToken = TakeCredential("WORKFLOWS_GITHUB_TOKEN");
        var scanToken = TakeCredential("WORKFLOWS_SONAR_TOKEN");
        var verifyToken = TakeCredential("WORKFLOWS_SONAR_VERIFY_TOKEN");
        if (githubToken.Length == 0 || scanToken.Length == 0 || verifyToken.Length == 0)
            return Result.Fail<ReleaseVerificationSession>(
                "Read-only GitHub and both existing Sonar verification credentials are required."
            );

        var client = SonarClient.CreateHttpClient();
        var github = new GitHubClient(policy.Repository, githubToken);
        var artifacts = new GitHubArtifacts(client, githubToken, policy.Repository);
        var readiness = new ReleaseReadiness(
            github,
            artifacts,
            new SonarClient(client, new Uri(policy.SonarUrl)),
            new ProcessRunner(),
            TimeProvider.System,
            policy,
            contract,
            scanToken,
            verifyToken
        );
        return Result.Ok(
            new ReleaseVerificationSession(client, github, artifacts, readiness, contract)
        );
    }

    public void Dispose()
    {
        (GitHub as IDisposable)?.Dispose();
        client.Dispose();
    }

    private static string TakeCredential(string name)
    {
        var value = Environment.GetEnvironmentVariable(name) ?? "";
        Environment.SetEnvironmentVariable(name, null);
        return value;
    }
}
