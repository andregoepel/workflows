using AndreGoepel.Workflows.Sonar;
using Xunit;

namespace AndreGoepel.Workflows.Sonar.Tests;

public sealed class NuGetReleaseTests
{
    [Theory]
    [InlineData(true)]
    [InlineData(false)]
    public void Parse_PreparationMode_RequiresExactVersionTag(bool includeTag)
    {
        // Arrange
        using var fixture = new ReleaseFixture();
        string[] args =
        [
            "--root",
            fixture.Root,
            "--nuget-prepare",
            "--sha",
            MainFixture.Sha,
            "--version",
            "2.2.0",
            "--readiness-run",
            "100",
        ];
        if (includeTag)
            args = [.. args, "--tag", "v2.2.0"];

        // Act
        var result = NuGetRelease.Parse(args);

        // Assert
        Assert.Equal(includeTag, result.IsSuccess);
    }

    [Theory]
    [InlineData("2.2.0", true)]
    [InlineData("2.2.0-beta.1", false)]
    [InlineData("02.2.0", false)]
    [InlineData("2.2", false)]
    [InlineData("2.2.0;dotnet", false)]
    [InlineData("2.2.0+build", false)]
    [InlineData("2.2.0\n", false)]
    public void Parse_ReleaseVersion_RestrictsInitialPolicy(string version, bool valid)
    {
        // Arrange
        using var fixture = new ReleaseFixture();
        string[] args =
        [
            "--root",
            fixture.Root,
            "--nuget-dry-run",
            "--sha",
            MainFixture.Sha,
            "--version",
            version,
            "--readiness-run",
            "100",
        ];

        // Act
        var result = NuGetRelease.Parse(args);

        // Assert
        Assert.Equal(valid, result.IsSuccess);
    }

    [Theory]
    [InlineData("--publish", "true")]
    [InlineData("--source", "https://other-feed.example")]
    [InlineData("--tag", "v1.0.0")]
    [InlineData("--allow-stale", "true")]
    public void Parse_UnknownOrInconsistentOption_Blocks(string flag, string value)
    {
        // Arrange
        using var fixture = new ReleaseFixture();
        string[] args =
        [
            "--root",
            fixture.Root,
            "--nuget-dry-run",
            "--sha",
            MainFixture.Sha,
            "--version",
            "2.2.0",
            "--readiness-run",
            "100",
            flag,
            value,
        ];

        // Act
        var result = NuGetRelease.Parse(args);

        // Assert
        Assert.True(result.IsFailure);
    }

    [Theory]
    [InlineData("ACTIONS_ID_TOKEN_REQUEST_TOKEN")]
    [InlineData("ACTIONS_ID_TOKEN_REQUEST_URL")]
    [InlineData("NUGET_API_KEY")]
    [InlineData("NUGET_AUTH_TOKEN")]
    [InlineData("NUGET_SYMBOL_API_KEY")]
    public void CheckCredentialBoundary_PublicationAccessAlreadyPresent_Blocks(string variable)
    {
        // Act
        var result = NuGetRelease.CheckCredentialBoundary(name =>
            name == variable ? "secret" : null
        );
        var child = ProcessRunner.CreateStartInfo(Path.GetTempPath(), ["dotnet", "pack"]);

        // Assert
        Assert.True(result.IsFailure);
        Assert.DoesNotContain("secret", result.Error);
        Assert.False(child.Environment.ContainsKey(variable));
    }

    [Fact]
    public void CheckCredentialBoundary_OnlyReadCredentials_PermitsPreparation()
    {
        // Act
        var result = NuGetRelease.CheckCredentialBoundary(name =>
            name == "WORKFLOWS_GITHUB_TOKEN" ? "read-only" : null
        );

        // Assert
        Assert.True(result.IsSuccess, result.Error);
    }
}
