using System.Text.Json;
using System.Text.Json.Nodes;
using AndreGoepel.Workflows.Sonar;
using Xunit;

namespace AndreGoepel.Workflows.Sonar.Tests;

public sealed class SharedLibraryTests
{
    [Theory]
    [InlineData(false)]
    [InlineData(true)]
    public async Task StandaloneCi_RequiresOnlyItsReviewedJobs(bool omitReferences)
    {
        // Arrange
        var fixture = Standalone();
        if (omitReferences)
            fixture.Responses["actions/runs/200"].AsObject().Remove("referenced_workflows");

        // Act
        var result = await fixture.GitHub().ReadAsync(TestContext.Current.CancellationToken);

        // Assert
        Assert.True(result.IsSuccess, result.Error);
        Assert.Single(result.Value!);
        Assert.Equal(200, result.Value![0].Id);
    }

    [Theory]
    [InlineData("unexpected-ci-reference")]
    [InlineData("wrong-tool-pin")]
    [InlineData("mutable-tool-reference")]
    [InlineData("wrong-repository")]
    [InlineData("trigger-rerun")]
    public async Task SharedRunnerOrCiDrift_RejectsEvidence(string fault)
    {
        // Arrange
        var fixture = Standalone();
        switch (fault)
        {
            case "unexpected-ci-reference":
                fixture.Responses["actions/runs/200"]["referenced_workflows"] = MainFixture.Node(
                    new[] { new { path = "other", sha = MainFixture.Sha } }
                );
                break;
            case "wrong-tool-pin":
                fixture.Responses["actions/runs/100"]["referenced_workflows"]![0]!["sha"] =
                    new string('a', 40);
                break;
            case "mutable-tool-reference":
                fixture.Responses["actions/runs/100"]["referenced_workflows"]![0]!["path"] =
                    "andregoepel/workflows/.github/workflows/sonar-library.yml@main";
                break;
            case "wrong-repository":
                fixture.Responses["actions/runs/200"]["head_repository"]!["full_name"] =
                    "other/library";
                break;
            case "trigger-rerun":
                fixture.Responses["actions/runs/200"]["run_attempt"] = 2;
                break;
        }

        // Act
        var result = await fixture.GitHub().ReadAsync(TestContext.Current.CancellationToken);

        // Assert
        Assert.True(result.IsFailure);
    }

    [Theory]
    [InlineData("repository")]
    [InlineData("workflow-path")]
    [InlineData("tool-revision")]
    [InlineData("sonar-url")]
    [InlineData("missing-trigger")]
    [InlineData("empty-jobs")]
    public void InvalidConsumerPolicy_RejectsBeforeNetworkOrSourceExecution(string fault)
    {
        // Arrange
        var original = Standalone().Policy;
        var policy = fault switch
        {
            "repository" => original with { Repository = "owner/../escape" },
            "workflow-path" => original with { WorkflowPath = "../outside.yml" },
            "tool-revision" => original with { ToolRevision = "main" },
            "sonar-url" => original with { SonarUrl = "https://token@sonar.example.org/" },
            "missing-trigger" => original with
            {
                TriggerWorkflowPath = ".github/workflows/missing.yml",
            },
            "empty-jobs" => original with
            {
                Workflows = [original.Workflows[0] with { Jobs = [] }],
            },
            _ => original,
        };

        // Act
        var result = MainConfiguration.Validate(policy);

        // Assert
        Assert.True(result.IsFailure);
        Assert.True(MainConfiguration.Validate(original).IsSuccess);
    }

    [Fact]
    public async Task LocalScan_CentralServerIdentity_RejectsEvenThroughLoopback()
    {
        // Arrange
        using var fixture = new RunnerFixture();
        var contract = fixture.Contract with { CentralServerId = fixture.Options.ServerId };

        // Act
        var result = await new AnalysisRunner(fixture, fixture).RunAsync(
            fixture.Options,
            contract,
            "scan",
            "verify",
            TestContext.Current.CancellationToken
        );

        // Assert
        Assert.True(result.IsFailure);
        Assert.Empty(fixture.Commands);
        Assert.False(Directory.Exists(fixture.Output));
    }

    [Fact]
    public void Fingerprint_ChangesWhenConsumerOrToolPinChanges()
    {
        // Arrange
        var policy = Standalone().Policy;

        // Act
        var repository = (policy with { Repository = "another/library" }).Fingerprint();
        var revision = (policy with { ToolRevision = new string('a', 40) }).Fingerprint();
        var roundTrip = JsonSerializer.Deserialize<MainPolicy>(
            JsonSerializer.Serialize(policy, JsonFormat.Options),
            JsonFormat.Options
        )!;

        // Assert
        Assert.NotEqual(policy.Fingerprint(), repository);
        Assert.NotEqual(policy.Fingerprint(), revision);
        Assert.Equal(policy.Fingerprint(), roundTrip.Fingerprint());
    }

    [Fact]
    public void AnalysisReadiness_DoesNotAuthorizePublishing()
    {
        // Arrange
        var fixture = new MainFixture();
        var receipt = new ReadinessReceipt(
            1,
            MainFixture.Repository,
            fixture.Identity,
            MainFixture.WorkflowPath,
            MainFixture.Sha,
            "1",
            "policy",
            "contract",
            "summary",
            "source",
            MainFixture.Baseline,
            "task",
            "analysis",
            [],
            fixture.Now,
            fixture.Now.AddMinutes(1)
        );

        // Act
        var json = JsonSerializer.SerializeToElement(receipt, JsonFormat.Options);

        // Assert
        Assert.True(json.GetProperty("analysisReady").GetBoolean());
        Assert.False(json.GetProperty("releaseEligible").GetBoolean());
        Assert.Equal("library-analysis-readiness", json.GetProperty("kind").GetString());
    }

    private static MainFixture Standalone()
    {
        var fixture = new MainFixture();
        fixture.Policy = fixture.Policy with
        {
            Workflows = [fixture.Policy.Workflows[0] with { SharedPath = "", SharedSha = "" }],
        };
        fixture.Responses["actions/runs/200"]["referenced_workflows"] = new JsonArray();
        return fixture;
    }
}
