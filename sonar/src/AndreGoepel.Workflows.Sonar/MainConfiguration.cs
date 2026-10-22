using System.Text.RegularExpressions;
using AndreGoepel.Core;

namespace AndreGoepel.Workflows.Sonar;

internal static partial class MainConfiguration
{
    internal static bool IsRepository(string repository) => RepositoryPattern().IsMatch(repository);

    internal static Result Validate(MainPolicy policy)
    {
        if (
            string.IsNullOrWhiteSpace(policy.Version)
            || !IsRepository(policy.Repository)
            || !WorkflowPattern().IsMatch(policy.WorkflowPath)
            || !WorkflowPattern().IsMatch(policy.TriggerWorkflowPath)
            || policy.WorkflowPath == policy.TriggerWorkflowPath
            || !Uri.TryCreate(policy.SonarUrl, UriKind.Absolute, out var url)
            || url.Scheme != "https"
            || url.AbsoluteUri != policy.SonarUrl
            || url.AbsolutePath != "/"
            || url.UserInfo.Length != 0
            || url.Query.Length != 0
            || url.Fragment.Length != 0
            || string.IsNullOrWhiteSpace(policy.ServerId)
            || !RevisionPattern().IsMatch(policy.ToolRevision)
            || !ProjectPattern().IsMatch(policy.Project)
            || policy.MaximumRunAgeMinutes is < 1 or > 1440
            || policy.EvidenceLifetimeMinutes is < 1 or > 60
            || policy.Workflows.Length == 0
            || policy.Workflows.Select(w => w.Path).Distinct(StringComparer.Ordinal).Count()
                != policy.Workflows.Length
            || policy.Workflows.Select(w => w.Id).Distinct().Count() != policy.Workflows.Length
            || !policy.Workflows.Any(w => w.Path == policy.TriggerWorkflowPath)
            || policy.Conditions.Length != 6
            || policy.Conditions.Select(c => c.Metric).Distinct(StringComparer.Ordinal).Count() != 6
            || policy.Conditions.Any(c => c.Op is not ("GT" or "LT"))
            || policy.Profiles.Length == 0
            || policy.Profiles.Select(p => p.Language).Distinct(StringComparer.Ordinal).Count()
                != policy.Profiles.Length
            || (
                policy.BaselineAnalysisId is not null
                && !Guid.TryParse(policy.BaselineAnalysisId, out _)
            )
        )
            return Result.Fail("Incomplete or invalid reviewed main policy.");

        foreach (var workflow in policy.Workflows)
        {
            if (
                workflow.Id <= 0
                || !WorkflowPattern().IsMatch(workflow.Path)
                || !DigestPattern().IsMatch(workflow.FileSha256)
                || workflow.Jobs.Count == 0
                || workflow.Jobs.Any(j =>
                    string.IsNullOrWhiteSpace(j.Key)
                    || j.Value.Length == 0
                    || j.Value.Any(string.IsNullOrWhiteSpace)
                )
                || (
                    string.IsNullOrEmpty(workflow.SharedPath)
                    != string.IsNullOrEmpty(workflow.SharedSha)
                )
                || (
                    !string.IsNullOrEmpty(workflow.SharedSha)
                    && !RevisionPattern().IsMatch(workflow.SharedSha)
                )
            )
                return Result.Fail("Incomplete or invalid mandatory workflow policy.");
        }

        return Result.Ok();
    }

    [GeneratedRegex(
        "^[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9][A-Za-z0-9._-]*$",
        RegexOptions.CultureInvariant
    )]
    private static partial Regex RepositoryPattern();

    [GeneratedRegex("^\\.github/workflows/[A-Za-z0-9_-]+\\.ya?ml$", RegexOptions.CultureInvariant)]
    private static partial Regex WorkflowPattern();

    [GeneratedRegex("^[a-z0-9]+(?:-[a-z0-9]+)*$", RegexOptions.CultureInvariant)]
    private static partial Regex ProjectPattern();

    [GeneratedRegex("^[a-f0-9]{64}$", RegexOptions.CultureInvariant)]
    private static partial Regex DigestPattern();

    [GeneratedRegex("^[a-f0-9]{40}$", RegexOptions.CultureInvariant)]
    private static partial Regex RevisionPattern();
}
