using System.Globalization;
using System.Text.Json;
using AndreGoepel.Core;

namespace AndreGoepel.Workflows.Sonar;

internal static class MainEnvironment
{
    internal static Result<MainRunIdentity> Validate(
        string root,
        Func<string, string?> environment,
        JsonElement payload,
        MainPolicy policy
    )
    {
        var sha = environment("GITHUB_SHA") ?? "";
        if (
            environment("GITHUB_ACTIONS") != "true"
            || environment("GITHUB_REPOSITORY") != policy.Repository
            || environment("GITHUB_EVENT_NAME") != "workflow_run"
            || environment("GITHUB_REF") != "refs/heads/main"
            || environment("GITHUB_WORKFLOW_REF")
                != $"{policy.Repository}/{policy.WorkflowPath}@refs/heads/main"
            || environment("GITHUB_WORKFLOW_SHA") != sha
            || sha.Length != 40
            || !sha.All(char.IsAsciiHexDigit)
            || !Path.IsPathFullyQualified(root)
            || !string.Equals(
                Path.GetFullPath(root),
                environment("GITHUB_WORKSPACE"),
                StringComparison.Ordinal
            )
            || !long.TryParse(
                environment("GITHUB_RUN_ID"),
                CultureInfo.InvariantCulture,
                out var runId
            )
            || runId <= 0
            || !int.TryParse(
                environment("GITHUB_RUN_ATTEMPT"),
                CultureInfo.InvariantCulture,
                out var attempt
            )
            || attempt <= 0
        )
            return Result.Fail<MainRunIdentity>(
                "Central analysis requires the trusted main workflow context."
            );

        var trigger = payload.GetProperty("workflow_run");
        if (
            trigger.GetProperty("id").GetInt64() <= 0
            || trigger.GetProperty("run_attempt").GetInt32() <= 0
            || trigger.GetProperty("event").GetString() != "push"
            || trigger.GetProperty("head_branch").GetString() != "main"
            || trigger.GetProperty("head_sha").GetString() != sha
            || trigger.GetProperty("path").GetString() != policy.TriggerWorkflowPath
            || trigger.GetProperty("conclusion").GetString() != "success"
            || trigger.GetProperty("repository").GetProperty("full_name").GetString()
                != policy.Repository
            || trigger.GetProperty("head_repository").GetProperty("full_name").GetString()
                != policy.Repository
        )
            return Result.Fail<MainRunIdentity>(
                "The triggering CI run is not a successful main push for this revision."
            );

        return Result.Ok(
            new MainRunIdentity(
                sha,
                runId,
                attempt,
                trigger.GetProperty("id").GetInt64(),
                trigger.GetProperty("run_attempt").GetInt32()
            )
            {
                Repository = policy.Repository,
            }
        );
    }
}
