using System.Text.Json;
using System.Text.RegularExpressions;
using AndreGoepel.Core;

namespace AndreGoepel.Workflows.Sonar;

internal sealed partial record NuGetTarget(
    string Repository,
    long ReadinessWorkflowId,
    string[] Packages
)
{
    internal const string ConfigurationPath = "scripts/sonar/publication-target.json";

    public string VerifierRevision { get; init; } = "";

    internal Result Validate() =>
        Repository is not null
        && RepositoryPattern().IsMatch(Repository)
        && ReadinessWorkflowId is > 0 and < 9007199254740992
        && VerifierRevision is { Length: 40 }
        && VerifierRevision.Any(c => c != '0')
        && VerifierRevision.All(c => c is >= '0' and <= '9' or >= 'a' and <= 'f')
        && Packages is { Length: > 0 and <= 50 }
        && Packages.All(id => id is { Length: <= 100 } && PackagePattern().IsMatch(id))
        && Packages.Distinct(StringComparer.OrdinalIgnoreCase).Count() == Packages.Length
            ? Result.Ok()
            : Result.Fail(
                "A reviewed repository, readiness workflow and package inventory are required."
            );

    internal static Result<NuGetTarget> Read(string root)
    {
        var file = new FileInfo(Path.Combine(root, ConfigurationPath));
        if (
            !file.Exists
            || file.Length > 65536
            || (file.Attributes & FileAttributes.ReparsePoint) != 0
        )
            return Result.Fail<NuGetTarget>("A reviewed publication target is required.");

        var target = JsonSerializer.Deserialize<NuGetTarget>(
            File.ReadAllText(file.FullName),
            JsonFormat.Options
        );
        return target is not null && target.Validate().IsSuccess
            ? Result.Ok(target)
            : Result.Fail<NuGetTarget>("Invalid publication target.");
    }

    [GeneratedRegex(
        "^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9][A-Za-z0-9_.-]{0,99}\\z",
        RegexOptions.CultureInvariant
    )]
    private static partial Regex RepositoryPattern();

    [GeneratedRegex("^[A-Za-z][A-Za-z0-9]*(?:[.][A-Za-z0-9]+)*\\z", RegexOptions.CultureInvariant)]
    private static partial Regex PackagePattern();
}
