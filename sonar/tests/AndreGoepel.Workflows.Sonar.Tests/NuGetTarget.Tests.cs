using System.Text.Json;
using AndreGoepel.Workflows.Sonar;
using Xunit;

namespace AndreGoepel.Workflows.Sonar.Tests;

public sealed class NuGetTargetTests
{
    [Theory]
    [InlineData("repository")]
    [InlineData("workflow")]
    [InlineData("revision")]
    [InlineData("placeholder")]
    [InlineData("empty")]
    [InlineData("traversal")]
    [InlineData("duplicate")]
    [InlineData("case-duplicate")]
    public void Validate_InvalidTarget_Rejects(string fault)
    {
        // Arrange
        var target = ReleaseFixture.Target;
        target = fault switch
        {
            "repository" => target with { Repository = "../other" },
            "workflow" => target with { ReadinessWorkflowId = 0 },
            "revision" => target with { VerifierRevision = "main" },
            "placeholder" => target with { VerifierRevision = new string('0', 40) },
            "empty" => target with { Packages = [] },
            "traversal" => target with { Packages = ["../Other"] },
            "duplicate" => target with { Packages = ["Example.Library", "Example.Library"] },
            _ => target with { Packages = ["Example.Library", "example.library"] },
        };

        // Act
        var result = target.Validate();

        // Assert
        Assert.True(result.IsFailure);
    }

    [Fact]
    public async Task Inspect_IndependentSinglePackageTarget_AcceptsOnlyThatInventory()
    {
        // Arrange
        using var fixture = new ReleaseFixture();
        await fixture.InitializeAsync(TestContext.Current.CancellationToken);
        var release = fixture.Release with
        {
            Target = ReleaseFixture.Target with { Packages = ["Example.Library.Core"] },
        };
        var directory = Path.Combine(fixture.Root, "artifacts/packages");
        NuGetPackagesTests.CreatePackages(release, directory);

        // Act
        var valid = await NuGetPackages.InspectAsync(
            release,
            directory,
            TestContext.Current.CancellationToken
        );
        var substituted = await NuGetPackages.InspectAsync(
            fixture.Release,
            directory,
            TestContext.Current.CancellationToken
        );

        // Assert
        Assert.True(valid.IsSuccess, valid.Error);
        Assert.Equal(2, valid.Value!.Length);
        Assert.True(substituted.IsFailure);
    }

    [Fact]
    public async Task Verify_TargetFromAnotherRepository_RejectsBeforeNetwork()
    {
        // Arrange
        using var fixture = new ReleaseFixture();
        await fixture.InitializeAsync(TestContext.Current.CancellationToken);
        fixture.Main.Requests.Clear();
        var release = fixture.Release with
        {
            Target = ReleaseFixture.Target with { Repository = "example/other" },
        };

        // Act
        var result = await fixture
            .Verifier()
            .VerifyAsync(release, TestContext.Current.CancellationToken);

        // Assert
        Assert.True(result.IsFailure);
        Assert.Empty(fixture.Main.Requests);
    }

    [Fact]
    public void Read_SeparateConsumers_DoNotShareMutableConfiguration()
    {
        // Arrange
        using var first = new ReleaseFixture();
        using var second = new ReleaseFixture();
        var other = ReleaseFixture.Target with
        {
            Repository = "example/other",
            Packages = ["Other.Library"],
        };
        File.WriteAllText(
            Path.Combine(second.Root, NuGetTarget.ConfigurationPath),
            JsonSerializer.Serialize(other, JsonFormat.Options)
        );

        // Act
        var firstRead = NuGetTarget.Read(first.Root);
        var secondRead = NuGetTarget.Read(second.Root);

        // Assert
        Assert.Equal(MainFixture.Repository, firstRead.Value!.Repository);
        Assert.Equal("example/other", secondRead.Value!.Repository);
        Assert.Equal(["Other.Library"], secondRead.Value.Packages);
        Assert.Equal(ReleaseFixture.Packages, firstRead.Value.Packages);
    }
}
